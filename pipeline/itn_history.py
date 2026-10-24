"""'In the news' additions (enwiki) -> MAIN_PAGE_EVENTS (slot='itn', ts = revision timestamp when the bold link appeared).
Uses revisions of Template:In_the_news, NOT the feed's end-of-day `news` (that would be look-ahead).
An event = a bold-linked title present in a revision but not in the previous one (re-additions count again).
Re-runnable: recomputes the whole window and replaces it. Run every 10 min for live mode.
  python pipeline/itn_history.py --since 2026-10-03T00:00:00Z
"""
import argparse, os, re, sys, pathlib
from datetime import datetime, timedelta, timezone
import requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipeline.sf import connect

UA = {"User-Agent": os.environ.get("WIKI_USER_AGENT", "FlashcrowdOxHack/1.0")}
API = "https://en.wikipedia.org/w/api.php"
BOLD = [re.compile(r"'''\s*\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]\s*'''"),      # '''[[Title]]''' / '''[[Title|text]]'''
        re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?\|\s*'''[^\]]*'''\s*\]\]")]           # [[Title|'''text''']]


def norm(t):
    t = t.strip().replace(" ", "_")
    return t[:1].upper() + t[1:]


def titles(text):
    # only the news items, not the "Ongoing" / "Recent deaths" footers
    body = re.split(r"\{\{\s*ITN[ _]footer|<!--\s*Ongoing|Ongoing\s*:", text, maxsplit=1, flags=re.I)[0]
    return {norm(m) for rx in BOLD for m in rx.findall(body)}


def revisions(since):
    params = {"action": "query", "prop": "revisions", "titles": "Template:In_the_news", "rvprop": "timestamp|content",
              "rvslots": "main", "rvlimit": 50, "rvdir": "newer", "rvstart": since, "format": "json", "formatversion": 2}
    while True:
        j = requests.get(API, params=params, headers=UA, timeout=60).json()
        for rv in j["query"]["pages"][0].get("revisions", []):
            yield rv["timestamp"], rv["slots"]["main"].get("content", "")
        if "continue" not in j:
            break
        params.update(j["continue"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=(datetime.now(timezone.utc) - timedelta(days=21)).strftime("%Y-%m-%dT00:00:00Z"))
    a = ap.parse_args()
    prev, rows, n = None, [], 0
    for ts, text in revisions(a.since):
        cur = titles(text); n += 1
        if prev is not None:      # first revision only sets the baseline (items already up have unknown start)
            for t in sorted(cur - prev):
                rows.append((ts.replace("T", " ").rstrip("Z"), "enwiki", t, "itn"))
        prev = cur
    print(f"{n} revisions scanned, {len(rows)} ITN additions")
    for r in rows[-8:]: print("  ", r[0], r[2])
    with connect() as conn:
        c = conn.cursor()
        c.execute("BEGIN")
        c.execute("DELETE FROM MAIN_PAGE_EVENTS WHERE slot='itn' AND ts >= %s", (a.since.replace("T", " ").rstrip("Z"),))
        if rows: c.executemany("INSERT INTO MAIN_PAGE_EVENTS (ts, wiki, title, slot) VALUES (%s,%s,%s,%s)", rows)
        c.execute("COMMIT")


if __name__ == "__main__":
    main()
