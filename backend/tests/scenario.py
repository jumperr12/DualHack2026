"""Generator syntetycznych trajektorii do testów (prawdziwy scenariusz Eagle S powstaje w M3)."""

import math

from kotwica.geo import to_3035
from kotwica.models import Ping

KN_TO_MS = 0.514444


def leg(mmsi: int, ts0: int, lon0: float, lat0: float, lon1: float, lat1: float,
        sog: float, hdg_offset: float = 0.0, step_s: int = 30, nav_stat: int = 0,
        is_replay: int = 0) -> list[Ping]:
    """Odcinek trasy ze stałą prędkością. `hdg_offset` to rozjazd dziobu względem kursu."""
    x0, y0 = to_3035(lon0, lat0)
    x1, y1 = to_3035(lon1, lat1)
    dist = math.dist((x0, y0), (x1, y1))
    duration = dist / (sog * KN_TO_MS) if sog > 0 else step_s
    n = max(int(duration // step_s), 1)
    cog = (math.degrees(math.atan2(x1 - x0, y1 - y0))) % 360      # 0 = północ
    out = []
    for i in range(n):
        f = i / n
        lon = lon0 + (lon1 - lon0) * f
        lat = lat0 + (lat1 - lat0) * f
        x, y = to_3035(lon, lat)
        out.append(Ping(mmsi=mmsi, ts=ts0 + i * step_s, lat=lat, lon=lon, x=x, y=y,
                        sog=sog, cog=cog, heading=(cog + hdg_offset) % 360, rot=0,
                        nav_stat=nav_stat, is_replay=is_replay))
    return out


def track(mmsi: int, ts0: int, legs: list[dict]) -> list[Ping]:
    """Kolejne odcinki jeden po drugim; każdy leg to kwargs dla `leg` bez mmsi i ts0."""
    out: list[Ping] = []
    ts = ts0
    for kwargs in legs:
        part = leg(mmsi, ts, **kwargs)
        out += part
        ts = part[-1].ts + kwargs.get("step_s", 30) if part else ts
    return out


def background_fleet(ts0: int, n: int, lon: float, lat: float, minutes: int,
                     hdg_offset: float = 0.0, sog: float = 8.0, span_deg: float = 0.1,
                     spacing_deg: float = 0.005, offset_lat: float = 0.03) -> list[Ping]:
    """Grupa odniesienia dla sygnatury: statki kursujące tam i z powrotem obok kabla.

    Muszą zostać w promieniu `FLEET_RADIUS_M` przez cały scenariusz, inaczej sygnatury nie da się
    policzyć. Płyną `offset_lat` na północ od kabla, czyli poza strefą, żeby same nie dawały alertów.
    """
    pings: list[Ping] = []
    for i in range(n):
        lat_i = lat + offset_lat + spacing_deg * i
        legs, elapsed = [], 0.0
        # jeden przelot w jedną stronę: 2 * span_deg długości geograficznej
        km = 2 * span_deg * 111.32 * math.cos(math.radians(lat_i))
        leg_min = km / (sog * 1.852) * 60
        while elapsed < minutes:
            west, east = lon - span_deg, lon + span_deg
            a, b = (east, west) if len(legs) % 2 == 0 else (west, east)
            legs.append(dict(lon0=a, lat0=lat_i, lon1=b, lat1=lat_i, sog=sog,
                             hdg_offset=hdg_offset))
            elapsed += leg_min
        pings += track(2000 + i, ts0, legs)
    return pings


def merge(*groups: list[Ping]) -> list[Ping]:
    """Łączy strumienie pingów i sortuje po czasie, tak jak przychodzą z bazy."""
    out: list[Ping] = []
    for g in groups:
        out += g
    return sorted(out, key=lambda p: p.ts)
