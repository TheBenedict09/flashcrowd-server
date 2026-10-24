"""Build REAL level packages for flashcrowd_engine.GameSession from Snowflake (PC1).

Format = PC3's `flashcrowd export` package: {level, constants, hourly_views, scheduled, region_forecasts, article_forecasts}.
  - hourly_views[r]: real published page views per region, computed with PC3's regional_views (same basis as
    REGION_FORECASTS; enwiki split by PC3's hour-of-day default weights).
  - constants: PC1's measured B and C via flashcrowd_engine.from_measurements; SCALE via calibrate_scale.
  - scheduled: flashcrowd_engine.scheduled_table over the 7 days before the level.
  - region_forecasts / article_forecasts: PC3's real FORECASTS / REGION_FORECASTS rows when they exist for the window,
    otherwise persistence (same hour yesterday) region forecasts and an empty radar — stated in the level note.
Boss levels = the biggest real jumps in servers needed (vs the trailing 3 h median), one window per region first.
  python api/build_levels.py [--n 3] [--since 2026-10-18]
"""
import argparse, json, math, pathlib, statistics, sys
from datetime import datetime, timedelta
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.sf import connect
from flashcrowd_engine import REGIONS, from_measurements, calibrate_scale
from flashcrowd_engine.policies import scheduled_table, persistence_forecasts

OUT = ROOT / "api" / "levels"
WEIGHTS = {"dewiki": {"LDN": 1}, "frwiki": {"LDN": 1}, "itwiki": {"LDN": 1}, "nlwiki": {"LDN": 1},
           "eswiki": {"NJ": 1}, "ptwiki": {"NJ": 1}, "jawiki": {"TYO": 1}, "kowiki": {"TYO": 1}, "idwiki": {"TYO": 1},
           "enwiki": {"LDN": 0.35, "NJ": 0.45, "TYO": 0.20}}
REGION_WIKIS = {r: [w for w, m in WEIGHTS.items() if r in m] for r in REGIONS}
CITY = {"LDN": "London", "NJ": "New Jersey", "TYO": "Tokyo"}
TOP_N = 500
H = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")


def regional_hourly(cur):
    """V_r(h) with PC3's own definition (flashcrowd_model.regions.regional_views): all published views per wiki-hour,
    enwiki split by the hour-of-day default weights. Same basis as PC3's REGION_FORECASTS, so the autopilot's
    forecasts and the simulated demand are in the same units."""
    import types, pandas as pd
    from flashcrowd_model.regions import regional_views
    rows = cur.execute("SELECT hour_utc, wiki, SUM(views) FROM PAGEVIEWS_HOURLY GROUP BY 1, 2").fetchall()
    w = pd.DataFrame(rows, columns=["hour_utc", "wiki", "views"])
    w["views"] = w["views"].astype(float)
    raw = types.SimpleNamespace(wiki_hour_views=w,
                                en_shared=pd.DataFrame(columns=["hour_utc", "region", "views_weighted", "views_raw"]))
    rv = regional_views(raw)
    hours = [h.to_pydatetime() for h in rv.index]
    series = {h.to_pydatetime(): {r: float(rv.loc[h, r]) for r in REGIONS} for h in rv.index}
    return hours, series


def top_article(cur, region, t0, t1):
    """Real article whose views rose most vs the same hours a day earlier (for the level title)."""
    wikis = ",".join(f"'{w}'" for w in REGION_WIKIS[region])
    r = cur.execute(f"""
        WITH now AS (SELECT wiki, title, MAX(views) pk FROM PAGEVIEWS_ARTICLES
                     WHERE wiki IN ({wikis}) AND hour_utc BETWEEN '{t0}' AND '{t1}' GROUP BY 1, 2 HAVING pk >= 2000),
             prev AS (SELECT wiki, title, MAX(views) pk FROM PAGEVIEWS_ARTICLES
                      WHERE wiki IN ({wikis}) AND hour_utc BETWEEN DATEADD(day,-1,'{t0}') AND DATEADD(day,-1,'{t1}') GROUP BY 1, 2)
        SELECT now.wiki, now.title, now.pk, COALESCE(prev.pk, 0) FROM now LEFT JOIN prev USING (wiki, title)
        ORDER BY now.pk / GREATEST(COALESCE(prev.pk, 0), 200) DESC LIMIT 1""").fetchone()
    return r


def live_forecasts(cur, t0, t1):
    rf = cur.execute("""SELECT issued_at, region, target_hour, q50_views, q90_views FROM REGION_FORECASTS
                        WHERE target_hour BETWEEN %s AND %s""", (t0, t1)).fetchall()
    af = cur.execute("""SELECT issued_at, wiki, title, region, horizon_h, p_surge, size_q50, size_q90, cause, top_reasons
                        FROM FORECASTS WHERE issued_at BETWEEN DATEADD(hour,-3,%s) AND %s""", (t0, t1)).fetchall()
    rf = [{"issued_at": H(a), "region": b, "target_hour": H(c), "q50_views": float(d), "q90_views": float(e)} for a, b, c, d, e in rf]
    af = [{"issued_at": H(r[0]), "wiki": r[1], "title": r[2], "region": r[3], "horizon_h": int(r[4]), "p_surge": float(r[5]),
           "size_q50": float(r[6] or 0), "size_q90": float(r[7] or 0), "cause": r[8],
           "top_reasons": json.loads(r[9]) if isinstance(r[9], str) else (r[9] or [])} for r in af]
    return rf, af


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--since", default="2026-10-18")
    ap.add_argument("--hours", type=int, default=6)
    ap.add_argument("--test-start", default="2026-10-24", help="first held-out day of the current model (metrics.json test split)")
    a = ap.parse_args()
    conn = connect(); cur = conn.cursor()
    meas = cur.execute("SELECT kind, region, value, unit FROM INFRA_MEASUREMENTS WHERE kind IN ('boot','capacity')").fetchall()
    k0 = from_measurements([{"kind": x[0], "region": x[1], "value": x[2], "unit": x[3]} for x in meas])
    hours, S = regional_hourly(cur)
    k = calibrate_scale({r: [S[h][r] for h in hours] for r in REGIONS}, k0)
    print("constants:", k.to_json())
    need = {r: [k.servers_for_views(S[h][r]) for h in hours] for r in REGIONS}
    since = datetime.fromisoformat(a.since)
    cands = []
    for r in REGIONS:
        for i, h in enumerate(hours):
            if h < since or i < 3 or i + a.hours - 2 >= len(hours):
                continue
            base = statistics.median(need[r][i - 3:i])
            jump = need[r][i] / max(base, 1)
            cands.append((jump, need[r][i] - base, r, i))
    cands.sort(reverse=True)
    picked = []
    for jump, plus, r, i in cands:          # one per region first, no overlapping windows in the same region
        if len(picked) >= a.n: break
        if any(p[2] == r for p in picked) and len({p[2] for p in picked}) < len(REGIONS): continue
        if any(p[2] == r and abs(p[3] - i) < a.hours for p in picked): continue
        picked.append((jump, plus, r, i))
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for jump, plus, r, i in picked:
        s = i - 2; start = hours[s]; end = hours[s + a.hours - 1]
        art = top_article(cur, r, hours[i], hours[min(i + 2, len(hours) - 1)])
        part = "night" if start.hour < 6 else "morning" if start.hour < 12 else "afternoon" if start.hour < 18 else "evening"
        lid = f"{start:%b-%d}-{part}-{r}".lower()
        name = art[1].replace("_", " ") if art else "regional"
        rf, af = live_forecasts(cur, start, end)
        held_out = start >= datetime.fromisoformat(a.test_start)
        src = ("PC3 model forecasts, as-of (no look-ahead), " +
               ("HELD-OUT test day" if held_out else "IN-SAMPLE (inside the model's training window)"))
        if not rf:
            rf = persistence_forecasts({rr: [S[hours[j - 24]][rr] for j in range(s, s + a.hours)] for rr in REGIONS}, H(start))
            src = "persistence region forecasts (PC3 live forecasts not yet available for this window)"
        train = [h for h in hours if start - timedelta(days=7) <= h < start]
        sched = scheduled_table({rr: [(h.hour, S[h][rr]) for h in train] for rr in REGIONS}, k)
        lv = {"id": lid, "title": f"{name} surge in {CITY[r]}", "start": H(start), "hours": a.hours,
              "note": (f"REAL Wikipedia demand ({art[0]} '{name}': peak {art[2]:,} views/h vs {art[3]:,} a day earlier). "
                       f"Servers needed jump {jump:.2f}x at {hours[i]:%H:%M} UTC. Measured B and C. {src}.") if art else
                      f"REAL Wikipedia demand. Servers needed jump {jump:.2f}x at {hours[i]:%H:%M} UTC.",
              "surge_ratio": round(max(1.1, min(1.5, jump * 0.8)), 2), "surge_min_jump": 1}
        pkg = {"level": lv, "constants": k.to_json(), "scheduled": sched, "region_forecasts": rf, "article_forecasts": af,
               "hourly_views": {rr: [S[hours[j]][rr] for j in range(s, s + a.hours)] for rr in REGIONS}}
        (OUT / f"{lid}.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=1))
        index.append({kk: lv[kk] for kk in ("id", "title", "start", "hours", "note")})
        print(f"{lid}: {lv['title']} | jump {jump:.2f}x (+{plus:.0f} servers) | needed {need[r][s:s+a.hours]} | {src[:30]}")
    (OUT.parent / "levels.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    conn.close()


if __name__ == "__main__":
    main()
