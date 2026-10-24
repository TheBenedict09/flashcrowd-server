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
| Capacity C (req/s) | |
| RTT LDN–NJ / NJ–TYO / LDN–TYO (ms) | / / |

## fc-core (AWS, London)
- Instance `fc-core`, t3.medium, Elastic IP **18.132.176.215**. SSH: `ssh -i secrets/fc_ssh ubuntu@18.132.176.215`
- Services: `recorder` (live), `backfill@<since>_<until>` (5 chunks), `load-edits.timer` (every 5 min → Snowflake `EDITS`; read `EDITS_CLEAN`).
- Deploy code: `rsync -az -e 'ssh -i secrets/fc_ssh' --exclude .venv --exclude .git --exclude 'secrets/fc_ssh*' ./ ubuntu@18.132.176.215:~/flashcrowd-server/`
