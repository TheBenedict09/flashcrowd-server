"""Load new lines from edits_*.jsonl into Snowflake EDITS. Run every 5 min (systemd timer).
Keeps a byte offset per file in <data>/.offsets.json so nothing is loaded twice; only complete lines are read."""
import json, os, sys, pathlib, glob
import pandas as pd
from snowflake.connector.pandas_tools import write_pandas
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pipeline.sf import connect

DATA = pathlib.Path(os.environ.get("FC_DATA", "/home/ubuntu/data"))
OFF = DATA / ".offsets.json"
COLS = ["ts", "wiki", "title", "namespace", "type", "user_name", "is_bot", "is_revert", "delta_bytes", "comment"]
BATCH = 200_000


def main():
    offsets = json.loads(OFF.read_text()) if OFF.exists() else {}
    conn = connect(); total = 0
    try:
        for f in sorted(glob.glob(str(DATA / "edits_*.jsonl"))):
            pos = offsets.get(f, 0)
            with open(f, "rb") as fh:
                fh.seek(pos)
                while True:
                    rows, start = [], fh.tell()
                    for _ in range(BATCH):
                        line = fh.readline()
                        if not line: break
                        if not line.endswith(b"\n"):       # partial line still being written
                            fh.seek(-len(line), 1); break
                        try: rows.append(json.loads(line))
                        except ValueError: pass
                    if not rows:
                        break
                    df = pd.DataFrame(rows, columns=COLS)
                    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_localize(None)
                    df.columns = [c.upper() for c in df.columns]
                    write_pandas(conn, df, "EDITS", use_logical_type=True)
                    total += len(df); offsets[f] = fh.tell()
                    OFF.write_text(json.dumps(offsets))       # commit offset only after a successful write
    finally:
        conn.close()
    print(f"loaded {total} rows")


if __name__ == "__main__":
    main()
