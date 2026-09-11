# DualHack2026 — Kotwica
3 person project based on Sentinel imagery and AIS transponders to act as a warning system for important infrastructure in the Baltic Sea. Full spec: [CLAUDE.md](CLAUDE.md).

## Local dev

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pytest
.venv/Scripts/python -m workers.ais_ingest       # records live AIS to ../data/kotwica.db
.venv/Scripts/python -m uvicorn kotwica.api.main:app --reload   # GET /health
.venv/Scripts/python scripts/load_static.py      # data/static/raw/*.geojson -> data/static/*.geojson
```

## Deploy (VM with Docker)

```bash
cp .env.example .env    # fill DOMAIN, ADMIN_TOKEN, ...; DNS api.<DOMAIN> -> VM
docker compose up -d --build
curl https://api.<DOMAIN>/health
```
