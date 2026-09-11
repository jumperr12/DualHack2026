import time

from fastapi.testclient import TestClient

from kotwica import db
from kotwica.config import settings


def test_health_reports_ais_worker(tmp_path, monkeypatch):
    path = str(tmp_path / "k.db")
    c = db.connect(path)
    db.init_schema(c)
    db.set_meta(c, "ais_last_write", int(time.time()) - 5)
    c.commit()
    monkeypatch.setattr(settings, "DB_PATH", path)

    from kotwica.api.main import app
    body = TestClient(app).get("/health").json()
    assert body["ok"] is True
    assert body["workers"]["ais-worker"]["age_s"] >= 5
