"""Reguły detektora (sekcja 8.1 i 8.2). Każda to czysta funkcja: (punkty, opis) albo None.

Opis trafia wprost do `reasons` w bazie, a stamtąd do interfejsu i pakietu dowodowego —
to jest nasza wyjaśnialność, więc ma być zrozumiały dla człowieka.
"""

import math
from dataclasses import dataclass

from kotwica.config import Settings
from kotwica.detector.signature import Signature
from kotwica.detector.state import Sample, VesselState

NAV_STAT_PL = {0: "w drodze", 1: "na kotwicy", 2: "bez możliwości manewru", 3: "ograniczona zdolność",
               4: "ograniczony zanurzeniem", 5: "zacumowany", 6: "na mieliźnie", 7: "połów",
               8: "pod żaglami"}


@dataclass(slots=True)
class RuleCtx:
    sample: Sample
    state: VesselState
    states: dict[int, VesselState]
    zone: str | None            # nazwa obiektu, w którego strefie jest ping (po wyłączeniach)
    excluded: bool              # ping leży w porcie / na kotwicowisku
    ship_type: int | None
    sig: Signature
    s: Settings


def _nav(sample: Sample) -> str:
    return NAV_STAT_PL.get(sample.nav_stat, f"status {sample.nav_stat}")


def is_parked(sample: Sample, s: Settings) -> bool:
    """Statek zgłasza postój (kotwica/cuma) I faktycznie stoi.

    Sam zgłoszony status nie wystarcza. Jednostka deklarująca „na kotwicy", ale sunąca 3 kn nad
    kablem, to dokładnie wzorzec wleczenia kotwicy — takiej nie wolno nam odpuścić. Odwrotnie:
    tankowiec stojący 0,1 kn na kotwicowisku, nad którym przebiega rurociąg, to normalny postój.
    """
    return (sample.nav_stat in s.ANCHORED_NAV_STATS
            and sample.sog is not None and sample.sog < s.SLOW_MIN)


def slow_in_zone(c: RuleCtx) -> tuple[int, str] | None:
    if not c.zone or c.excluded or c.sample.sog is None:
        return None
    if not (c.s.SLOW_MIN < c.sample.sog < c.s.SLOW_MAX):
        return None
    if is_parked(c.sample, c.s):
        return None
    return c.s.PTS_SLOW_IN_ZONE, (f"SOG {c.sample.sog:.1f} kn w strefie {c.zone}, "
                                  f"status: {_nav(c.sample)}")


def speed_drop(c: RuleCtx) -> tuple[int, str] | None:
    if not c.zone or c.excluded or not c.sample.sog:
        return None
    window = int(c.s.SPEED_WINDOW_H * 3600)
    mean = c.state.mean_sog(window, c.sample.ts)
    if mean is None or mean < c.s.SPEED_DROP_MIN_KN:
        return None                      # statek i tak się wcześniej nie przemieszczał
    if mean < c.s.SPEED_DROP_RATIO * c.sample.sog:
        return None
    return c.s.PTS_SPEED_DROP, (f"zwolnił z {mean:.1f} kn (średnia z {c.s.SPEED_WINDOW_H:g} h) "
                                f"do {c.sample.sog:.1f} kn")


def dwell(c: RuleCtx) -> tuple[int, str] | None:
    if not c.zone or c.excluded or is_parked(c.sample, c.s):
        return None
    minutes = c.state.dwell_s(c.sample.ts) / 60
    if minutes <= c.s.DWELL_MIN:
        return None
    return c.s.PTS_DWELL, f"w strefie {c.zone} nieprzerwanie od {minutes:.0f} min"


def repeat_crossing(c: RuleCtx) -> tuple[int, str] | None:
    if not c.zone or c.excluded:
        return None
    window = int(c.s.REPEAT_CROSSING_H * 3600)
    n = c.state.crossings_of(c.zone, window, c.sample.ts)
    if n < 2:
        return None
    return c.s.PTS_REPEAT_CROSSING, (f"{n} przecięcia obiektu {c.zone} "
                                     f"w ciągu {c.s.REPEAT_CROSSING_H:g} h")


def ais_gap(c: RuleCtx) -> tuple[int, str] | None:
    """Luka w nadawaniu, gdy ostatnia znana pozycja była w strefie.

    Sama luka nie wystarcza: pokrycie AIS bywa dziurawe. Wymagamy potwierdzenia, że w tym
    czasie inne statki w promieniu 20 km nadawały (sekcja 18).
    """
    prev = c.state.samples[-2] if len(c.state.samples) >= 2 else None
    if prev is None or prev.zone is None:
        return None
    if is_parked(prev, c.s):
        return None            # statek stojący na kotwicy milknie z powodów technicznych
    gap_s = c.sample.ts - prev.ts
    if gap_s <= c.s.GAP_MIN * 60:
        return None
    # Świadków liczymy tylko z wnętrza luki. Ping oddalony o sekundy od jej krawędzi nie dowodzi,
    # że odbiór działał przez całą przerwę — a przy awarii naszego ingestu cała flota wraca naraz.
    lo, hi = prev.ts + c.s.GAP_EDGE_MARGIN_S, c.sample.ts - c.s.GAP_EDGE_MARGIN_S
    others = 0
    for mmsi, st in c.states.items():
        if mmsi == c.state.mmsi:
            continue
        if any(lo < x.ts < hi
               and math.dist((x.x, x.y), (prev.x, prev.y)) <= c.s.GAP_COVERAGE_RADIUS_M
               for x in st.samples):
            others += 1
            if others >= c.s.GAP_COVERAGE_MIN_VESSELS:
                break
    if others < c.s.GAP_COVERAGE_MIN_VESSELS:
        return None
    return c.s.PTS_AIS_GAP, (f"przerwa w nadawaniu {gap_s / 60:.0f} min, ostatnia pozycja "
                             f"w strefie {prev.zone}; w tym czasie {others} innych jednostek "
                             f"nadawało w promieniu {c.s.GAP_COVERAGE_RADIUS_M / 1000:.0f} km")


def drag_signature(c: RuleCtx) -> tuple[int, str] | None:
    if not c.zone or c.excluded or not c.sig.available:
        return None
    if c.sig.persistence <= c.s.SIG_PERSIST_MIN:
        return None
    if c.sig.straightness is None or c.sig.straightness <= c.s.STRAIGHT_MIN:
        return None
    return c.s.PTS_DRAG_SIGNATURE, (
        f"rozjazd dziób/kurs {c.sig.delta:.0f}° przy medianie floty {c.sig.fleet_median:.0f}° "
        f"({c.sig.fleet_n} jednostek) utrzymuje się przez {c.sig.persistence * 100:.0f}% pingów "
        f"w {c.s.SIG_WINDOW_MIN} min; tor prosty w {c.sig.straightness * 100:.0f}%; "
        f"HDG dostępne w {c.sig.hdg_coverage * 100:.0f}% pingów")


# Kolejność ma znaczenie tylko dla czytelności `reasons`.
RULES = (slow_in_zone, speed_drop, dwell, repeat_crossing, ais_gap, drag_signature)


def tanker_bonus(c: RuleCtx, points_so_far: int) -> tuple[int, str] | None:
    """Premia tylko wtedy, gdy inne reguły już coś dały (sekcja 8.1)."""
    lo, hi = c.s.TANKER_TYPES
    if points_so_far <= 0 or c.ship_type is None or not (lo <= c.ship_type <= hi):
        return None
    return c.s.PTS_TANKER_BONUS, f"jednostka typu {c.ship_type} (zbiornikowiec)"


def is_whitelisted(ship_type: int | None, s: Settings) -> bool:
    """Holowniki, prace podwodne, służby — te jednostki mają prawo tam wolno pływać."""
    return ship_type is not None and ship_type in s.WHITELIST_SHIP_TYPES


def is_fishing(ship_type: int | None, nav_stat: int | None, s: Settings) -> bool:
    return ship_type == s.FISHING_SHIP_TYPE or nav_stat == s.FISHING_NAV_STAT
