"""Wspólne pomocniki do Overpass (OpenStreetMap). Używane przez skrypty w scripts/."""

import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("osm")

# Publiczne instancje Overpass. Główna bywa przeciążona (504), więc mamy zapasowe.
MIRRORS = ("https://overpass-api.de/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter")
NAME_KEYS = ("name", "seamark:name", "name:en", "ref", "name:fi", "name:sv", "name:et", "name:pl")


def query_overpass(query: str, user_agent: str, timeout: int = 300, rounds: int = 3) -> list[dict]:
    """Pyta kolejne instancje; przy 429/504 czeka i próbuje ponownie. Publiczne serwery są wspólne,
    więc lepiej poczekać niż je zalewać."""
    last: Exception | None = None
    for rnd in range(rounds):
        for url in MIRRORS:
            req = urllib.request.Request(url, data=query.encode("utf-8"),
                                         headers={"User-Agent": user_agent})
            try:
                log.info("querying %s", url)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                els = data.get("elements", [])
                log.info("got %d elements", len(els))
                return els
            except urllib.error.HTTPError as exc:
                # Overpass wyjaśnia błąd w treści odpowiedzi, nie w kodzie HTTP.
                try:
                    body = exc.read().decode("utf-8", "replace")
                except Exception:
                    body = ""
                detail = " ".join(body.split())[:300]
                log.warning("%s failed: %s %s", url, exc, detail)
                last = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                log.warning("%s failed: %s", url, exc)
                last = exc
        wait = 30 * (rnd + 1)
        log.info("all mirrors busy, waiting %d s", wait)
        time.sleep(wait)
    raise RuntimeError(f"all Overpass mirrors failed, last: {last}")


def tiles(bbox: tuple[float, float, float, float], step: float
          ) -> list[tuple[float, float, float, float]]:
    """Dzieli (S, W, N, E) na kafelki o boku `step` stopni — jedno wielkie zapytanie się nie wyrabia."""
    s, w, n, e = bbox
    out = []
    lat = s
    while lat < n:
        lon = w
        while lon < e:
            out.append((lat, lon, min(lat + step, n), min(lon + step, e)))
            lon += step
        lat += step
    return out


def name_of(tags: dict) -> str | None:
    for k in NAME_KEYS:
        v = tags.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def coords(geometry: list[dict]) -> list[list[float]]:
    return [[p["lon"], p["lat"]] for p in geometry]


def way_geometry(el: dict) -> dict | None:
    """Way -> LineString albo Polygon (gdy zamknięty)."""
    if not el.get("geometry"):
        return None
    ring = coords(el["geometry"])
    if len(ring) >= 4 and ring[0] == ring[-1]:
        return {"type": "Polygon", "coordinates": [ring]}
    if len(ring) >= 2:
        return {"type": "LineString", "coordinates": ring}
    return None


def relation_lines(el: dict) -> dict | None:
    """Relacja liniowa (kabel/rurociąg z wielu odcinków) -> MultiLineString."""
    lines = [coords(m["geometry"]) for m in el.get("members", [])
             if m.get("type") == "way" and m.get("geometry") and len(m["geometry"]) >= 2]
    if not lines:
        return None
    return {"type": "MultiLineString", "coordinates": lines}
