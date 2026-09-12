"""Projekcje EPSG:4326 <-> EPSG:3035 oraz strefy wokół infrastruktury.

Wszystkie odległości i bufory liczymy w metrach (EPSG:3035). Strefa to bufor `ZONE_BUFFER_M`
wokół linii kabla lub rurociągu. Wyłączenia (porty, kotwicowiska) sprawdzamy PRZED regułami:
w porcie wolna prędkość jest normalna, a kable wychodzą na ląd właśnie przy portach.
"""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape
from shapely.strtree import STRtree

log = logging.getLogger("geo")


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


def geom_to_3035(geom):
    """Geometria WGS84 -> EPSG:3035 (bez kopiowania punkt po punkcie)."""
    def fwd(c):
        x, y = to_3035_many(c[:, 0], c[:, 1])
        return np.column_stack([x, y])
    return shapely.transform(geom, fwd)


@dataclass(slots=True)
class Asset:
    """Kabel, rurociąg albo inny liniowy obiekt infrastruktury, w metrach."""
    name: str
    layer: str          # cables | pipelines
    kind: str | None    # power | telecom | gas ...
    line: object        # geometria w EPSG:3035


class Zones:
    """Strefy wokół infrastruktury + warstwa wyłączeń. Wszystko w EPSG:3035.

    Bufory trzymamy osobno w STRtree (żeby wiedzieć, KTÓRY obiekt), a dodatkowo jako
    przygotowaną sumę do szybkiego sprawdzenia „czy w ogóle w strefie".
    """

    def __init__(self, assets: list[Asset], buffer_m: float, exclusions: list | None = None):
        self.assets = assets
        self.buffer_m = buffer_m
        self.buffers = [a.line.buffer(buffer_m) for a in assets]
        self.tree = STRtree(self.buffers) if self.buffers else None
        self.union = shapely.union_all(self.buffers) if self.buffers else None
        if self.union is not None:
            shapely.prepare(self.union)
        self.exclusions = exclusions or []
        self.excl_union = shapely.union_all(self.exclusions) if self.exclusions else None
        if self.excl_union is not None:
            shapely.prepare(self.excl_union)
        log.info("zones: %d assets, buffer %.0f m, %d exclusion polygons",
                 len(assets), buffer_m, len(self.exclusions))

    # --- zapytania punktowe (x, y w EPSG:3035) ---

    def in_zone(self, x: float, y: float) -> bool:
        return self.union is not None and bool(shapely.contains_xy(self.union, x, y))

    def in_exclusion(self, x: float, y: float) -> bool:
        return self.excl_union is not None and bool(shapely.contains_xy(self.excl_union, x, y))

    def zone_at(self, x: float, y: float) -> str | None:
        """Nazwa najbliższego obiektu, w którego buforze leży punkt, albo None."""
        if self.tree is None:
            return None
        point = shapely.points(x, y)
        # `intersects`, nie `contains`: STRtree stosuje predykat jako input.predicate(tree_geom),
        # więc `contains` pytałoby, czy punkt zawiera bufor.
        hits = self.tree.query(point, predicate="intersects")
        if len(hits) == 0:
            return None
        if len(hits) == 1:
            return self.assets[hits[0]].name
        nearest = min(hits, key=lambda i: shapely.distance(self.assets[i].line, point))
        return self.assets[nearest].name

    def distance_to(self, name: str, x: float, y: float) -> float | None:
        for a in self.assets:
            if a.name == name:
                return float(shapely.distance(a.line, shapely.points(x, y)))
        return None

    # --- zapytania odcinkiem trasy (dwa kolejne pingi) ---

    def crossings(self, x0: float, y0: float, x1: float, y1: float) -> list[str]:
        """Które obiekty przecina odcinek trasy. Do reguły `repeat_crossing` i do forensyki."""
        if self.tree is None:
            return []
        seg = shapely.linestrings([[x0, y0], [x1, y1]])
        out = []
        for i in self.tree.query(seg, predicate="intersects"):
            if shapely.intersects(self.assets[i].line, seg):
                out.append(self.assets[i].name)
        return out

    def nearest_asset(self, x: float, y: float) -> tuple[str, float] | None:
        """Najbliższy obiekt i odległość w metrach, niezależnie od bufora (dla forensyki)."""
        if not self.assets:
            return None
        point = shapely.points(x, y)
        dists = [shapely.distance(a.line, point) for a in self.assets]
        i = int(np.argmin(dists))
        return self.assets[i].name, float(dists[i])

    # --- wczytywanie z data/static ---

    @classmethod
    def from_static(cls, static_dir: str | Path, buffer_m: float,
                    layers: tuple[str, ...] = ("cables", "pipelines")) -> "Zones":
        static = Path(static_dir)
        assets: list[Asset] = []
        for layer in layers:
            path = static / f"{layer}.geojson"
            if not path.exists():
                log.warning("missing %s", path)
                continue
            fc = json.loads(path.read_text(encoding="utf-8"))
            for i, f in enumerate(fc.get("features", [])):
                props = f.get("properties") or {}
                geom = shape(f["geometry"])
                if geom.is_empty or geom.geom_type not in ("LineString", "MultiLineString"):
                    continue
                assets.append(Asset(name=props.get("name") or f"{layer}-{i}", layer=layer,
                                    kind=props.get("kind"), line=geom_to_3035(geom)))
        exclusions = []
        excl_path = static / "exclusions.geojson"
        if excl_path.exists():
            fc = json.loads(excl_path.read_text(encoding="utf-8"))
            for f in fc.get("features", []):
                geom = shape(f["geometry"])
                if not geom.is_empty and geom.geom_type in ("Polygon", "MultiPolygon"):
                    exclusions.append(geom_to_3035(geom))
        return cls(assets, buffer_m, exclusions)
