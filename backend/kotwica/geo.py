"""Projekcje EPSG:4326 <-> EPSG:3035. Wszystkie odległości i bufory liczymy w metrach (3035)."""

from functools import lru_cache

from pyproj import Transformer


@lru_cache(maxsize=None)
def _transformer(src: int, dst: int) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def to_3035(lon: float, lat: float) -> tuple[float, float]:
    return _transformer(4326, 3035).transform(lon, lat)


def to_3035_many(lons, lats):
    return _transformer(4326, 3035).transform(lons, lats)


def to_4326_many(xs, ys):
    return _transformer(3035, 4326).transform(xs, ys)


def in_bbox(lon: float, lat: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
