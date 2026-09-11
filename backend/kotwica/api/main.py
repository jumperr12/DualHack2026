"""API tylko do odczytu. W M0 wyłącznie /health; reszta endpointów w M1."""

import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from kotwica import db
from kotwica.config import settings

# worker -> klucz w `meta` z czasem ostatniego zapisu
WORKER_KEYS = {"ais-worker": "ais_last_write"}
STALE_S = 120

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
        return {"ok": all(w["ok"] for w in workers.values()), "now": now, "workers": workers}
    finally:
        conn.close()
