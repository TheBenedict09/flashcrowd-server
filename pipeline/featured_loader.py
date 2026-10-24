"""Today's Featured Article (enwiki) per day -> MAIN_PAGE_EVENTS (slot='tfa', ts = 00:00 UTC of that day).
TFA is scheduled in advance, so this carries no look-ahead risk. Re-runnable: replaces rows for the date range.
  python pipeline/featured_loader.py --start 2026-10-10 --end 2026-10-26
"""
import argparse, os, sys, pathlib
from datetime import date, timedelta
import requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipeline.sf import connect

UA = {"User-Agent": os.environ.get("WIKI_USER_AGENT", "FlashcrowdOxHack/1.0")}


def tfa(d):
    r = requests.get(f"https://en.wikipedia.org/api/rest_v1/feed/featured/{d:%Y/%m/%d}", headers=UA, timeout=30)
    if r.status_code != 200:
        return None
    t = (r.json().get("tfa") or {}).get("titles", {}).get("canonical")
    return t.replace(" ", "_") if t else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=str(date.today() - timedelta(days=15)))
    ap.add_argument("--end", default=str(date.today() + timedelta(days=1)))
    a = ap.parse_args()
    s, e = date.fromisoformat(a.start), date.fromisoformat(a.end)
    rows = []
    for i in range((e - s).days + 1):
        d = s + timedelta(days=i); t = tfa(d)
        print(d, t or "-")
        if t: rows.append((f"{d} 00:00:00", "enwiki", t, "tfa"))
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute("DELETE FROM MAIN_PAGE_EVENTS WHERE slot='tfa' AND ts BETWEEN %s AND %s", (f"{s} 00:00:00", f"{e} 00:00:00"))
        cur.executemany("INSERT INTO MAIN_PAGE_EVENTS (ts, wiki, title, slot) VALUES (%s,%s,%s,%s)", rows)
        cur.execute("COMMIT")
    print(f"loaded {len(rows)} TFA rows")


if __name__ == "__main__":
    main()
