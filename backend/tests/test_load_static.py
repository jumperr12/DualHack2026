from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import shape

from kotwica.config import Settings
from scripts.load_static import layer_from_filename, pick_name, process_features

BBOX = Settings().BALTIC_BBOX


def line(coords, **props):
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "LineString", "coordinates": coords}}


def dense_line(lon0, lat0, lon1, lat1, n=200):
    return [[float(x), float(y)] for x, y in zip(np.linspace(lon0, lon1, n), np.linspace(lat0, lat1, n))]


def test_clip_simplify_and_names():
    fc = {"type": "FeatureCollection", "features": [
        line(dense_line(24.5, 59.5, 25.5, 60.2), NAME="Estlink 2"),     # w całości w bbox
        line(dense_line(5.0, 57.0, 12.0, 57.0), cable_name="Skagerrak"),  # częściowo poza
        line([[0.0, 40.0], [1.0, 41.0]], name="Poza"),                   # całkiem poza
        line(dense_line(20.0, 58.0, 21.0, 58.0)),                        # bez nazwy
    ]}
    out = process_features(fc, "cables", BBOX, kind="power")
    names = [f["properties"]["name"] for f in out]
    assert names == ["Estlink 2", "Skagerrak", "cables-3"]
    assert all(f["properties"]["kind"] == "power" for f in out)

    clipped = shape(out[1]["geometry"])
    assert clipped.bounds[0] >= BBOX[0] - 1e-9
    # prosta linia z 200 punktów upraszcza się do kilku
    assert len(shape(out[0]["geometry"]).coords) < 10


def test_swapped_axes_detected():
    fc = {"type": "FeatureCollection", "features": [
        line([[59.5, 24.5], [60.2, 25.5]], name="Swapped")]}
    out = process_features(fc, "cables", BBOX)
    x0, y0 = shape(out[0]["geometry"]).coords[0]
    assert 9 <= x0 <= 31 and 53 <= y0 <= 66


def test_projected_crs_reprojected():
    from kotwica.geo import to_3035_many
    xs, ys = to_3035_many(np.array([24.5, 25.5]), np.array([59.5, 60.2]))
    fc = {"type": "FeatureCollection",
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3035"}},
          "features": [line([[xs[0], ys[0]], [xs[1], ys[1]]], name="Projected")]}
    out = process_features(fc, "cables", BBOX)
    (x0, y0), _ = shape(out[0]["geometry"]).coords
    assert abs(x0 - 24.5) < 1e-4 and abs(y0 - 59.5) < 1e-4


def test_layer_from_filename_and_pick_name():
    assert layer_from_filename(Path("EMODnet_HA_Energy_Cables.geojson")) == "cables"
    assert layer_from_filename(Path("pipelines.geojson")) == "pipelines"
    assert layer_from_filename(Path("windfarmspoly.geojson")) == "windfarms"
    assert layer_from_filename(Path("other.geojson")) is None
    assert pick_name({"Name": " Balticconnector "}) == "Balticconnector"
    assert pick_name({"id": 3}) is None
