"""Explain the live alerts: why is each top-forecast article being edited right now?

Runs a few minutes after every live forecast (fc-explain.timer). For the top alerted articles of the latest FORECASTS
batch that have no recent label yet:

  1. Snowflake Cortex AI_SUMMARIZE_AGG turns the article's human edit summaries from the last 3 h into one sentence,
     inside the warehouse (one query for all articles).
  2. Gemini (PC3's GeminiLabeler, batched, JSON schema, temperature 0) picks a cause from the contract enum and writes a
     one-line reason, seeing the Snowflake summary, the raw edit summaries and the model's own top reasons.

Writes CAUSE_EXPLAIN (same columns as CAUSE_LABELS + summary). It is kept apart from CAUSE_LABELS on purpose:
CAUSE_LABELS holds burst labels that can become model features, these are explanations only. Evidence is limited to
edits with ts <= issued_at, so an explanation never uses the future either.

    python -m pipeline.cause_explain            # latest batch
"""
import json
import os
import pathlib
import sys

from pipeline.sf import connect

sys.path.insert(0, str(pathlib.Path.home() / "flashcrowd-model" / "src"))
from flashcrowd_model.gemini_labeler import GeminiLabeler, BAD_SUMMARY  # noqa: E402

TOP_N = int(os.getenv("FC_EXPLAIN_TOP", "15"))
MIN_P = float(os.getenv("FC_EXPLAIN_MIN_P", "0.10"))

DDL = """CREATE TABLE IF NOT EXISTS CAUSE_EXPLAIN (ts TIMESTAMP_NTZ, wiki STRING, title STRING, cause STRING,
         confidence FLOAT, reason STRING, summary STRING, n_edits INT, labelled_at TIMESTAMP_NTZ)"""


def main():
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(DDL)
        top = cur.execute("""
            SELECT issued_at, wiki, title, p_surge, top_reasons FROM FORECASTS
            WHERE issued_at = (SELECT MAX(issued_at) FROM FORECASTS) AND p_surge >= %s
            QUALIFY ROW_NUMBER() OVER (PARTITION BY wiki, title ORDER BY p_surge DESC) = 1
            ORDER BY p_surge DESC LIMIT %s""", (MIN_P, TOP_N)).fetchall()
        if not top:
            print("explain: nothing alerted"); return
        t = top[0][0]
        done = {(w, ti) for w, ti in cur.execute("""
            SELECT wiki, title FROM CAUSE_EXPLAIN WHERE ts > DATEADD(hour, -2, %s::TIMESTAMP_NTZ)
            UNION SELECT wiki, title FROM CAUSE_LABELS WHERE ts > DATEADD(hour, -2, %s::TIMESTAMP_NTZ)""", (t, t)).fetchall()}
        todo = [r for r in top if (r[1], r[2]) not in done]
        if not todo:
            print("explain: all top alerts already explained"); return

        keys = " UNION ALL ".join("SELECT %s, %s" for _ in todo)
        params = [x for r in todo for x in (r[1], r[2])]
        ev = {}
        for w, ti, n, humans, reverts, comments, summary in cur.execute(f"""
            WITH k(wiki, title) AS ({keys})
            SELECT k.wiki, k.title, COUNT(*), COUNT(DISTINCT e.user_name), COUNT_IF(e.is_revert),
                   ARRAY_TO_STRING(ARRAY_SLICE(ARRAY_AGG(LEFT(e.comment, 160)) WITHIN GROUP (ORDER BY e.ts DESC), 0, 15), ' | '),
                   AI_SUMMARIZE_AGG(e.comment)
            FROM k JOIN EDITS e ON e.wiki = k.wiki AND e.title = k.title AND NOT e.is_bot AND e.comment <> ''
             AND e.ts <= %s AND e.ts > DATEADD(hour, -3, %s::TIMESTAMP_NTZ)
            GROUP BY k.wiki, k.title""", params + [t, t]).fetchall():
            s = (summary or "").strip().replace("\n", " ")
            ev[(w, ti)] = {"n": n, "humans": humans, "reverts": reverts, "comments": comments or "(no summaries)",
                           "summary": s[:600] if s and not any(b in s.lower() for b in BAD_SUMMARY) else "(none)"}

        lab = GeminiLabeler(budget=4)
        items = []
        for _, w, ti, p, reasons in todo:
            e = ev.get((w, ti), {"n": 0, "humans": 0, "reverts": 0, "comments": "(no edits in the last 3 h)", "summary": "(none)"})
            rs = json.loads(reasons) if isinstance(reasons, str) else (reasons or [])
            e = {**e, "main_page": "see model reasons: " + "; ".join(map(str, rs[:3]))}
            items.append((f"explain|{w}|{ti}|{t:%Y-%m-%dT%H%M}", w, ti, e))
        got = lab.label_many(items)
        rows = []
        for key, w, ti, e in items:
            if key in got:
                g = got[key]
                rows.append((t, w, ti, g["cause"], g["confidence"], g["reason"], e["summary"], e["n"]))
        if rows:
            cur.executemany("""INSERT INTO CAUSE_EXPLAIN (ts, wiki, title, cause, confidence, reason, summary, n_edits, labelled_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, SYSDATE())""", rows)
        print(f"explain: {len(todo)} alerts, {sum(1 for x in ev.values() if x['summary'] != '(none)')} Snowflake summaries, "
              f"{lab.calls} Gemini calls, {len(rows)} written")


if __name__ == "__main__":
    main()
