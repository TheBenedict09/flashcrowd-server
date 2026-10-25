# flashcrowd-server (PC1 — Cloud + Backend)

FLASHCROWD predicts Wikipedia traffic surges 1–3 h ahead and decides how many servers to start in London, New Jersey and Tokyo. This repo holds the data pipeline (edit recorder, page-view loader, Snowflake), infra measurements (AWS EC2 boot/RTT/capacity) and the FastAPI + WebSocket server.

Contracts: see `CONTRACTS.md` (version 1.1). Never build against another repo's half-finished code.

## Run locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in
uvicorn api.main:app --reload --port 8000   # GET /api/health
```

## Layout
- `sql/ddl.sql` — Snowflake objects (run once)
- `pipeline/` — recorder, backfill, loaders
- `infra/` — EC2 boot bench, RTT probe, load test
- `api/` — FastAPI app
- `deploy/` — systemd units, nginx

## Numbers (fill in)
| Number | Value |
|-|-|
| Edit backfill start date | 2026-10-17 06:55 UTC (EventStreams retention ≈ 7 days) |
| Days of page views loaded | 10 Oct 00:00 → 24 Oct 12:00 UTC (loading; then every 15 min). Published ≈ 2 h 11 min after the hour (min 2:00, max 2:54) |
| Boot median LDN / NJ / TYO (min) | 0.87 / 0.83 / 1.18 (EC2 t3.micro, Ubuntu 24.04, launch → nginx serving; 5 runs each) |
| Capacity C (req/s) | 254 (t3.micro, nginx static 558 KB page, p95<200 ms; network-bound — see infra/loadtest.md) |
| RTT LDN–NJ / NJ–TYO / LDN–TYO (ms) | 75.5 / 147.7 / 215.3 (TCP connect, first medians; probes run all weekend) |

## API (live)
- **Game sessions are real**: PC3's `flashcrowd_engine.GameSession` (v0.3) over `api/levels/*.json`, built by `api/build_levels.py` from Snowflake (real page views of tracked articles, measured B/C, SCALE via `calibrate_scale`). Rebuild: `python api/build_levels.py --n 3` then restart `api`.
- Boss levels (clean data, PC3 model forecasts as-of; all inside the training window = in-sample):
  `oct-22-morning-ldn` (John Anderson), `oct-23-evening-tyo` (Jacob Misiorowski), `oct-21-afternoon-nj` (BioWare).
  Autopilot vs reactive server-hours at 100% served: 85.5 vs 95.0 · 98.2 vs 116.4 · 123.4 vs 134.7 (-8% to -16%).
- Frontend: PC2's `flashcrowd-web` built with `VITE_SYNTHETIC_DATA=false`, served from `/var/www/flashcrowd` → **http://18.132.176.215/**

## PC3 model on fc-core (`~/flashcrowd-model`, run by PC1)
- Trained on real Snowflake data (`configs/train_short.json`, window 17 Oct 10:00 → 24 Oct 12:00, test = 24 Oct).
  Held-out PR-AUC (surge within 2 h) **0.067 vs persistence 0.006**; median lead 2.0 h; onset recall 0.09.
- Timers: `fc-live` (forecasts every 15 min → FORECASTS/REGION_FORECASTS), `fc-scorecard` (genuine live forecasts from 20:00 UTC).
- Incident 24 Oct ~20:10 BST: 5 page-view hours (24 Oct 07–11 UTC) had been loaded twice by an overlap of the backfill and
  the 15-min job. Fixed (dedup + idempotent per-hour load + flock), model retrained, forecasts and levels regenerated.
  The contaminated run is kept in `~/flashcrowd-model/artifacts_contaminated_dup_pv/`.

## API (mock pieces)
- Base URL for PC2: **`http://18.132.176.215/api`** — WS: `ws://18.132.176.215/api/sessions/{id}/stream`
- `MOCK=1` serves `api/mocks/` (PC2's `levels.json` + `ticks.jsonl`; the other 5 are contract-shaped placeholders). Smoke test: `python scripts/api_smoke_test.py`
- nginx: `/` → `/var/www/flashcrowd` (frontend), `/api` + WS → uvicorn :8000, rate limit 20 r/s per IP.

## fc-core (AWS, London)
- Instance `fc-core`, t3.medium, Elastic IP **18.132.176.215**. SSH: `ssh -i secrets/fc_ssh ubuntu@18.132.176.215`
- RTT probes: `fc-rtt-LDN/NJ/TYO` (t3.nano) → `rtt-collect.timer` every 5 min (`infra/rtt_probe.py summary`).
- Services: `recorder` (live), `backfill@<since>_<until>` (5 chunks), `load-edits.timer` (every 5 min → Snowflake `EDITS`; read `EDITS_CLEAN`).
- Deploy code: `rsync -az -e 'ssh -i secrets/fc_ssh' --exclude .venv --exclude .git --exclude 'secrets/fc_ssh*' ./ ubuntu@18.132.176.215:~/flashcrowd-server/`
# flashcrowd-arcade
