"""Cause labeller (CONTRACT §7): Snowflake summarises, Gemini classifies. Runs on bursts only, writes CAUSE_LABELS.

Burst = edits_60m >= 8 and humans_60m >= 3 at decision time t. Two stages, both see ONLY evidence that existed at t
(edit summaries with ts <= t, counts as of t, Main Page status as of t):

  1. Snowflake Cortex  AI_SUMMARIZE_AGG(comment) over the article's human edits in (t-1h, t], computed inside the
                       warehouse for every burst in one query: dozens of terse, multilingual edit summaries become
                       one plain-English sentence about what is happening to the article.
  2. Gemini            classifies each burst into the contract's cause enum (JSON schema, temperature 0), seeing the
                       Cortex summary plus the raw summaries. Up to BATCH bursts per call to stay inside rate limits.

Labels are cached by (wiki, title, hour) so re-runs and the live job never pay twice. Without Snowflake (DuckDB
store, tests) stage 1 is skipped and Gemini sees the raw summaries only.

    flashcrowd-gemini --start 2026-10-17 --end 2026-10-24T09:00 --budget 3000     # history, for training
    (the live forecast job calls label_bursts() itself every run when GEMINI_API_KEY is set)
"""
from __future__ import annotations

import argparse
import json
import os
import time

import pandas as pd

from . import config as C
from .data import HOUR, RawData, load_window
from .features import build, make_grid
from .store import Store, open_store, ts_literal

BATCH = 12                     # bursts per Gemini call
LABEL = {"type": "object", "properties": {
    "cause": {"type": "string", "enum": list(C.CAUSES)},
    "confidence": {"type": "number"}, "reason": {"type": "string"}}, "required": ["cause", "confidence", "reason"]}
SCHEMA = LABEL                 # single-burst schema (kept for the injected-client tests)
BATCH_SCHEMA = {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}, **LABEL["properties"]},
                                           "required": ["id", "cause", "confidence", "reason"]}}

GUIDE = """Causes: breaking_news (a news event happening now), death (the subject died), sports (a match, result, transfer,
tournament), scheduled_event (election, release, launch, holiday, announced in advance), entertainment (film, TV, music,
celebrity), vandalism (bad-faith edits being reverted), edit_war (good-faith editors reverting each other),
bot_maintenance (mass cleanup, file deletions, formatting, categories), other (routine editing, unclear)."""

PROMPT = """You classify why a Wikipedia article is being edited right now.
Article: {title} ({wiki}). Edits in the last hour: {n}, unique human editors: {humans}, reverts: {reverts}.
On the Main Page: {main_page}. Snowflake summary of the edits: {summary}
Recent edit summaries (newest first):
{comments}
""" + GUIDE + "\nReturn the most likely cause and a one-sentence reason a reader would understand. Use only this evidence."

BATCH_PROMPT = """You classify why each of these Wikipedia articles is being edited right now. """ + GUIDE + """
For every article return its id, the most likely cause, a confidence 0-1 and a one-sentence reason a reader would
understand. Use only the evidence given; do not guess from outside knowledge.

{items}"""

ITEM = """### id {id}: {title} ({wiki})
edits last hour {n}, human editors {humans}, reverts {reverts}, on Main Page: {main_page}
Snowflake summary: {summary}
Edit summaries: {comments}
"""

CACHE = C.CACHE_DIR / "gemini_cache.jsonl"
BAD_SUMMARY = ("do not match", "no edit", "cannot", "not provided", "no information")


def _clean(label: dict) -> dict:
    label = dict(label)
    if label.get("cause") not in C.CAUSES:
        label["cause"] = "other"
    label["confidence"] = float(min(max(float(label.get("confidence") or 0), 0), 1))
    label["reason"] = str(label.get("reason", ""))[:500]
    return label


class GeminiLabeler:
    # `client` can be injected (tests use a fake); otherwise the real google-genai client is built from env vars.
    def __init__(self, client=None, model: str | None = None, budget: int = 3000):
        if client is None:
            from google import genai
            client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.client = client
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
        self.budget = budget
        self.calls = 0
        self.cache: dict[str, dict] = {}
        if CACHE.exists():
            for line in CACHE.read_text().splitlines():
                d = json.loads(line)
                self.cache[d["key"]] = d["label"]

    @staticmethod
    def key(wiki, title, t) -> str:
        return f"{wiki}|{title}|{pd.Timestamp(t):%Y-%m-%dT%H}"

    # One call with temperature 0 and a schema; exponential back-off on rate limits (429 / RESOURCE_EXHAUSTED).
    def _call(self, prompt: str, schema: dict):
        delay = 4.0
        for _ in range(7):
            try:
                resp = self.client.models.generate_content(
                    model=self.model, contents=prompt,
                    config={"response_mime_type": "application/json", "response_schema": schema, "temperature": 0})
                return json.loads(resp.text)
            except Exception as e:                                   # noqa: BLE001 - SDK raises several types
                s = str(e)
                if "429" in s or "RESOURCE_EXHAUSTED" in s or "503" in s or "UNAVAILABLE" in s:
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue
                raise
        raise RuntimeError("Gemini rate-limited after 7 retries")

    def _remember(self, key, lab):
        self.cache[key] = lab
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with CACHE.open("a") as fh:
            fh.write(json.dumps({"key": key, "label": lab}, ensure_ascii=False) + "\n")

    def label(self, wiki, title, t, evidence: dict) -> dict | None:
        key = self.key(wiki, title, t)
        if key in self.cache:
            return self.cache[key]
        if self.calls >= self.budget:
            return None
        self.calls += 1
        ev = {"summary": "(none)", **evidence}
        lab = _clean(self._call(PROMPT.format(wiki=wiki, title=title.replace("_", " "), **ev), SCHEMA))
        self._remember(key, lab)
        return lab

    # Many bursts per call. items: [(key, wiki, title, evidence)]; returns {key: label} for the ones labelled.
    def label_many(self, items) -> dict:
        out = {}
        todo = [it for it in items if it[0] not in self.cache]
        for it in items:
            if it[0] in self.cache:
                out[it[0]] = self.cache[it[0]]
        for i in range(0, len(todo), BATCH):
            if self.calls >= self.budget:
                break
            chunk = todo[i:i + BATCH]
            self.calls += 1
            text = "\n".join(ITEM.format(id=j, wiki=w, title=t.replace("_", " "), **{"summary": "(none)", **ev})
                             for j, (_, w, t, ev) in enumerate(chunk))
            try:
                res = self._call(BATCH_PROMPT.format(items=text), BATCH_SCHEMA)
            except Exception as e:                                   # noqa: BLE001 - one bad batch must not stop a run
                print("gemini batch failed:", str(e)[:200])
                continue
            for r in res if isinstance(res, list) else []:
                j = r.get("id")
                if isinstance(j, int) and 0 <= j < len(chunk):
                    lab = _clean(r)
                    lab.pop("id", None)
                    self._remember(chunk[j][0], lab)
                    out[chunk[j][0]] = lab
        return out


def _q(s: str) -> str:
    return "'" + str(s).replace("\\", "\\\\").replace("'", "''") + "'"


# Stage 1: one Snowflake query per chunk of bursts -> {key: summary}. Empty dict on DuckDB or any failure.
def cortex_summaries(store: Store | None, firsts: list) -> dict:
    if store is None or getattr(store, "dialect", "") != "snowflake" or not firsts:
        return {}
    out = {}
    for i in range(0, len(firsts), 150):
        chunk = firsts[i:i + 150]
        values = ",".join(f"({_q(k)},{_q(w)},{_q(t)},{ts_literal(ts)}::TIMESTAMP_NTZ)" for k, w, t, ts in chunk)
        sql = f"""
            WITH k(key, wiki, title, t) AS (SELECT * FROM VALUES {values})
            SELECT k.key, AI_SUMMARIZE_AGG(e.comment) AS summary
            FROM k JOIN EDITS e ON e.wiki = k.wiki AND e.title = k.title
             AND e.ts <= k.t AND e.ts > DATEADD(hour, -1, k.t) AND NOT e.is_bot AND e.comment <> ''
            GROUP BY k.key"""
        try:
            df = store.query(sql)
        except Exception as e:                                       # noqa: BLE001 - labelling still works without it
            print("cortex summaries skipped:", str(e)[:200])
            return out
        for r in df.itertuples():
            s = (r.summary or "").strip().replace("\n", " ")
            if s and not any(b in s.lower() for b in BAD_SUMMARY):
                out[r.key] = s[:600]
    return out


# Pick the bursts from a feature frame and build each prompt's evidence from edits with ts <= t only.
def label_bursts(raw: RawData, frame: pd.DataFrame, labeler: GeminiLabeler, store: Store | None = None) -> pd.DataFrame:
    bursts = frame[(frame.edits_60m >= C.GEMINI_BURST_EDITS_60M) & (frame.humans_60m >= C.GEMINI_BURST_HUMANS_60M)]
    bursts = bursts.sort_values("t")
    e = raw.edits
    evidence, firsts = {}, []
    for b in bursts.itertuples():                 # the earliest burst row of each (wiki, title, hour) defines its evidence
        key = labeler.key(b.wiki, b.title, b.t)
        if key in evidence or key in labeler.cache:
            continue
        w = e[(e.wiki == b.wiki) & (e.title == b.title) & (e.ts <= b.t) & (e.ts > b.t - HOUR)]
        human = w[~w.is_bot].sort_values("ts", ascending=False)
        on_mp = "yes, Today's Featured Article" if b.is_tfa_today else ("yes, In the news" if b.in_itn_6h else "no")
        evidence[key] = (b.wiki, b.title, {
            "n": int(len(human)), "humans": int(human.user_name.nunique()), "reverts": int(human.is_revert.sum()),
            "main_page": on_mp, "comments": " | ".join(c[:160] for c in human.comment.fillna("").head(15) if c) or "(no summaries)"})
        firsts.append((key, b.wiki, b.title, b.t))
    summaries = cortex_summaries(store, firsts)
    items = []
    for key, (wiki, title, ev) in evidence.items():
        if key in summaries:
            ev["summary"] = summaries[key]
        items.append((key, wiki, title, ev))
    labeler.label_many(items)

    rows = []
    for b in bursts.itertuples():
        lab = labeler.cache.get(labeler.key(b.wiki, b.title, b.t))
        if lab:
            rows.append({"ts": b.t, "wiki": b.wiki, "title": b.title, "cause": lab["cause"],
                         "confidence": lab["confidence"], "reason": str(lab.get("reason", ""))[:500]})
    print(f"labeller: {len(bursts)} burst rows, {len(items)} new (wiki,title,hour), {len(summaries)} Snowflake summaries, "
          f"{labeler.calls} Gemini calls")
    return pd.DataFrame(rows, columns=["ts", "wiki", "title", "cause", "confidence", "reason"])


# Idempotent write of one window of labels.
def write_labels(store: Store, labels: pd.DataFrame, t_from, t_to) -> int:
    return store.replace_rows(labels, "CAUSE_LABELS", f"ts >= {ts_literal(t_from)} AND ts <= {ts_literal(t_to)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Label edit bursts (Snowflake summary + Gemini) and write CAUSE_LABELS")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--budget", type=int, default=3000)
    a = ap.parse_args(argv)
    store = open_store()
    raw = load_window(store, a.start, a.end)
    frame = build(raw, make_grid(raw, a.start, a.end), with_labels=False, with_causes=False)
    lab = GeminiLabeler(budget=a.budget)
    out = label_bursts(raw, frame, lab, store)
    n = write_labels(store, out, a.start, a.end)
    print(f"{n} cause labels written ({lab.calls} Gemini calls, rest from cache)")


if __name__ == "__main__":
    main()
