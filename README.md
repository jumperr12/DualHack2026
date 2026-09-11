# DualHack2026 — Kotwica
Every cable has a witness. AIS-based reconstruction and attribution of undersea cable damage in the Baltic.
Full spec: [CLAUDE.md](CLAUDE.md).

## Local dev

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pytest
.venv/Scripts/python -m workers.ais_ingest       # records live AIS to ../data/kotwica.db
.venv/Scripts/python -m uvicorn kotwica.api.main:app --reload   # http://localhost:8000/health
.venv/Scripts/python scripts/fetch_osm_exclusions.py   # anchorages + harbours from OSM -> data/static/raw/
.venv/Scripts/python scripts/load_static.py      # data/static/raw/*.geojson -> data/static/*.geojson
```

## Docker (same thing, all on one host)

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8000/health
```
