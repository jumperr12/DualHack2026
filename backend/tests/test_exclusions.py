from shapely.geometry import shape

from kotwica.config import Settings
from scripts.fetch_osm_exclusions import to_feature_collection
from scripts.load_static import layer_from_filename, process_features

BBOX = Settings().BALTIC_BBOX


def test_overpass_elements_to_geojson():
    elements = [
        {"type": "node", "id": 1, "lat": 60.1, "lon": 25.0,
         "tags": {"seamark:type": "harbour", "name": "Vuosaari"}},
        {"type": "way", "id": 2, "tags": {"seamark:type": "anchorage", "seamark:name": "A1"},
         "geometry": [{"lat": 60.0, "lon": 25.0}, {"lat": 60.0, "lon": 25.1},
                      {"lat": 60.1, "lon": 25.1}, {"lat": 60.0, "lon": 25.0}]},
        {"type": "way", "id": 3, "tags": {"harbour": "yes"},
         "geometry": [{"lat": 60.0, "lon": 25.0}, {"lat": 60.0, "lon": 25.1}]},   # otwarta linia
        {"type": "relation", "id": 4, "tags": {"landuse": "port"}, "members": [
            {"type": "way", "role": "outer", "geometry": [
                {"lat": 59.0, "lon": 24.0}, {"lat": 59.0, "lon": 24.1},
                {"lat": 59.1, "lon": 24.1}, {"lat": 59.0, "lon": 24.0}]}]},
        {"type": "node", "id": 5, "lat": 60.1, "lon": 25.0},   # bez tagów
    ]
    fc = to_feature_collection(elements)
    types = [f["geometry"]["type"] for f in fc["features"]]
    kinds = [f["properties"]["kind"] for f in fc["features"]]
    assert types == ["Point", "Polygon", "LineString", "MultiPolygon", "Point"]
    assert kinds == ["harbour", "anchorage", "harbour", "harbour", "harbour"]
    assert fc["features"][0]["properties"]["name"] == "Vuosaari"


def test_exclusion_points_and_lines_get_buffered_polygons():
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "harbour", "name": "P"},
         "geometry": {"type": "Point", "coordinates": [25.0, 60.0]}},
        {"type": "Feature", "properties": {"kind": "anchorage", "name": "A"},
         "geometry": {"type": "LineString", "coordinates": [[25.0, 60.0], [25.05, 60.0]]}},
        {"type": "Feature", "properties": {"kind": "anchorage", "name": "B"},
         "geometry": {"type": "Polygon", "coordinates": [[[25.0, 60.0], [25.1, 60.0], [25.1, 60.1], [25.0, 60.0]]]}},
    ]}
    out = process_features(fc, "exclusions", BBOX)
    assert [f["geometry"]["type"] for f in out] == ["Polygon", "Polygon", "Polygon"]
    assert [f["properties"]["kind"] for f in out] == ["harbour", "anchorage", "anchorage"]
    # bufor 2 km wokół punktu: szerokość ok. 4 km, czyli ok. 0.07° długości na 60°N
    minx, miny, maxx, maxy = shape(out[0]["geometry"]).bounds
    assert 0.06 < maxx - minx < 0.09 and 0.03 < maxy - miny < 0.04
    # gotowy poligon też dostaje margines: statek przy nabrzeżu jest tuż poza obrysem portu
    original = shape(fc["features"][2]["geometry"])
    widened = shape(out[2]["geometry"])
    assert widened.contains(original) and widened.area > original.area


def test_layer_detection_for_exclusions():
    from pathlib import Path
    assert layer_from_filename(Path("osm_exclusions.geojson")) == "exclusions"
    assert layer_from_filename(Path("anchorages_manual.geojson")) == "exclusions"
