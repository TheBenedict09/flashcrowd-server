"""Per-article region shares for enwiki from Wikimedia's top-per-country API -> COUNTRY_SHARE (roadmap §6 P1-C).
share_r = views in region r's countries / views in all listed countries (only top articles per country appear).
  python pipeline/country_share.py --start 2026-10-10 --end 2026-10-23
Re-runnable: replaces the date range.
"""
import argparse, os, sys, pathlib, time
from collections import defaultdict
from datetime import date, timedelta
import requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipeline.sf import connect

UA = {"User-Agent": os.environ.get("WIKI_USER_AGENT", "FlashcrowdOxHack/1.0")}
COUNTRIES = {"LDN": "GB DE FR IT ES NL PL SE IE BE".split(), "NJ": "US CA MX BR AR CO".split(),
             "TYO": "JP KR ID IN SG AU PH".split()}
API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top-per-country/{cc}/all-access/{d:%Y}/{d:%m}/{d:%d}"


def fetch(cc, d):
    for attempt in range(3):
        r = requests.get(API.format(cc=cc, d=d), headers=UA, timeout=30)
        if r.status_code == 404:
            return []
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json()["items"][0]["articles"]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=str(date.today() - timedelta(days=14)))
    ap.add_argument("--end", default=str(date.today() - timedelta(days=1)))
    a = ap.parse_args()
    s, e = date.fromisoformat(a.start), date.fromisoformat(a.end)
    rows = []
    for i in range((e - s).days + 1):
        d = s + timedelta(days=i)
        views = defaultdict(lambda: defaultdict(float))       # title -> region -> views
        for region, ccs in COUNTRIES.items():
            for cc in ccs:
                for art in fetch(cc, d):
                    if art.get("project") != "en.wikipedia":
                        continue
                    v = art.get("views_ceil") or art.get("views") or 0
                    views[art["article"].replace(" ", "_")][region] += float(v)
                time.sleep(0.1)
        for title, by_r in views.items():
            tot = sum(by_r.values())
            if tot <= 0 or title in ("Main_Page",) or title.startswith("Special:"):
                continue
            for region in COUNTRIES:
                rows.append((str(d), "enwiki", title, region, round(by_r.get(region, 0.0) / tot, 4)))
        print(d, len(views), "titles", flush=True)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute("DELETE FROM COUNTRY_SHARE WHERE day BETWEEN %s AND %s", (str(s), str(e)))
        cur.executemany("INSERT INTO COUNTRY_SHARE (day, wiki, title, region, share) VALUES (%s,%s,%s,%s,%s)", rows)
        cur.execute("COMMIT")
    print(f"loaded {len(rows)} rows")


if __name__ == "__main__":
    main()
