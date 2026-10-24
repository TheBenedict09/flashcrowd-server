import json, os, re, sys, time, argparse
from datetime import datetime, timezone
from requests_sse import EventSource

KEEP = {"enwiki","dewiki","frwiki","itwiki","nlwiki","eswiki","ptwiki","jawiki","kowiki","idwiki"}
REVERT = re.compile(r"(revert|undid|undo|rv\b|rvv|restored revision)", re.I)
UA = {"User-Agent": os.environ["WIKI_USER_AGENT"]}

ap = argparse.ArgumentParser()
ap.add_argument("--since", help="ISO time for historical replay, e.g. 2026-10-17T00:00:00Z")
ap.add_argument("--until", help="stop when events pass this ISO time (backfill only)")
ap.add_argument("--out", default="edits_live.jsonl")
a = ap.parse_args()
url = "https://stream.wikimedia.org/v2/stream/recentchange" + (f"?since={a.since}" if a.since else "")
until = datetime.fromisoformat(a.until.replace("Z","+00:00")).timestamp() if a.until else None
since = datetime.fromisoformat(a.since.replace("Z","+00:00")).timestamp() if a.since else None
# The stream merges two data-centre topics (eqiad + codfw) that can be days apart, so in backfill mode we
# SKIP out-of-range events and only stop after STOP_AFTER consecutive events past --until.
STOP_AFTER = 50_000
past = 0

def row(c):
    ln = c.get("length") or {}
    return {"ts": datetime.fromtimestamp(c["timestamp"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "wiki": c["wiki"], "title": c["title"].replace(" ", "_"), "namespace": c.get("namespace"),
            "type": c.get("type"), "user_name": c.get("user"), "is_bot": bool(c.get("bot")),
            "is_revert": bool(REVERT.search(c.get("comment") or "")),
            "delta_bytes": (ln.get("new") or 0) - (ln.get("old") or 0),
            "comment": (c.get("comment") or "")[:300]}

while True:
    if a.since: url = url.split("?")[0] + f"?since={a.since}"   # reconnects restart the window (dupes removed in Snowflake)
    try:
        with EventSource(url, headers=UA, timeout=60) as stream, open(a.out, "a") as out:
            for ev in stream:
                if ev.type != "message": continue
                try: c = json.loads(ev.data)
                except ValueError: continue
                if c.get("meta", {}).get("domain") == "canary": continue
                ts = c.get("timestamp", 0)
                if until and ts > until:
                    past += 1
                    if past >= STOP_AFTER: sys.exit(0)
                    continue
                past = 0
                if since and ts < since: continue
                if c.get("wiki") not in KEEP or c.get("type") not in ("edit", "new"): continue
                ns = c.get("namespace")
                if ns == 0 or (ns == 10 and c.get("title") == "Template:In the news"):
                    out.write(json.dumps(row(c), ensure_ascii=False) + "\n")
    except SystemExit:
        raise
    except Exception as e:
        print("reconnect:", e, file=sys.stderr); time.sleep(5)
