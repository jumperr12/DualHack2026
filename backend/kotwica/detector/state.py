"""Stan pojedynczego statku: historia pingów, czas w strefie, przecięcia.

Cały czas liczymy z `ts` pingów, nigdy z zegara ściennego — dzięki temu ten sam kod działa
na danych na żywo, w replayu i w forensyce po oknie historycznym.
"""

import math
from dataclasses import dataclass, field

from kotwica.models import Ping


@dataclass(slots=True)
class Sample:
    """Ping po przetworzeniu: to, co reguły muszą pamiętać."""
    ts: int
    x: float
    y: float
    sog: float | None
    cog: float | None
    heading: float | None
    nav_stat: int | None
    zone: str | None
    delta: float | None = None    # rozjazd dziób/kurs, None gdy nie da się policzyć
    z: float | None = None        # odchylenie od mediany floty


@dataclass(slots=True)
class VesselState:
    mmsi: int
    samples: list[Sample] = field(default_factory=list)
    zone: str | None = None
    zone_since: int | None = None            # ts wejścia do bieżącej strefy
    left_zone_ts: int | None = None          # ts ostatniego pingu poza strefą (do zamykania alertów)
    crossings: list[tuple[int, str]] = field(default_factory=list)   # (ts, obiekt)

    @property
    def last(self) -> Sample | None:
        return self.samples[-1] if self.samples else None

    def add(self, sample: Sample, keep_s: int) -> None:
        self.samples.append(sample)
        cutoff = sample.ts - keep_s
        if self.samples[0].ts < cutoff:
            self.samples = [s for s in self.samples if s.ts >= cutoff]

    def window(self, seconds: int, end_ts: int | None = None) -> list[Sample]:
        end = end_ts if end_ts is not None else (self.last.ts if self.last else 0)
        return [s for s in self.samples if end - seconds <= s.ts <= end]

    def mean_sog(self, seconds: int, before_ts: int) -> float | None:
        vals = [s.sog for s in self.samples
                if before_ts - seconds <= s.ts < before_ts and s.sog is not None]
        return sum(vals) / len(vals) if vals else None

    def dwell_s(self, now_ts: int) -> int:
        return now_ts - self.zone_since if self.zone_since is not None else 0

    def crossings_of(self, asset: str, seconds: int, now_ts: int) -> int:
        return sum(1 for ts, a in self.crossings if a == asset and ts >= now_ts - seconds)

    def straightness(self, seconds: int) -> float | None:
        """Odległość w linii prostej / długość przebytej trasy. 1.0 = idealnie prosto.

        Wleczona kotwica działa jak stępka i prostuje tor.
        """
        w = self.window(seconds)
        if len(w) < 3:
            return None
        path = sum(math.dist((a.x, a.y), (b.x, b.y)) for a, b in zip(w, w[1:]))
        if path < 1.0:
            return None                      # statek praktycznie stoi, prostota bez sensu
        return math.dist((w[0].x, w[0].y), (w[-1].x, w[-1].y)) / path

    def hdg_coverage(self, seconds: int) -> float:
        """Udział pingów z dostępnym HDG w oknie. Zawsze raportowany (sekcja 18)."""
        w = self.window(seconds)
        if not w:
            return 0.0
        return sum(1 for s in w if s.heading is not None) / len(w)

    def update_zone(self, zone: str | None, ts: int) -> None:
        if zone != self.zone:
            self.zone = zone
            self.zone_since = ts if zone else None
        if zone is None:
            self.left_zone_ts = ts
        else:
            self.left_zone_ts = None


def sample_from_ping(ping: Ping, zone: str | None) -> Sample:
    return Sample(ts=ping.ts, x=ping.x, y=ping.y, sog=ping.sog, cog=ping.cog,
                  heading=ping.heading, nav_stat=ping.nav_stat, zone=zone)
