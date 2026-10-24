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
| Edit backfill start date | |
| Days of page views loaded | |
| Boot median LDN / NJ / TYO (min) | / / |
| Capacity C (req/s) | |
| RTT LDN–NJ / NJ–TYO / LDN–TYO (ms) | / / |
