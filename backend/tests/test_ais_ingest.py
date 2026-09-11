import json

import pytest

from kotwica.config import Settings
from workers.ais_ingest import (Downsampler, Ingest, parse_location, parse_metadata,
                                parse_topic)

BBOX = Settings().BALTIC_BBOX

# Przykłady z dokumentacji Digitraffic
LOCATION = {"time": 1668075025, "sog": 10.7, "cog": 326.6, "navStat": 0, "rot": 0,
            "posAcc": True, "raim": False, "heading": 325, "lon": 20.345818, "lat": 60.03802}
METADATA = {"timestamp": 1668075026035, "destination": "UST LUGA", "name": "ARUNA CIHAN",
            "draught": 68, "eta": 733376, "posType": 15, "callSign": "V7WW7", "imo": 9543756,
            "type": 70, "refA": 160, "refB": 33, "refC": 20, "refD": 12}


def test_parse_location_docs_example():
    p = parse_location(230000001, LOCATION, BBOX)
    assert p.ts == 1668075025 and p.sog == 10.7 and p.cog == 326.6 and p.heading == 325
    assert p.nav_stat == 0
    # EPSG:3035 dla Bałtyku: x ~ 4.9e6, y ~ 4.1e6
    assert 4.5e6 < p.x < 5.5e6 and 3.8e6 < p.y < 4.5e6


@pytest.mark.parametrize("field,value", [("sog", 102.3), ("sog", 1023), ("cog", 360), ("cog", 3600),
                                         ("heading", 511), ("rot", -128), ("rot", 128)])
def test_not_available_values_become_none(field, value):
    p = parse_location(1, {**LOCATION, field: value}, BBOX)
    assert getattr(p, field) is None


def test_rot_kept_when_available():
    assert parse_location(1, {**LOCATION, "rot": -12}, BBOX).rot == -12
    assert parse_location(1, {**LOCATION, "rot": 127}, BBOX).rot == 127
    assert parse_location(1, LOCATION, BBOX).rot == 0


@pytest.mark.parametrize("lat,lon", [(91, 181), (40.0, 20.0), (60.0, 5.0)])
def test_invalid_or_outside_bbox_dropped(lat, lon):
    assert parse_location(1, {**LOCATION, "lat": lat, "lon": lon}, BBOX) is None


def test_parse_metadata_docs_example():
    m = parse_metadata(1, METADATA)
    assert m.ship_type == 70 and m.draught == pytest.approx(6.8) and m.imo == 9543756
    assert m.name == "ARUNA CIHAN" and m.call_sign == "V7WW7" and m.updated_at == 1668075026


def test_parse_metadata_zeros_are_none():
    m = parse_metadata(1, {**METADATA, "imo": 0, "type": 0, "draught": 0, "name": "@@@"})
    assert m.imo is None and m.ship_type is None and m.draught is None and m.name is None


def test_parse_topic():
    assert parse_topic("vessels-v2/230000001/location") == (230000001, "location")
    assert parse_topic("vessels-v2/230000001/locations") == (230000001, "location")
    assert parse_topic("vessels-v2/230000001/metadata") == (230000001, "metadata")
    assert parse_topic("vessels-v2/status") is None


def test_downsampler():
    d = Downsampler(30)
    assert d.accept(1, 100)
    assert not d.accept(1, 110)
    assert not d.accept(1, 90)      # starszy niż ostatni przyjęty
    assert d.accept(1, 130)
    assert d.accept(2, 110)         # inny statek, niezależnie


def test_ingest_batch_write(conn):
    ing = Ingest(conn, Settings())
    for i in range(10):             # co 10 s -> po downsamplingu 4 pingi (0, 30, 60, 90)
        ing.handle("vessels-v2/230000001/location",
                   json.dumps({**LOCATION, "time": LOCATION["time"] + 10 * i}).encode())
    ing.handle("vessels-v2/230000001/metadata", json.dumps(METADATA).encode())
    ing.handle("vessels-v2/230000001/metadata", json.dumps({**METADATA, "destination": "HELSINKI"}).encode())
    ing.handle("vessels-v2/230000001/location", b"not json")
    ing.flush(now=1668075100)

    assert conn.execute("SELECT count(*) FROM positions").fetchone()[0] == 4
    rows = conn.execute("SELECT destination FROM vessels").fetchall()
    assert [r[0] for r in rows] == ["HELSINKI"]
    assert conn.execute("SELECT value FROM meta WHERE key='pings_total'").fetchone()[0] == "4"

    # restart: licznik czytany z bazy, upsert nie dubluje statku
    ing2 = Ingest(conn, Settings())
    ing2.handle("vessels-v2/230000001/metadata", json.dumps(METADATA).encode())
    ing2.handle("vessels-v2/230000001/location",
                json.dumps({**LOCATION, "time": LOCATION["time"] + 500}).encode())
    ing2.flush(now=1668075600)
    assert conn.execute("SELECT count(*) FROM vessels").fetchone()[0] == 1
    assert ing2.pings_total == 5
