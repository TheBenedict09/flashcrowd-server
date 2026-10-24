"""Hourly page-view dumps -> Snowflake PAGEVIEWS_HOURLY (roadmap §6 P1-A).

published_at = Last-Modified of the dump file: THE no-look-ahead key.
  Backfill:  python pipeline/pageviews_loader.py --start 2026-10-10T00 --end 2026-10-24T12 --workers 2
  Cron:      python pipeline/pageviews_loader.py --recent 8      (fetch any of the last 8 h not yet loaded)
"""
import argparse, csv, gzip, io, os, sys, pathlib, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import requests
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipeline.sf import connect

WIKIS = ["en", "de", "fr", "it", "nl", "es", "pt", "ja", "ko", "id"]
DOMAIN = {d: f"{w}wiki" for w in WIKIS for d in (w, f"{w}.m")}      # desktop + mobile web
MIN_VIEWS = 20
DATA = pathlib.Path(os.environ.get("FC_DATA", "/home/ubuntu/data"))
OUT = DATA / "pageviews"; DONE = DATA / "pv_done.txt"
UA = {"User-Agent": os.environ.get("WIKI_USER_AGENT", "FlashcrowdOxHack/1.0")}
STAGE = "FC_PV_STAGE"


def url(h):
    return f"https://dumps.wikimedia.org/other/pageviews/{h:%Y}/{h:%Y-%m}/pageviews-{h:%Y%m%d-%H}0000.gz"


def fetch_hour(h):
    """Download + aggregate one hour. Returns (hour, csv_path, rows) or (hour, None, reason)."""
    u = url(h)
    for attempt in range(4):
        try:
            head = requests.head(u, headers=UA, timeout=30)
            if head.status_code == 404:
                return h, None, "not published yet"
            head.raise_for_status()
            pub = parsedate_to_datetime(head.headers["Last-Modified"]).astimezone(timezone.utc)
            agg = {}
            with requests.get(u, headers=UA, stream=True, timeout=120) as r:
                r.raise_for_status()
                with gzip.GzipFile(fileobj=r.raw) as gz:
                    for raw in io.TextIOWrapper(gz, encoding="utf-8", errors="replace"):
                        dom, _, rest = raw.partition(" ")
                        wiki = DOMAIN.get(dom)
                        if not wiki:
                            continue
                        parts = rest.split(" ")
                        if len(parts) < 2:
                            continue
                        try: v = int(parts[1])
                        except ValueError: continue
                        k = (wiki, parts[0]); agg[k] = agg.get(k, 0) + v
            OUT.mkdir(parents=True, exist_ok=True)
            path = OUT / f"pv_{h:%Y%m%d_%H}.csv.gz"
            hs, ps = h.strftime("%Y-%m-%d %H:00:00"), pub.strftime("%Y-%m-%d %H:%M:%S")
            n = 0
            with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                for (wiki, title), v in agg.items():
                    if v >= MIN_VIEWS:
                        w.writerow([hs, wiki, title, v, ps]); n += 1
            return h, path, n
        except Exception as e:
            err = e; time.sleep(10 * (attempt + 1))
    return h, None, f"failed: {err}"


def load(conn, path, h):
    """Idempotent: the hour is deleted and re-copied in one transaction, so a re-run can never double-count."""
    cur = conn.cursor()
    cur.execute(f"PUT 'file://{path}' @{STAGE} AUTO_COMPRESS=FALSE OVERWRITE=TRUE")
    cur.execute("BEGIN")
    cur.execute("DELETE FROM PAGEVIEWS_HOURLY WHERE hour_utc = %s", (h.strftime("%Y-%m-%d %H:00:00"),))
    cur.execute(f"""COPY INTO PAGEVIEWS_HOURLY (hour_utc, wiki, title, views, published_at)
        FROM @{STAGE}/{path.name}
        FILE_FORMAT = (TYPE=CSV COMPRESSION=GZIP FIELD_OPTIONALLY_ENCLOSED_BY='"' ESCAPE_UNENCLOSED_FIELD=NONE)
        PURGE = TRUE FORCE = TRUE""")
    cur.execute("COMMIT")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--recent", type=int, help="check the last N hours")
    ap.add_argument("--workers", type=int, default=2)        # be polite: max 2 parallel downloads
    a = ap.parse_args()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    if a.recent:
        hours = [now - timedelta(hours=i) for i in range(a.recent, 1, -1)]
    else:
        s, e = (datetime.strptime(x, "%Y-%m-%dT%H") for x in (a.start, a.end))
        hours = [s + timedelta(hours=i) for i in range(int((e - s).total_seconds() // 3600) + 1)]
    done = set(DONE.read_text().split()) if DONE.exists() else set()
    todo = [h for h in hours if f"{h:%Y%m%d%H}" not in done]
    print(f"{len(todo)} hours to load", flush=True)
    if not todo:
        return
    conn = connect()
    conn.cursor().execute(f"CREATE STAGE IF NOT EXISTS {STAGE}")
    try:
        with ProcessPoolExecutor(a.workers) as ex:
            for fut in as_completed([ex.submit(fetch_hour, h) for h in todo]):
                h, path, info = fut.result()
                if path is None:
                    print(f"{h:%Y-%m-%d %H}h skipped: {info}", flush=True); continue
                load(conn, path, h); path.unlink()
                with open(DONE, "a") as f: f.write(f"{h:%Y%m%d%H}\n")
                print(f"{h:%Y-%m-%d %H}h loaded {info} rows", flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
