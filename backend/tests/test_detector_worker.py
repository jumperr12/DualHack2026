"""Worker detektora: kursor po rowid, zapis alertów i vessel_state, odporność na restart."""

import json

from shapely.geometry import LineString, mapping

from kotwica import db
from kotwica.config import Settings
from tests.scenario import background_fleet, merge, track
from workers import detector as W

T0 = 1_700_000_000
CABLE = LineString([(24.5, 60.0), (25.6, 60.0)])


def make_static(tmp_path):
    (tmp_path / "cables.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Estlink 2", "kind": "power"},
         "geometry": mapping(CABLE)}]}))
    return tmp_path


def settings_for(tmp_path, db_path):
    return Settings(DB_PATH=str(db_path), STATIC_DIR=str(make_static(tmp_path)))


def insert(conn, pings):
    conn.executemany(
        "INSERT INTO positions(mmsi, ts, lat, lon, x, y, sog, cog, heading, rot, nav_stat, is_replay) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(p.mmsi, p.ts, p.lat, p.lon, p.x, p.y, p.sog, p.cog, p.heading, p.rot, p.nav_stat,
          p.is_replay) for p in pings])
    conn.commit()


def eagle_s():
    return merge(track(999000001, T0, [
        dict(lon0=26.30, lat0=60.05, lon1=25.62, lat1=60.05, sog=11.0),
        dict(lon0=25.62, lat0=60.0, lon1=25.22, lat1=60.0, sog=6.0, hdg_offset=20.0),
    ]), background_fleet(T0, 8, lon=25.4, lat=60.0, minutes=300))


def test_cycle_writes_alert_and_vessel_state(tmp_path):
    path = tmp_path / "k.db"
    conn = db.connect(str(path))
    db.init_schema(conn)
    conn.execute("INSERT INTO vessels(mmsi, name, ship_type) VALUES (999000001, 'EAGLE S', 80)")
    insert(conn, eagle_s())

    w = W.DetectorWorker(conn, settings_for(tmp_path, path))
    w.cycle()

    alerts = conn.execute("SELECT * FROM alerts").fetchall()
    assert len(alerts) == 1
    a = alerts[0]
    assert a["mmsi"] == 999000001 and a["asset"] == "Estlink 2" and a["level"] == "alarm"
    assert {r["rule"] for r in json.loads(a["reasons"])} >= {"slow_in_zone", "dwell", "speed_drop"}

    state = conn.execute("SELECT * FROM vessel_state WHERE mmsi = 999000001").fetchone()
    assert state["score"] == a["score"] and state["level"] == "alarm"
    assert conn.execute("SELECT count(*) FROM vessel_state").fetchone()[0] == 9   # + flota


def test_cursor_is_rowid_and_survives_restart(tmp_path):
    """Drugi przebieg nie może przetworzyć tych samych pingów ani zdublować alertu."""
    path = tmp_path / "k.db"
    conn = db.connect(str(path))
    db.init_schema(conn)
    conn.execute("INSERT INTO vessels(mmsi, ship_type) VALUES (999000001, 80)")
    pings = eagle_s()
    insert(conn, pings[:len(pings) // 2])
    s = settings_for(tmp_path, path)

    w = W.DetectorWorker(conn, s)
    assert w.cycle() > 0
    cursor = int(db.get_meta(conn, W.CURSOR_KEY))
    assert w.cycle() == 0                      # nic nowego
    assert int(db.get_meta(conn, W.CURSOR_KEY)) == cursor

    # restart workera na tej samej bazie + reszta pingów
    insert(conn, pings[len(pings) // 2:])
    w2 = W.DetectorWorker(conn, s)
    assert w2.alert_ids, "otwarty alert nie wrócił po restarcie"
    w2.cycle()
    assert conn.execute("SELECT count(*) FROM alerts").fetchone()[0] == 1


def test_replay_pings_do_not_move_the_cursor_past_live_ones(tmp_path):
    """Pingi replayu mają czas scenariusza; kursor po rowid nie gubi przez to żywych pingów."""
    path = tmp_path / "k.db"
    conn = db.connect(str(path))
    db.init_schema(conn)
    s = settings_for(tmp_path, path)
    future = [p for p in track(1, T0 + 10 * 86400,
                               [dict(lon0=25.5, lat0=60.0, lon1=25.4, lat1=60.0, sog=5.0)])]
    for p in future:
        p.is_replay = 1
    insert(conn, future)                                    # najpierw „przyszłość" z replayu
    live = track(2, T0, [dict(lon0=25.5, lat0=60.0, lon1=25.3, lat1=60.0, sog=5.0)])
    insert(conn, live)                                      # potem zwykłe pingi z przeszłości

    w = W.DetectorWorker(conn, s)
    db.set_meta(conn, W.CURSOR_KEY, 0)                      # licz od zera, jak przy pierwszym starcie
    conn.commit()
    w.detector.states.clear()
    w.cycle()
    seen = {r[0] for r in conn.execute("SELECT DISTINCT mmsi FROM vessel_state")}
    assert seen == {1, 2}, "kursor po rowid musi objąć oba strumienie"
