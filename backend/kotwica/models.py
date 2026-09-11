from dataclasses import dataclass


@dataclass(slots=True)
class Ping:
    mmsi: int
    ts: int            # sekundy UTC
    lat: float
    lon: float
    x: float           # EPSG:3035
    y: float
    sog: float | None
    cog: float | None
    heading: float | None
    nav_stat: int | None
    is_replay: int = 0


@dataclass(slots=True)
class VesselMeta:
    mmsi: int
    name: str | None
    ship_type: int | None
    imo: int | None
    call_sign: str | None
    destination: str | None
    draught: float | None   # metry
    updated_at: int         # sekundy UTC
