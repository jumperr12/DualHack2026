import json
import time

import pytest
from fastapi.testclient import TestClient

from kotwica import db
from kotwica.config import settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "k.db")
    c = db.connect(path)
    db.init_schema(c)
    now = int(time.time())
    db.set_meta(c, "ais_last_write", now - 5)
    c.executemany(
        "INSERT INTO positions(mmsi, ts, lat, lon, x, y, sog, cog, heading, rot, nav_stat, is_replay) "
        "VALUES (?, ?, ?, ?, 0, 0, ?, 90, 90, 0, 0, ?)",
        [(1, now - 600, 60.0, 25.0, 10.0, 0), (1, now - 300, 60.0, 25.1, 11.0, 0),   # statek 1, 2 pingi
         (2, now - 100, 59.0, 24.0, 0.0, 0),                                          # statek 2
         (3, now - 7200, 60.5, 25.5, 5.0, 0),                                         # stary, poza oknem
         (999000001, now - 50, 60.2, 25.2, 6.0, 1)])                                  # replay
    c.execute("INSERT INTO vessels(mmsi, name, ship_type) VALUES (1, 'TEST', 70)")
    c.execute("INSERT INTO vessel_state(mmsi, score, level) VALUES (1, 65, 'watch')")
    c.commit()
    static = tmp_path / "static"
    static.mkdir()
    (static / "cables.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Estlink 2"},
         "geometry": {"type": "LineString", "coordinates": [[25, 59.5], [25, 60.5]]}}]}))
    monkeypatch.setattr(settings, "DB_PATH", path)
    monkeypatch.setattr(settings, "STATIC_DIR", str(static))
    from kotwica.api import main
    main._infrastructure.cache_clear()
    return TestClient(main.app)


def test_health(client):
    body = client.get("/health").json()
    assert body["ok"] is True and body["workers"]["ais-worker"]["age_s"] >= 5
    assert body["workers"]["detector"]["ok"] is False


def test_infrastructure_merges_layers(client):
    fc = client.get("/infrastructure").json()
    assert [f["properties"]["layer"] for f in fc["features"]] == ["cables"]


def test_vessels_latest_per_mmsi_with_state(client):
    fc = client.get("/vessels").json()
    by = {f["properties"]["mmsi"]: f for f in fc["features"]}
    assert set(by) == {1, 2, 999000001}          # 3 jest za stary
    assert by[1]["properties"]["sog"] == 11.0    # najnowszy ping
    assert by[1]["properties"]["name"] == "TEST" and by[1]["properties"]["level"] == "watch"
    assert by[2]["properties"]["level"] is None

    fc = client.get("/vessels?bbox=24.5,59.5,25.5,60.5&replay=false").json()
    assert [f["properties"]["mmsi"] for f in fc["features"]] == [1]
    assert client.get("/vessels?bbox=nope").status_code == 400


def test_track(client):
    t = client.get("/vessels/1/track?hours=1").json()
    assert t["geometry"]["coordinates"] == [[25.0, 60.0], [25.1, 60.0]]
    assert t["properties"]["n"] == 2
    assert client.get("/vessels/42/track").status_code == 404


def test_alerts_endpoint(client, tmp_path):
    from kotwica import db
    from kotwica.config import settings
    c = db.connect(settings.DB_PATH)
    c.execute("INSERT INTO alerts(mmsi, asset, category, level, score, reasons, ts_start, ts_last, "
              "status, is_replay) VALUES (1, 'Estlink 2', 'suspicious', 'alarm', 95, ?, 10, 20, 'open', 0)",
              ('[{"rule":"slow_in_zone","points":40,"detail":"SOG 5.8 kn"}]',))
    c.execute("INSERT INTO alerts(mmsi, asset, category, level, score, reasons, ts_start, ts_last, "
              "status, is_replay) VALUES (2, 'C-Lion 1', 'suspicious', 'watch', 55, '[]', 5, 8, 'closed', 0)")
    c.commit()

    open_alerts = client.get("/alerts").json()
    assert [a["mmsi"] for a in open_alerts] == [1]
    assert open_alerts[0]["reasons"][0]["rule"] == "slow_in_zone"      # JSON, nie tekst
    assert [a["mmsi"] for a in client.get("/alerts?status=closed").json()] == [2]
    assert client.get("/alerts?status=open&since=100").json() == []


def test_signature_endpoint(client):
    body = client.get("/vessels/1/signature?window=3600").json()
    assert body["mmsi"] == 1 and len(body["series"]) == 2
    assert body["series"][0]["delta"] == 0.0        # cog 90, heading 90 w danych testowych
    assert body["hdg_coverage"] == 1.0
    assert client.get("/vessels/424242/signature").status_code == 404
