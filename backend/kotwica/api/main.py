"""API: odczyt z bazy. Wyjątki (forensyka, pakiet dowodowy) dojdą w M2/M3 z limitami z config."""

import json
import time
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from kotwica import db
from kotwica.config import settings

# worker -> klucz w `meta` z czasem ostatniego zapisu
WORKER_KEYS = {"ais-worker": "ais_last_write", "detector": "detector_last_run"}
STALE_S = 120
STATIC_LAYERS = ("cables", "pipelines", "windfarms", "platforms", "exclusions")
VESSELS_WINDOW_S = 30 * 60   # statek bez pingu dłużej niż to znika z mapy

app = FastAPI(title="Kotwica API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in settings.FRONTEND_ORIGIN.split(",") if o],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _conn():
    return db.connect(settings.DB_PATH, readonly=True)


@app.get("/health")
def health():
    now = int(time.time())
    conn = _conn()
    try:
        workers = {}
        for name, key in WORKER_KEYS.items():
            last = db.get_meta(conn, key)
            age = now - int(last) if last else None
            workers[name] = {"last_write": int(last) if last else None, "age_s": age,
                             "ok": age is not None and age < STALE_S}
        return {"ok": workers["ais-worker"]["ok"], "now": now, "workers": workers}
    finally:
        conn.close()


@lru_cache(maxsize=1)
def _infrastructure() -> dict:
    """Wszystkie warstwy statyczne w jednej FeatureCollection; `layer` w properties mówi która."""
    feats = []
    for layer in STATIC_LAYERS:
        path = Path(settings.STATIC_DIR) / f"{layer}.geojson"
        if not path.exists():
            continue
        for f in json.loads(path.read_text(encoding="utf-8")).get("features", []):
            f.setdefault("properties", {})["layer"] = layer
            feats.append(f)
    return {"type": "FeatureCollection", "features": feats}


@app.get("/infrastructure")
def infrastructure():
    return _infrastructure()


def _parse_bbox(bbox: str | None):
    if not bbox:
        return None
    try:
        min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat")
    return min_lon, min_lat, max_lon, max_lat


@app.get("/vessels")
def vessels(bbox: str | None = Query(None), replay: bool = True):
    """Ostatnia pozycja każdego statku z ostatnich 30 min + score/level z vessel_state (jeśli detektor działa)."""
    now_db = None
    b = _parse_bbox(bbox)
    conn = _conn()
    try:
        now_db = conn.execute("SELECT max(ts) FROM positions").fetchone()[0] or 0
        sql = """
            SELECT p.mmsi, p.ts, p.lat, p.lon, p.sog, p.cog, p.heading, p.nav_stat, p.is_replay,
                   v.name, v.ship_type, s.score, s.level, s.zone
            FROM positions p
            JOIN (SELECT mmsi, max(ts) AS mts FROM positions WHERE ts > ? GROUP BY mmsi) m
                 ON p.mmsi = m.mmsi AND p.ts = m.mts
            LEFT JOIN vessels v ON v.mmsi = p.mmsi
            LEFT JOIN vessel_state s ON s.mmsi = p.mmsi
            WHERE 1 = 1
        """
        args: list = [now_db - VESSELS_WINDOW_S]
        if b:
            sql += " AND p.lon BETWEEN ? AND ? AND p.lat BETWEEN ? AND ?"
            args += [b[0], b[2], b[1], b[3]]
        if not replay:
            sql += " AND p.is_replay = 0"
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    feats = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
        "properties": {k: r[k] for k in r.keys() if k not in ("lat", "lon")},
    } for r in rows]
    return {"type": "FeatureCollection", "features": feats, "ts": now_db}


@app.get("/vessels/{mmsi}/track")
def track(mmsi: int, hours: float = 6):
    conn = _conn()
    try:
        last = conn.execute("SELECT max(ts) FROM positions WHERE mmsi = ?", (mmsi,)).fetchone()[0]
        if last is None:
            raise HTTPException(404, "unknown mmsi")
        rows = conn.execute(
            "SELECT ts, lat, lon, sog, cog, heading, rot, nav_stat FROM positions "
            "WHERE mmsi = ? AND ts > ? ORDER BY ts", (mmsi, last - int(hours * 3600))).fetchall()
    finally:
        conn.close()
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[r["lon"], r["lat"]] for r in rows]},
        "properties": {"mmsi": mmsi, "n": len(rows),
                       "pings": [{k: r[k] for k in r.keys() if k not in ("lat", "lon")} for r in rows]},
    }


# Po `vite build` frontend leży w frontend/dist i API serwuje go pod / (bez CORS, bez drugiego serwera).
_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
