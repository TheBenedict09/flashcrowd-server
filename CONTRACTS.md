# CONTRACTS.md — FLASHCROWD (copied verbatim from roadmap §3; keep identical in all three repos)

> `CONTRACTS_VERSION = 1.1` — any change is announced in the team chat and the version bumped in all three repos.

### 3.1 Naming and units
- All times **UTC**, ISO-8601 with `Z` (`2026-10-24T14:00:00Z`). Python: timezone-aware `datetime`. Snowflake: `TIMESTAMP_NTZ` holding UTC.
- **Regions:** `LDN`, `NJ`, `TYO` ↔ AWS regions `eu-west-2` (London), `us-east-1` (N. Virginia — stands in for the US-East/NJ edge; label stays `NJ`), `ap-northeast-1` (Tokyo).
  - *v1.1 change (Sat 16:00): cloud provider switched from Vultr to AWS EC2. Region labels unchanged, so no frontend/model code changes needed.*
- **Wikis tracked** (`wiki` = EventStreams `wiki` field): `enwiki dewiki frwiki itwiki nlwiki eswiki ptwiki jawiki kowiki idwiki`.
- **Wiki → region map** (stated assumption, goes on a slide):

| wiki | region weights |
|-|-|
| dewiki, frwiki, itwiki, nlwiki | LDN 1.0 |
| eswiki, ptwiki | NJ 1.0 (most readers are in the Americas) |
| jawiki, kowiki, idwiki | TYO 1.0 |
| enwiki | split by `COUNTRY_SHARE` for that article/day when available; otherwise default LDN 0.35 / NJ 0.45 / TYO 0.20 × each region's hour-of-day curve |

- **Page-view dump domain codes:** `en` (desktop) + `en.m` (mobile web) etc. Sum both per title.
- **Titles:** use underscores as in dumps (`Mount_Fuji`). Convert EventStreams titles (spaces) with `title.replace(" ", "_")`.
- **Game time unit:** 1 simulation step = 1 minute.

### 3.2 Snowflake objects (PC1 creates, PC3 reads)
```sql
CREATE DATABASE IF NOT EXISTS FLASHCROWD;
CREATE SCHEMA IF NOT EXISTS FLASHCROWD.CORE;
USE SCHEMA FLASHCROWD.CORE;
CREATE WAREHOUSE IF NOT EXISTS FC_WH WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE;

CREATE TABLE IF NOT EXISTS EDITS (
  ts           TIMESTAMP_NTZ,   -- event time (UTC)
  wiki         STRING,
  title        STRING,          -- underscores
  namespace    INT,
  type         STRING,          -- edit | new
  user_name    STRING,
  is_bot       BOOLEAN,
  is_revert    BOOLEAN,         -- comment matches revert patterns
  delta_bytes  INT,
  comment      STRING
);

CREATE TABLE IF NOT EXISTS PAGEVIEWS_HOURLY (
  hour_utc      TIMESTAMP_NTZ,  -- start of the hour the views happened in
  wiki          STRING,
  title         STRING,
  views         INT,            -- desktop + mobile web summed
  published_at  TIMESTAMP_NTZ   -- Last-Modified of the dump file (≈ hour + 2h). THE no-look-ahead key.
);

CREATE TABLE IF NOT EXISTS MAIN_PAGE_EVENTS (
  ts     TIMESTAMP_NTZ,   -- when it became visible (TFA: 00:00 UTC of its day; ITN: revision timestamp)
  wiki   STRING,          -- enwiki only is fine
  title  STRING,
  slot   STRING           -- tfa | itn
);

CREATE TABLE IF NOT EXISTS COUNTRY_SHARE (
  day     DATE,
  wiki    STRING,
  title   STRING,
  region  STRING,         -- LDN | NJ | TYO (mapped from country)
  share   FLOAT
);

CREATE TABLE IF NOT EXISTS CAUSE_LABELS (   -- written by PC3's Gemini labeller
  ts        TIMESTAMP_NTZ,  -- decision time the label was produced for
  wiki      STRING,
  title     STRING,
  cause     STRING,         -- see enum in 3.4
  confidence FLOAT,
  reason    STRING
);

CREATE TABLE IF NOT EXISTS FORECASTS (        -- written by PC3 forecast job
  issued_at  TIMESTAMP_NTZ, wiki STRING, title STRING, region STRING,
  horizon_h  INT, p_surge FLOAT, size_q50 FLOAT, size_q90 FLOAT,
  cause STRING, top_reasons VARIANT
);

CREATE TABLE IF NOT EXISTS REGION_FORECASTS ( -- written by PC3, consumed by decision engine + API
  issued_at TIMESTAMP_NTZ, region STRING, target_hour TIMESTAMP_NTZ,
  q50_views FLOAT, q90_views FLOAT
);

CREATE TABLE IF NOT EXISTS INFRA_MEASUREMENTS (  -- written by PC1
  ts TIMESTAMP_NTZ, kind STRING,  -- boot | rtt | capacity
  region STRING, peer STRING, value FLOAT, unit STRING, note STRING
);
```

### 3.3 Simulator spec (PC3 implements in `flashcrowd_engine/sim.py`; PC1 imports it)
- **State per region `r`:** `running` (int ≥ 1), `booting` (list of `ready_at` minutes), cumulative `served`, `demand`, `dropped`, `server_minutes`, `breach_minutes`.
- **Constants (filled from measurements, Section 7):** `C` = requests/second one server sustains at p95 < 200 ms; `B[r]` = median boot-to-serving minutes; `SCALE` = requests per real Wikipedia view (chosen so a normal hour needs 2–6 servers per region); `MIN_SERVERS = 1`; `MAX_SERVERS = 40`.
- **Demand:** for minute `m` in hour `h`, region request rate `D_r(m)` (req/min) = `SCALE × V_r(h) / 60`, linearly interpolated between hour midpoints, where `V_r(h)` = Σ over tracked articles of `views × region_weight` (3.1). Region-level only; articles matter for forecasts/explanations.
- **Each minute:** (1) servers whose `ready_at ≤ m` join `running`; (2) `cap = running × C × 60`; `served = min(D, cap)`; `dropped = D − served`; (3) `server_minutes += running + len(booting)` (booting servers cost too); (4) `breach_minutes += 1` if `dropped > 0.01 × D`; (5) policy acts on the observation.
- **Actions:** `{"type":"scale","region":"TYO","delta":+3}` → `delta` new entries in `booting` with `ready_at = m + B[r]`; negative delta removes running servers (never below `MIN_SERVERS`), cancelling booting ones first.
- **Score:** `served_pct = Σserved/Σdemand`, `server_hours = Σserver_minutes/60`, `breach_minutes`. Pareto plot: x = server_hours, y = 100 − served_pct.
- **Observation given to policies:** `{m, now_utc, regions: {r: {running, booting:[ready_at…], util_hist:[last 15 min], demand_hist:[last 15 min]}}, region_forecasts:[only issued_at ≤ now], article_forecasts:[only issued_at ≤ now]}`.
- **Determinism:** no randomness inside the sim. Same inputs → same score.

### 3.4 JSON shapes
**Article forecast** (`FORECASTS` row / API item):
```json
{ "issued_at": "2026-10-24T15:00:00Z", "wiki": "jawiki", "title": "台風", "region": "TYO",
  "horizon_h": 2, "p_surge": 0.71, "size_q50": 38000, "size_q90": 90000,
  "cause": "breaking_news",
  "top_reasons": ["9 human editors in the last hour", "Created in 4 languages", "In 'In the news' since 14:20"] }
```
**Cause enum:** `breaking_news | death | sports | scheduled_event | entertainment | vandalism | edit_war | bot_maintenance | other`.

**Region forecast:** `{ "issued_at": "...Z", "region": "LDN", "target_hour": "...Z", "q50_views": 812000, "q90_views": 1430000 }`

### 3.5 HTTP + WebSocket API (PC1 builds, PC2 calls). Base: `https://<api-host>/api`
| Method | Path | Body / query | Returns |
|-|-|-|-|
| GET | `/health` | – | `{"ok":true,"version":"1.1"}` |
| GET | `/levels` | – | `[{"id":"oct-19-evening","title":"Sunday night surge","start":"...Z","hours":6,"note":"..."}]` |
| POST | `/sessions` | `{"level_id":"...", "speed":60}` (speed = sim minutes per real second) | `{"session_id":"abc","start":"...Z","end":"...Z","constants":{"C":...,"B":{"LDN":..},"SCALE":...}}` |
| POST | `/sessions/{id}/action` | `{"type":"scale","region":"TYO","delta":1}` | `{"ok":true}` or `{"ok":false,"error":"min_servers"}` |
| POST | `/sessions/{id}/control` | `{"cmd":"pause"\|"resume"\|"speed","value":120}` | `{"ok":true}` |
| GET | `/sessions/{id}/review` | – | review object (below) |
| WS | `/sessions/{id}/stream` | – | one `tick` message per sim minute (below) |
| GET | `/live/forecasts` | `?limit=20` | latest article forecasts (3.4) |
| GET | `/live/scorecard` | – | scorecard object (below) |
| POST | `/voice/tts` | `{"text":"..."}` | `audio/mpeg` (ElevenLabs proxied; key stays server-side) |
| GET | `/agent/reasons` | `?region=TYO` | `{"region":"TYO","action":"start 3 servers","reasons":[...],"p_surge":0.71}` (ElevenLabs agent tool) |

**WS `tick` message:**
```json
{ "type": "tick", "m": 312, "now": "2026-10-19T17:12:00Z",
  "players": {
    "you":       {"regions": {"LDN": {"running":4,"booting":[318,320],"demand_rps":2210,"cap_rps":2400,"dropped_rps":0}, "NJ": {...}, "TYO": {...}}, "served_pct": 97.4, "server_hours": 31.2, "breach_minutes": 6},
    "autopilot": {"served_pct": 99.1, "server_hours": 29.8, "breach_minutes": 1},
    "reactive":  {"served_pct": 93.0, "server_hours": 27.5, "breach_minutes": 22},
    "scheduled": {"served_pct": 95.2, "server_hours": 33.0, "breach_minutes": 12} },
  "radar": [ /* top 8 article forecasts issued ≤ now */ ],
  "alerts": [ {"level":"warn","text":"Surge likely on 'Typhoon' within 2 h, mostly Tokyo"} ] }
```
**Other WS messages:** `{"type":"end","review_url":"/api/sessions/abc/review"}` · `{"type":"error","message":"..."}`

**Review object:**
```json
{ "you": {"served_pct":96.1,"server_hours":41.0,"breach_minutes":19,"grade":"B"},
  "autopilot": {...}, "reactive": {...}, "scheduled": {...},
  "surges": [ {"title":"Typhoon","region":"TYO","onset":"...Z","needed_servers":9,
               "you":{"ready_at_onset":5,"verdict":"late","minutes_short":24},
               "autopilot":{"ready_at_onset":9,"verdict":"on_time"}} ] }
```
**Verdicts:** `brilliant` (enough servers ready at onset and the scale-up started while p_surge < 0.5) · `on_time` · `late` (enough within 30 min after onset) · `missed` · `overspent` (started ≥ 3 servers for a surge that never came).

**Scorecard object:**
```json
{ "since": "2026-10-24T14:00:00Z", "checked": 37, "hits": 21, "misses": 6, "false_alarms": 10,
  "median_lead_h": 1.4,
  "rows": [ {"issued_at":"...Z","wiki":"enwiki","title":"...","p_surge":0.82,"horizon_h":2,
             "outcome":"hit","actual_peak_views":154000,"verified_at":"...Z"} ] }
```
**Errors:** HTTP 4xx/5xx with `{"error":"snake_case_code","message":"human text"}`.

### 3.6 Mock files (PC2 creates on day one, in `web/public/mocks/`)
`levels.json`, `session.json`, `ticks.jsonl` (≥ 300 lines), `review.json`, `forecasts.json`, `scorecard.json`, `reasons.json`. The frontend switches to mocks with `?mock=1`.
