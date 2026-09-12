"""API: odczyt z bazy. Wyjątki (forensyka, pakiet dowodowy) dojdą w M2/M3 z limitami z config."""

import asyncio
import json
import time
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from kotwica import db, forensics
from kotwica.config import settings
from kotwica.detector.signature import angular_diff, median
from kotwica.geo import Zones, to_3035

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
                   v.name, v.ship_type, s.score, s.level, s.zone, s.category
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


def _alert_row(r) -> dict:
    d = {k: r[k] for k in r.keys()}
    d["reasons"] = json.loads(r["reasons"] or "[]")
    return d


@app.get("/alerts")
def alerts(status: str | None = "open", since: int | None = None, limit: int = 200,
           category: str | None = None):
    """Zwraca też kategorię `whitelisted_activity` (holowniki, piloty, służby przy infrastrukturze).
    Frontend pokazuje je osobno, żeby główna lista została czysta, ale nic nie znika."""
    sql = "SELECT * FROM alerts WHERE 1 = 1"
    args: list = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if category:
        sql += " AND category = ?"
        args.append(category)
    if since:
        sql += " AND ts_last > ?"
        args.append(since)
    sql += " ORDER BY score DESC, ts_last DESC LIMIT ?"
    args.append(limit)
    conn = _conn()
    try:
        return [_alert_row(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@app.get("/alerts/stream")
async def alerts_stream(request: Request):
    """SSE: nowe i zaktualizowane alerty. Odpytujemy bazę, bo workery piszą do niej niezależnie."""
    async def gen():
        last_seen = int(time.time())
        while True:
            if await request.is_disconnected():
                break
            conn = _conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE ts_last > ? ORDER BY ts_last", (last_seen,)
                ).fetchall()
            finally:
                conn.close()
            for r in rows:
                last_seen = max(last_seen, r["ts_last"])
                yield {"event": "alert", "data": json.dumps(_alert_row(r), ensure_ascii=False)}
            await asyncio.sleep(5)
    return EventSourceResponse(gen())


@app.get("/vessels/{mmsi}/signature")
def signature(mmsi: int, ts: int | None = None, window: int = 3600):
    """Szereg do wykresu w szufladzie statku: SOG, rozjazd dziób/kurs i mediana floty w tle."""
    conn = _conn()
    try:
        end = ts or conn.execute("SELECT max(ts) FROM positions WHERE mmsi = ?",
                                 (mmsi,)).fetchone()[0]
        if end is None:
            raise HTTPException(404, "unknown mmsi")
        start = end - window
        rows = conn.execute(
            "SELECT ts, x, y, sog, cog, heading FROM positions "
            "WHERE mmsi = ? AND ts BETWEEN ? AND ? ORDER BY ts", (mmsi, start, end)).fetchall()
        # mediana floty liczona z pozycji innych statków w tym samym oknie i promieniu
        others = conn.execute(
            "SELECT ts, x, y, sog, cog, heading FROM positions "
            "WHERE mmsi != ? AND ts BETWEEN ? AND ? AND sog > ? AND heading IS NOT NULL "
            "AND cog IS NOT NULL", (mmsi, start, end, settings.SIG_SOG_MIN)).fetchall()
    finally:
        conn.close()

    radius2 = settings.FLEET_RADIUS_M ** 2
    bucket = settings.FLEET_BUCKET_MIN * 60
    series, with_hdg = [], 0
    for r in rows:
        delta = None
        if r["heading"] is not None and r["cog"] is not None and (r["sog"] or 0) >= settings.SIG_SOG_MIN:
            delta = angular_diff(r["heading"], r["cog"])
        if r["heading"] is not None:
            with_hdg += 1
        near = [angular_diff(o["heading"], o["cog"]) for o in others
                if abs(o["ts"] - r["ts"]) <= bucket
                and (o["x"] - r["x"]) ** 2 + (o["y"] - r["y"]) ** 2 <= radius2]
        series.append({"ts": r["ts"], "sog": r["sog"], "delta": delta,
                       "fleet_median": round(median(near), 1) if len(near) >= settings.FLEET_MIN_N else None,
                       "fleet_n": len(near)})
    return {"mmsi": mmsi, "window": window,
            "hdg_coverage": round(with_hdg / len(rows), 3) if rows else 0.0,
            "series": series}


@lru_cache(maxsize=1)
def _zones() -> Zones:
    return Zones.from_static(settings.STATIC_DIR, settings.ZONE_BUFFER_M)


@lru_cache(maxsize=1)
def _ensure_schema() -> bool:
    """Tabele forensyki tworzy API, bo to jedyny pisarz `forensic_*`. Raz na proces."""
    conn = db.connect(settings.DB_PATH)
    try:
        db.init_schema(conn)
    finally:
        conn.close()
    return True


class FaultRequest(BaseModel):
    lat: float
    lon: float
    fault_ts: int
    asset: str | None = None
    radius_m: float | None = None
    win_back_s: int | None = None
    win_fwd_s: int | None = None


@app.post("/forensics")
def post_forensics(req: FaultRequest):
    """Tryb do tyłu. Świadomy wyjątek od zasady „API tylko czyta" — z limitami z sekcji 9."""
    _ensure_schema()
    zones = _zones()
    asset = req.asset
    if not asset:                       # klik na mapie: sami wskazujemy najbliższy obiekt
        nearest = zones.nearest_asset(*to_3035(req.lon, req.lat))
        asset = nearest[0] if nearest else None
    query = forensics.FaultQuery(lat=req.lat, lon=req.lon, fault_ts=req.fault_ts, asset=asset,
                                 radius_m=req.radius_m, win_back_s=req.win_back_s,
                                 win_fwd_s=req.win_fwd_s)
    conn = db.connect(settings.DB_PATH)
    try:
        result = forensics.analyse(conn, query, zones, settings)
        forensics.save(conn, result)
    except forensics.ForensicLimit as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()
    return result.as_dict()


@app.get("/forensics/{case_id}")
def get_forensics(case_id: int):
    conn = _conn()
    try:
        case = conn.execute("SELECT * FROM forensic_cases WHERE id = ?", (case_id,)).fetchone()
        if case is None:
            raise HTTPException(404, "unknown case")
        cands = conn.execute("SELECT * FROM forensic_candidates WHERE case_id = ? ORDER BY rank",
                             (case_id,)).fetchall()
        names = {r["mmsi"]: (r["name"], r["ship_type"]) for r in
                 conn.execute("SELECT mmsi, name, ship_type FROM vessels")}
    finally:
        conn.close()
    out = {k: case[k] for k in case.keys()}
    out["candidates"] = []
    for c in cands:
        d = {k: c[k] for k in c.keys() if k != "case_id"}
        d["reasons"] = json.loads(c["reasons"] or "[]")
        d["name"], d["ship_type"] = names.get(c["mmsi"], (None, None))
        out["candidates"].append(d)
    return out


# Po `vite build` frontend leży w frontend/dist i API serwuje go pod / (bez CORS, bez drugiego serwera).
_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
