"""Warstwa wyłączeń z OpenStreetMap (Overpass): kotwicowiska i porty -> data/static/raw/osm_exclusions.geojson.

Potem `load_static.py` zamienia to na data/static/exclusions.geojson (punkty i linie buforowane).
Obszar domyślnie ograniczony do zasięgu Digitraffic (Zatoka Fińska + Botnicka), żeby zapytanie było lekkie.

    python scripts/fetch_osm_exclusions.py [--bbox minLon,minLat,maxLon,maxLat]
"""

import argparse
import json
import logging
import urllib.request
from pathlib import Path

from kotwica.config import settings
from kotwica.log import setup_logging

log = logging.getLogger("fetch_osm_exclusions")

OVERPASS = "https://overpass-api.de/api/interpreter"
DEFAULT_BBOX = (19.0, 58.0, 31.0, 66.0)

QUERY = """
[out:json][timeout:180];
(
  nwr["seamark:type"="anchorage"]({s},{w},{n},{e});
  nwr["seamark:type"="harbour"]({s},{w},{n},{e});
  nwr["seamark:type"="anchor_berth"]({s},{w},{n},{e});
  nwr["harbour"="yes"]({s},{w},{n},{e});
  nwr["landuse"="port"]({s},{w},{n},{e});
  nwr["industrial"="port"]({s},{w},{n},{e});
);
out geom;
"""


def kind_of(tags: dict) -> str:
    st = tags.get("seamark:type", "")
    if "anchor" in st:
        return "anchorage"
    return "harbour"


def name_of(tags: dict) -> str | None:
    for k in ("seamark:name", "name", "name:en", "name:fi", "name:sv", "name:et"):
        if tags.get(k):
            return tags[k]
    return None


def _ring(geom_list):
    return [[p["lon"], p["lat"]] for p in geom_list]


def element_to_geometry(el: dict) -> dict | None:
    t = el.get("type")
    if t == "node":
        return {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
    if t == "way" and el.get("geometry"):
        ring = _ring(el["geometry"])
        if len(ring) >= 4 and ring[0] == ring[-1]:
            return {"type": "Polygon", "coordinates": [ring]}
        return {"type": "LineString", "coordinates": ring}
    if t == "relation":
        polys = []
        for m in el.get("members", []):
            if m.get("type") == "way" and m.get("role") in ("outer", "") and m.get("geometry"):
                ring = _ring(m["geometry"])
                if len(ring) >= 4 and ring[0] == ring[-1]:
                    polys.append([ring])
        if polys:
            return {"type": "MultiPolygon", "coordinates": polys}
        if "bounds" in el:   # relacja z otwartych fragmentów: bierzemy jej obrys
            b = el["bounds"]
            return {"type": "Polygon", "coordinates": [[
                [b["minlon"], b["minlat"]], [b["maxlon"], b["minlat"]],
                [b["maxlon"], b["maxlat"]], [b["minlon"], b["maxlat"]], [b["minlon"], b["minlat"]]]]}
    return None


def to_feature_collection(elements: list[dict]) -> dict:
    feats = []
    for el in elements:
        tags = el.get("tags") or {}
        geom = element_to_geometry(el)
        if geom is None:
            continue
        feats.append({"type": "Feature",
                      "properties": {"name": name_of(tags), "kind": kind_of(tags),
                                     "osm": f"{el['type']}/{el['id']}"},
                      "geometry": geom})
    return {"type": "FeatureCollection", "features": feats}


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", default=",".join(map(str, DEFAULT_BBOX)))
    ap.add_argument("--out", type=Path, default=Path(settings.STATIC_DIR) / "raw" / "osm_exclusions.geojson")
    args = ap.parse_args()
    w, s, e, n = map(float, args.bbox.split(","))
    query = QUERY.format(s=s, w=w, n=n, e=e)
    req = urllib.request.Request(OVERPASS, data=query.encode(),
                                 headers={"User-Agent": f"{settings.APP_NAME} (BDUH 2026 hackathon)"})
    log.info("querying Overpass for bbox %s", args.bbox)
    with urllib.request.urlopen(req, timeout=240) as resp:
        data = json.load(resp)
    fc = to_feature_collection(data.get("elements", []))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    kinds = {}
    for f in fc["features"]:
        kinds[f["properties"]["kind"]] = kinds.get(f["properties"]["kind"], 0) + 1
    log.info("wrote %s: %d features %s", args.out, len(fc["features"]), kinds)


if __name__ == "__main__":
    main()
