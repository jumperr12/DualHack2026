"""EMODnet (GeoJSON) -> data/static/{cables,pipelines,windfarms}.geojson.

Pliki pobiera człowiek do data/static/raw/. Warstwa z nazwy pliku (cable/pipe/wind) albo --layer.
Przycięcie do bbox Bałtyku, uproszczenie w EPSG:3035, ujednolicone pole `name`.

    python scripts/load_static.py
    python scripts/load_static.py --file raw/foo.geojson --layer cables
"""

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import box, mapping, shape

from kotwica.config import settings
from kotwica.geo import to_3035_many, to_4326_many
from kotwica.log import setup_logging

log = logging.getLogger("load_static")

LAYERS = ("cables", "pipelines", "windfarms")
SIMPLIFY_M = 50
NAME_KEYS = ("name", "cable_name", "cablename", "name_cable", "pipe_name", "pipename",
             "pipeline", "sitename", "site_name", "project", "title", "label", "nazwa")


def layer_from_filename(path: Path) -> str | None:
    n = path.stem.lower()
    if "cable" in n or "kabl" in n:
        return "cables"
    if "pipe" in n or "rurociag" in n:
        return "pipelines"
    if "wind" in n or "farm" in n:
        return "windfarms"
    return None


def cable_kind(path: Path) -> str | None:
    n = path.stem.lower()
    if "telecom" in n or "tele" in n:
        return "telecom"
    if "power" in n or "energy" in n or "electric" in n:
        return "power"
    return None


def pick_name(props: dict) -> str | None:
    lower = {k.lower(): v for k, v in props.items()}
    for key in NAME_KEYS:
        v = lower.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _to_wgs84_transformer(fc: dict) -> Transformer | None:
    """GeoJSON z WFS bywa w innym CRS (np. 3857/3035). Zwraca transformer albo None dla WGS84."""
    name = (fc.get("crs") or {}).get("properties", {}).get("name")
    if not name:
        return None
    crs = CRS.from_user_input(name)
    if crs.equals(CRS.from_epsg(4326)) or crs.to_epsg() == 4326 or "CRS84" in name:
        return None
    return Transformer.from_crs(crs, 4326, always_xy=True)


def _maybe_swap_axes(geoms: list, bbox) -> list:
    """WFS z urn EPSG:4326 potrafi zwrócić (lat, lon). Wykryj po bbox Bałtyku i zamień."""
    if not geoms:
        return geoms
    minx, miny, maxx, maxy = shapely.total_bounds(np.array(geoms))
    b = box(*bbox)
    if box(minx, miny, maxx, maxy).intersects(b):
        return geoms
    if box(miny, minx, maxy, maxx).intersects(b):
        log.warning("coordinates look like (lat, lon), swapping axes")
        return [shapely.transform(g, lambda c: c[:, ::-1]) for g in geoms]
    return geoms


def simplify_m(geom, tolerance_m: float):
    def fwd(c):
        x, y = to_3035_many(c[:, 0], c[:, 1])
        return np.column_stack([x, y])

    def back(c):
        lon, lat = to_4326_many(c[:, 0], c[:, 1])
        return np.column_stack([lon, lat])

    g = shapely.transform(geom, fwd).simplify(tolerance_m, preserve_topology=True)
    return shapely.transform(g, back)


def process_features(fc: dict, layer: str, bbox, kind: str | None = None) -> list[dict]:
    tr = _to_wgs84_transformer(fc)
    feats = [f for f in fc.get("features", []) if f.get("geometry")]
    geoms = [shape(f["geometry"]) for f in feats]
    if tr is not None:
        geoms = [shapely.transform(g, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))
                 for g in geoms]
    geoms = _maybe_swap_axes(geoms, bbox)

    clip = box(*bbox)
    out = []
    for f, g in zip(feats, geoms):
        g = g.intersection(clip)
        if g.is_empty:
            continue
        g = simplify_m(g, SIMPLIFY_M)
        if g.is_empty:
            continue
        g = shapely.set_precision(g, 1e-5)   # ~1 m, mniejsze pliki
        props = f.get("properties") or {}
        name = pick_name(props)
        if name is None:
            name = f"{layer}-{len(out) + 1}"
            log.info("no name attribute, using %s (keys: %s)", name, sorted(props)[:10])
        p = {"name": name, "layer": layer}
        if kind:
            p["kind"] = kind
        out.append({"type": "Feature", "properties": p, "geometry": mapping(g)})
    return out


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser()
    static = Path(settings.STATIC_DIR)
    ap.add_argument("--raw", type=Path, default=static / "raw")
    ap.add_argument("--out", type=Path, default=static)
    ap.add_argument("--file", type=Path, help="pojedynczy plik zamiast całego katalogu raw")
    ap.add_argument("--layer", choices=LAYERS)
    args = ap.parse_args()

    files = [args.file] if args.file else sorted(
        p for p in args.raw.glob("*") if p.suffix.lower() in (".geojson", ".json"))
    if not files:
        log.error("no GeoJSON files in %s", args.raw)
        raise SystemExit(1)

    by_layer: dict[str, list] = defaultdict(list)
    for path in files:
        layer = args.layer or layer_from_filename(path)
        if layer is None:
            log.warning("skipping %s: cannot infer layer (use --layer)", path.name)
            continue
        fc = json.loads(path.read_text(encoding="utf-8"))
        kind = cable_kind(path) if layer == "cables" else None
        feats = process_features(fc, layer, settings.BALTIC_BBOX, kind)
        log.info("%s -> %s: %d of %d features kept", path.name, layer, len(feats),
                 len(fc.get("features", [])))
        by_layer[layer].extend(feats)

    args.out.mkdir(parents=True, exist_ok=True)
    for layer, feats in by_layer.items():
        dst = args.out / f"{layer}.geojson"
        dst.write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                                  ensure_ascii=False), encoding="utf-8")
        log.info("wrote %s (%d features)", dst, len(feats))


if __name__ == "__main__":
    main()
