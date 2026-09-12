"""Sygnatura wleczenia kotwicy (sekcja 8.2). To jest wyróżnik projektu.

Fizyka: wleczona kotwica przykłada dużą siłę oporu przy dziobie. Żeby utrzymać kurs, statek
ustawia się skośnie do własnego śladu — dziób wskazuje gdzie indziej niż kierunek ruchu.

Wiatr i prąd też powodują rozjazd, więc nie porównujemy z zerem, tylko z medianą floty
w tym samym miejscu i czasie. To jest kontrola środowiskowa i najważniejsza część tego modułu.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

from kotwica.config import Settings
from kotwica.detector.state import Sample, VesselState

EPS = 1e-6


def angular_diff(a: float, b: float) -> float:
    """Różnica kierunków w zakresie 0–180°."""
    return abs((a - b + 180) % 360 - 180)


def delta_of(sample: Sample, sog_min: float) -> float | None:
    """Rozjazd dziób/kurs dla jednego pingu albo None, jeśli nie da się go wiarygodnie policzyć."""
    if sample.heading is None or sample.cog is None or sample.sog is None:
        return None
    if sample.sog < sog_min:
        return None
    return angular_diff(sample.heading, sample.cog)


def median(values: list[float]) -> float:
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def mad(values: list[float], med: float) -> float:
    return median([abs(v - med) for v in values])


@dataclass(slots=True)
class FleetRef:
    """Grupa odniesienia: inne statki w pobliżu, w tym samym kubełku czasu."""
    median: float
    mad: float
    n: int


def fleet_reference(neighbors: Iterable[Sample], x: float, y: float, ts: int,
                    s: Settings) -> FleetRef | None:
    """Mediana i MAD rozjazdu dla floty wokół punktu. None, gdy za mało jednostek.

    `neighbors` to ostatnie pingi innych statków z okolicy (silnik podaje je z indeksu siatkowego).
    """
    bucket = s.FLEET_BUCKET_MIN * 60
    deltas = []
    for sample in neighbors:
        if abs(sample.ts - ts) > bucket:
            continue
        if math.dist((sample.x, sample.y), (x, y)) > s.FLEET_RADIUS_M:
            continue
        d = sample.delta if sample.delta is not None else delta_of(sample, s.SIG_SOG_MIN)
        if d is not None:
            deltas.append(d)
    if len(deltas) < s.FLEET_MIN_N:
        return None
    med = median(deltas)
    return FleetRef(median=med, mad=mad(deltas, med), n=len(deltas))


@dataclass(slots=True)
class Signature:
    z: float | None                # odchylenie bieżącego pingu od mediany floty
    persistence: float             # udział pingów z z > Z_SIG w oknie SIG_WINDOW_MIN
    straightness: float | None
    hdg_coverage: float
    fleet_n: int
    fleet_median: float | None
    delta: float | None

    @property
    def available(self) -> bool:
        return self.z is not None


def compute(state: VesselState, neighbors: Iterable[Sample], sample: Sample,
            s: Settings) -> Signature:
    """Liczy sygnaturę dla właśnie dodanego pingu i zapisuje `delta`/`z` w stanie.

    `z` jest liczone przyrostowo przy każdym pingu, więc trwałość (`persistence`) korzysta
    z wartości policzonych wtedy, gdy flota faktycznie tam była.
    """
    window_s = s.SIG_WINDOW_MIN * 60
    sample.delta = delta_of(sample, s.SIG_SOG_MIN)
    ref = fleet_reference(neighbors, sample.x, sample.y, sample.ts, s) \
        if sample.delta is not None else None
    if ref is not None:
        scale = max(1.4826 * ref.mad, s.SIG_MIN_SCALE_DEG)
        sample.z = (sample.delta - ref.median) / scale
    zs = [x.z for x in state.window(window_s) if x.z is not None]
    persistence = sum(1 for z in zs if z > s.Z_SIG) / len(zs) if zs else 0.0
    return Signature(z=sample.z, persistence=persistence,
                     straightness=state.straightness(window_s),
                     hdg_coverage=state.hdg_coverage(window_s),
                     fleet_n=ref.n if ref else 0,
                     fleet_median=ref.median if ref else None,
                     delta=sample.delta)
