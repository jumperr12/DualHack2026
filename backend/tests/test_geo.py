import json

import pytest
from shapely.geometry import LineString, Point, mapping

from kotwica.geo import Asset, Zones, geom_to_3035, to_3035

# Kabel na południku 25°E, od 59.8°N do 60.2°N (Zatoka Fińska).
CABLE = LineString([(25.0, 59.8), (25.0, 60.2)])
OTHER = LineString([(26.0, 59.8), (26.0, 60.2)])


@pytest.fixture
def zones():
    return Zones([Asset("Estlink 2", "cables", "power", geom_to_3035(CABLE)),
                  Asset("C-Lion 1", "cables", "telecom", geom_to_3035(OTHER))], buffer_m=2000)


def xy(lon, lat):
    return to_3035(lon, lat)


def test_in_zone_and_zone_at(zones):
    assert zones.in_zone(*xy(25.0, 60.0))            # na kablu
    assert zones.zone_at(*xy(25.0, 60.0)) == "Estlink 2"
    assert zones.zone_at(*xy(26.0, 60.0)) == "C-Lion 1"
    assert not zones.in_zone(*xy(25.5, 60.0))        # w połowie drogi między kablami
    assert zones.zone_at(*xy(25.5, 60.0)) is None


def test_buffer_edge(zones):
    # 1 km od kabla: w strefie; 3 km: poza. 0.01° długości na 60°N to ok. 550 m.
    assert zones.in_zone(*xy(25.018, 60.0))
    assert not zones.in_zone(*xy(25.06, 60.0))


def test_distance_and_nearest(zones):
    d = zones.distance_to("Estlink 2", *xy(25.018, 60.0))
    assert 800 < d < 1200
    name, dist = zones.nearest_asset(*xy(25.3, 60.0))   # bliżej Estlink niż C-Lion
    assert name == "Estlink 2" and 14_000 < dist < 20_000


def test_crossings(zones):
    # odcinek z zachodu na wschód przez kabel 25°E
    assert zones.crossings(*xy(24.9, 60.0), *xy(25.1, 60.0)) == ["Estlink 2"]
    # odcinek wzdłuż kabla, nieprzecinający go
    assert zones.crossings(*xy(25.02, 59.9), *xy(25.02, 60.1)) == []
    # odcinek przez oba kable
    assert set(zones.crossings(*xy(24.5, 60.0), *xy(26.5, 60.0))) == {"Estlink 2", "C-Lion 1"}


def test_exclusions():
    port = Point(25.0, 60.0).buffer(0.02)   # poligon wokół punktu na kablu
    z = Zones([Asset("Estlink 2", "cables", "power", geom_to_3035(CABLE))], 2000,
              [geom_to_3035(port)])
    assert z.in_zone(*xy(25.0, 60.0))
    assert z.in_exclusion(*xy(25.0, 60.0))      # ten sam punkt jest w porcie
    assert not z.in_exclusion(*xy(25.0, 60.1))  # dalej wzdłuż kabla już nie


def test_from_static(tmp_path):
    (tmp_path / "cables.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Estlink 2", "kind": "power"},
         "geometry": mapping(CABLE)},
        {"type": "Feature", "properties": {"name": "punkt"},        # nie linia, pomijamy
         "geometry": mapping(Point(25.0, 60.0))}]}))
    (tmp_path / "exclusions.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Port"},
         "geometry": mapping(Point(25.0, 59.85).buffer(0.02))}]}))
    z = Zones.from_static(tmp_path, buffer_m=2000)
    assert [a.name for a in z.assets] == ["Estlink 2"]
    assert z.zone_at(*xy(25.0, 60.0)) == "Estlink 2"
    assert z.in_exclusion(*xy(25.0, 59.85))
    assert len(z.exclusions) == 1
