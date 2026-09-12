"""Tryb do tyłu (sekcja 9): czas + miejsce awarii -> ranking kandydatów."""

import pytest
from shapely.geometry import LineString

from kotwica import db
from kotwica.config import Settings
from kotwica.forensics import FaultQuery, ForensicLimit, analyse, save
from kotwica.geo import Asset, Zones, geom_to_3035
from tests.scenario import background_fleet, merge, track

T0 = 1_700_000_000
CABLE = LineString([(24.5, 60.0), (25.6, 60.0)])
FAULT_LON, FAULT_LAT = 25.40, 60.0          # punkt na kablu
FAULT_TS = T0 + 3 * 3600                    # awaria 3 h po starcie scenariusza
S = Settings(FORENSIC_TIMEOUT_S=60)         # w testach nie gonimy czasu


def zones():
    return Zones([Asset("Estlink 2", "cables", "power", geom_to_3035(CABLE))], S.ZONE_BUFFER_M)


def insert(conn, pings):
    conn.executemany(
        "INSERT INTO positions(mmsi, ts, lat, lon, x, y, sog, cog, heading, rot, nav_stat, is_replay) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(p.mmsi, p.ts, p.lat, p.lon, p.x, p.y, p.sog, p.cog, p.heading, p.rot, p.nav_stat,
          p.is_replay) for p in pings])
    conn.commit()


def culprit():
    """Tankowiec: 2 h marszu 11 kn, potem wleczenie 6 kn przez punkt awarii, potem ucieczka."""
    return track(999000001, T0, [
        dict(lon0=26.30, lat0=60.05, lon1=25.62, lat1=60.05, sog=11.0),
        dict(lon0=25.62, lat0=60.0, lon1=25.22, lat1=60.0, sog=6.0, hdg_offset=20.0),
        dict(lon0=25.22, lat0=60.0, lon1=25.0, lat1=60.2, sog=11.0),
    ])


def passerby():
    """Prom przecinający kabel z pełną prędkością 20 km od miejsca awarii."""
    return track(111, T0, [dict(lon0=25.02, lat0=59.8, lon1=25.02, lat1=60.2, sog=16.0)])


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "f.db"))
    db.init_schema(c)
    c.execute("INSERT INTO vessels(mmsi, name, ship_type) VALUES (999000001, 'EAGLE S', 80)")
    c.execute("INSERT INTO vessels(mmsi, name, ship_type) VALUES (111, 'FERRY', 60)")
    insert(c, merge(culprit(), passerby(), background_fleet(T0, 8, 25.4, 60.0, 300)))
    yield c
    c.close()


def test_culprit_is_ranked_first(conn):
    r = analyse(conn, FaultQuery(FAULT_LAT, FAULT_LON, FAULT_TS, asset="Estlink 2"), zones(), S)
    assert r.candidates, r.note
    top = r.candidates[0]
    assert top.mmsi == 999000001 and top.rank == 1
    assert top.min_dist_m < 500                      # przechodził przez punkt awarii
    assert abs(top.tca_ts - FAULT_TS) < 3 * 3600     # w oknie analizy
    assert top.crossed is True
    rules = {x.rule for x in top.reasons}
    assert "proximity" in rules and "crossed_asset" in rules
    assert {"slow_in_zone", "dwell"} <= rules        # punkty z tego samego silnika co produkcja
    assert r.runtime_ms < 60_000 and r.n_vessels >= 2


def test_ferry_is_ranked_below_or_absent(conn):
    r = analyse(conn, FaultQuery(FAULT_LAT, FAULT_LON, FAULT_TS, asset="Estlink 2"), zones(), S)
    by_mmsi = {c.mmsi: c for c in r.candidates}
    assert 999000001 in by_mmsi
    if 111 in by_mmsi:
        assert by_mmsi[111].score < by_mmsi[999000001].score


def test_empty_ranking_when_nothing_nearby(conn):
    """System ma prawo powiedzieć „nie wiem" (sekcja 9.5)."""
    r = analyse(conn, FaultQuery(59.0, 20.0, FAULT_TS, asset="Estlink 2"), zones(), S)
    assert r.candidates == []
    assert r.note and "brak wskazania" in r.note


def test_time_window_matters(conn):
    """Awaria dobę później: ten sam punkt, ale nikogo tam wtedy nie było."""
    r = analyse(conn, FaultQuery(FAULT_LAT, FAULT_LON, FAULT_TS + 86400, asset="Estlink 2"),
                zones(), S)
    assert r.candidates == [] and r.positions_scanned == 0


def test_post_event_behaviour_is_reported(conn):
    r = analyse(conn, FaultQuery(FAULT_LAT, FAULT_LON, FAULT_TS, asset="Estlink 2"), zones(), S)
    top = r.candidates[0]
    assert top.post_event and "SOG" in top.post_event


def test_row_limit_is_enforced(conn):
    tight = Settings(FORENSIC_MAX_ROWS=10, FORENSIC_TIMEOUT_S=60)
    with pytest.raises(ForensicLimit, match="zawęź"):
        analyse(conn, FaultQuery(FAULT_LAT, FAULT_LON, FAULT_TS, asset="Estlink 2"), zones(), tight)


def test_case_is_saved_with_candidates(conn):
    r = analyse(conn, FaultQuery(FAULT_LAT, FAULT_LON, FAULT_TS, asset="Estlink 2"), zones(), S)
    case_id = save(conn, r)
    row = conn.execute("SELECT * FROM forensic_cases WHERE id = ?", (case_id,)).fetchone()
    assert row["asset"] == "Estlink 2" and row["n_vessels"] == r.n_vessels
    cands = conn.execute("SELECT * FROM forensic_candidates WHERE case_id = ? ORDER BY rank",
                         (case_id,)).fetchall()
    assert [c["mmsi"] for c in cands] == [c.mmsi for c in r.candidates]
    assert cands[0]["reasons"].startswith("[")
