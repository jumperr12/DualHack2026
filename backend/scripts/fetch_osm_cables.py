"""Kable i rurociągi podmorskie Bałtyku z OpenStreetMap -> data/static/raw/osm_{cables,pipelines}.geojson.

EMODnet ma kable tylko z krajów, które je oddały (DE, NL, FR, UK, NO) — w Zatoce Fińskiej, czyli
tam, gdzie mamy AIS z Digitraffic, nie ma nic. OSM ma Estlink 1/2, C-Lion 1, Baltic Connector,
BCS East-West Interlink, EESF-2/3, NordBalt, Fenno-Skan.

    python scripts/fetch_osm_cables.py
"""

import argparse
import json
import logging
import time
from pathlib import Path

from kotwica.config import settings
from kotwica.log import setup_logging
from kotwica.osm import name_of, query_overpass, relation_lines, tiles, way_geometry

log = logging.getLogger("fetch_osm_cables")

# (S, W, N, E) — Bałtyk. Overpass chce współrzędne w tej kolejności.
DEFAULT_BBOX = (53.0, 9.0, 66.0, 31.0)

# Dwa kroki, bo `out geom` na całym Bałtyku kończy się 504. Najpierw same tagi (tanie),
# potem geometria wyłącznie dla wybranych obiektów.
QUERY_TAGS = """
[out:json][timeout:300];
(
  nwr["submarine"="yes"]({s},{w},{n},{e});
  nwr["location"="underwater"]["power"="cable"]({s},{w},{n},{e});
  nwr["location"="underwater"]["man_made"="pipeline"]({s},{w},{n},{e});
);
out tags qt;
"""

QUERY_GEOM = """
[out:json][timeout:300];
(
{parts}
);
out geom;
"""
ID_BATCH = 80


def geom_query(ways: list[str], rels: list[str]) -> str | None:
    """Pusta lista musi zniknąć z zapytania — `way(id:0)` to błąd 400, nie pusty wynik."""
    parts = []
    if ways:
        parts.append(f"  way(id:{','.join(ways)});")
    if rels:
        parts.append(f"  relation(id:{','.join(rels)});")
    return QUERY_GEOM.format(parts="\n".join(parts)) if parts else None


def classify(tags: dict) -> tuple[str, str] | None:
    """-> (warstwa, rodzaj) albo None, jeśli to nie jest kabel ani rurociąg."""
    if tags.get("man_made") == "pipeline":
        medium = tags.get("substance") or tags.get("type") or "pipeline"
        return "pipelines", medium
    if tags.get("power") == "cable":
        return "cables", "power"
    if tags.get("communication") == "line" or tags.get("telecom") == "line" \
            or tags.get("communication:mobile_phone") or "cable" in (tags.get("seamark:type") or ""):
        return "cables", "telecom"
    return None


def is_submarine(tags: dict) -> bool:
    return tags.get("submarine") == "yes" or tags.get("location") in ("underwater", "sea")


def wanted(elements: list[dict], named_only: bool = True) -> dict[str, dict]:
    """Filtruje po tagach (krok 1) -> {'way/123': tags}."""
    out = {}
    skipped_unnamed = skipped_kind = 0
    for el in elements:
        tags = el.get("tags") or {}
        if not is_submarine(tags):
            continue
        if classify(tags) is None:
            skipped_kind += 1
            continue
        if name_of(tags) is None and named_only:
            skipped_unnamed += 1
            continue
        out[f"{el['type']}/{el['id']}"] = tags
    log.info("selected %d objects (skipped %d unnamed, %d not cable/pipeline)",
             len(out), skipped_unnamed, skipped_kind)
    return out


def to_features(elements: list[dict], tags_by_id: dict[str, dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"cables": [], "pipelines": []}
    for el in elements:
        key = f"{el['type']}/{el['id']}"
        tags = el.get("tags") or tags_by_id.get(key) or {}
        kinds = classify(tags)
        if kinds is None:
            continue
        layer, kind = kinds
        geom = relation_lines(el) if el["type"] == "relation" else way_geometry(el)
        if geom is None:
            continue
        out[layer].append({"type": "Feature", "geometry": geom, "properties": {
            "name": name_of(tags), "kind": kind, "operator": tags.get("operator"), "osm": key}})
    log.info("built %d cables, %d pipelines", len(out["cables"]), len(out["pipelines"]))
    return out


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", default=",".join(map(str, DEFAULT_BBOX)), help="S,W,N,E")
    ap.add_argument("--out", type=Path, default=Path(settings.STATIC_DIR) / "raw")
    ap.add_argument("--all", action="store_true", help="także obiekty bez nazwy (dużo lokalnych kabelków)")
    ap.add_argument("--step", type=float, default=5.0, help="bok kafelka w stopniach")
    ap.add_argument("--refresh", action="store_true", help="pomiń cache tagów i pobierz od nowa")
    args = ap.parse_args()
    bbox = tuple(map(float, args.bbox.split(",")))
    ua = f"{settings.APP_NAME} (BDUH 2026 hackathon)"

    cache = args.out / "osm_tags_cache.json"
    if cache.exists() and not args.refresh:
        tagged = json.loads(cache.read_text(encoding="utf-8"))
        log.info("using cached tags from %s (%d elements)", cache, len(tagged))
    else:
        tagged = []
        tl = tiles(bbox, args.step)
        for i, (s, w, n, e) in enumerate(tl, 1):
            log.info("tile %d/%d: S%.1f W%.1f N%.1f E%.1f", i, len(tl), s, w, n, e)
            tagged += query_overpass(QUERY_TAGS.format(s=s, w=w, n=n, e=e), ua)
            time.sleep(3)   # publiczny serwis, nie zalewamy go
        args.out.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(tagged, ensure_ascii=False), encoding="utf-8")
    tags_by_id = wanted(tagged, named_only=not args.all)

    ids = {"way": [], "relation": []}
    for key in tags_by_id:
        t, i = key.split("/")
        if t in ids:
            ids[t].append(i)
    geom_els: dict[str, dict] = {}
    for start in range(0, max(len(ids["way"]), len(ids["relation"])), ID_BATCH):
        q = geom_query(ids["way"][start:start + ID_BATCH], ids["relation"][start:start + ID_BATCH])
        if q is None:
            continue
        for el in query_overpass(q, ua):
            geom_els[f"{el['type']}/{el['id']}"] = el
        time.sleep(2)
    layers = to_features(list(geom_els.values()), tags_by_id)
    args.out.mkdir(parents=True, exist_ok=True)
    for layer, feats in layers.items():
        dst = args.out / f"osm_{layer}.geojson"
        dst.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False),
                       encoding="utf-8")
        log.info("wrote %s (%d features): %s", dst, len(feats),
                 ", ".join(sorted({f["properties"]["name"] for f in feats})[:12]))


if __name__ == "__main__":
    main()
