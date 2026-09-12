"""Testy pojedynczych reguł na syntetycznych trajektoriach (sekcja 17: każda reguła ma test)."""

import pytest
from shapely.geometry import LineString

from kotwica.config import Settings
from kotwica.detector.engine import Detector
from kotwica.geo import Asset, Zones, geom_to_3035
from tests.scenario import leg, merge, track

S = Settings()
T0 = 1_700_000_000
CABLE = LineString([(24.5, 60.0), (25.6, 60.0)])


def zones():
    return Zones([Asset("Estlink 2", "cables", "power", geom_to_3035(CABLE))], S.ZONE_BUFFER_M)


def fired(updates, mmsi=1):
    """Zbiór reguł z najwyżej punktowanego alertu danego statku."""
    mine = [u for u in updates if u.mmsi == mmsi]
    if not mine:
        return set()
    return {r.rule for r in max(mine, key=lambda u: u.score).reasons}


def run(detector, pings):
    out = []
    for p in sorted(pings, key=lambda p: p.ts):
        out += detector.process(p)
    return out


def test_slow_in_zone_fires_only_when_slow_and_under_way():
    d = Detector(zones=zones(), ship_types={1: 70})
    slow = track(1, T0, [dict(lon0=25.5, lat0=60.0, lon1=25.3, lat1=60.0, sog=5.0)])
    assert "slow_in_zone" in fired(run(d, slow))

    # statek naprawdę stojący na kotwicy -> reguła milczy (odstępstwo od sekcji 8.1: liczy się
    # faktyczny postój, nie sam zgłoszony status; patrz test_anchored_but_moving_is_not_exempt)
    d2 = Detector(zones=zones(), ship_types={1: 70})
    anchored = track(1, T0, [dict(lon0=25.40, lat0=60.0, lon1=25.399, lat1=60.0, sog=0.2,
                                  nav_stat=1, step_s=180)])
    assert "slow_in_zone" not in fired(run(d2, anchored))

    # za szybko na wleczenie kotwicy
    d3 = Detector(zones=zones(), ship_types={1: 70})
    fast = track(1, T0, [dict(lon0=25.5, lat0=60.0, lon1=25.3, lat1=60.0, sog=9.0)])
    assert "slow_in_zone" not in fired(run(d3, fast))


def test_dwell_needs_more_than_threshold():
    d = Detector(zones=zones(), ship_types={1: 70})
    # 5 kn wzdłuż kabla przez ponad 30 min (0.2° na 60°N to ok. 11 km, czyli ok. 72 min)
    long_stay = track(1, T0, [dict(lon0=25.5, lat0=60.0, lon1=25.3, lat1=60.0, sog=5.0)])
    assert "dwell" in fired(run(d, long_stay))

    d2 = Detector(zones=zones(), ship_types={1: 70})
    short = track(1, T0, [dict(lon0=25.02, lat0=59.98, lon1=25.02, lat1=60.02, sog=8.0)])
    assert "dwell" not in fired(run(d2, short))


def test_speed_drop_compares_with_previous_two_hours():
    d = Detector(zones=zones(), ship_types={1: 70})
    pings = track(1, T0, [
        dict(lon0=26.4, lat0=60.05, lon1=25.62, lat1=60.05, sog=12.0),   # 2 h szybko, poza strefą
        dict(lon0=25.62, lat0=60.0, lon1=25.42, lat1=60.0, sog=5.0),     # zwolnienie w strefie
    ])
    assert "speed_drop" in fired(run(d, pings))


def test_repeat_crossing_counts_the_same_asset():
    d = Detector(zones=zones(), ship_types={1: 70})
    # trzy przejścia przez kabel tam i z powrotem, wolno, w ciągu ok. 1.5 h
    legs = []
    for i in range(3):
        legs.append(dict(lon0=25.3, lat0=59.98, lon1=25.3, lat1=60.02, sog=6.0))
        legs.append(dict(lon0=25.3, lat0=60.02, lon1=25.3, lat1=59.98, sog=6.0))
    assert "repeat_crossing" in fired(run(d, track(1, T0, legs)))


def test_ais_gap_requires_confirmed_coverage():
    """Luka przy kablu liczy się tylko wtedy, gdy inne statki w tym czasie nadawały."""
    gap_s = (S.GAP_MIN + 15) * 60
    before = leg(1, T0, 25.5, 60.0, 25.45, 60.0, sog=5.0)
    after = leg(1, before[-1].ts + gap_s, 25.3, 60.0, 25.25, 60.0, sog=5.0)

    # bez świadków: sama cisza to za mało, bo pokrycie AIS bywa dziurawe
    d = Detector(zones=zones(), ship_types={1: 70})
    assert "ais_gap" not in fired(run(d, before + after))

    # trzy inne statki nadające w promieniu 20 km w trakcie luki
    witnesses = []
    for i in range(S.GAP_COVERAGE_MIN_VESSELS):
        witnesses += leg(500 + i, before[-1].ts + 60, 25.4 + 0.01 * i, 60.05,
                         25.35 + 0.01 * i, 60.05, sog=8.0)
    d2 = Detector(zones=zones(), ship_types={1: 70})
    assert "ais_gap" in fired(run(d2, merge(before, after, witnesses)))


def test_tanker_bonus_only_on_top_of_other_rules():
    slow = dict(lon0=25.5, lat0=60.0, lon1=25.3, lat1=60.0, sog=5.0)
    d = Detector(zones=zones(), ship_types={1: 80})
    assert "tanker_bonus" in fired(run(d, track(1, T0, [slow])))

    # sam przejazd bez innych przesłanek: premia nie ma się od czego doliczyć
    d2 = Detector(zones=zones(), ship_types={1: 80})
    fast = track(1, T0, [dict(lon0=25.02, lat0=59.9, lon1=25.02, lat1=60.1, sog=12.0)])
    assert run(d2, fast) == []


def test_scores_match_the_table():
    """Punkty w `reasons` muszą się zgadzać z tabelą z sekcji 8.1."""
    d = Detector(zones=zones(), ship_types={1: 80})
    updates = run(d, track(1, T0, [dict(lon0=25.5, lat0=60.0, lon1=25.3, lat1=60.0, sog=5.0)]))
    best = max(updates, key=lambda u: u.score)
    points = {r.rule: r.points for r in best.reasons}
    assert points["slow_in_zone"] == S.PTS_SLOW_IN_ZONE
    assert points["dwell"] == S.PTS_DWELL
    assert points["tanker_bonus"] == S.PTS_TANKER_BONUS
    assert best.score == sum(points.values())
    assert best.level == ("alarm" if best.score >= S.LEVEL_ALARM else "watch")


@pytest.mark.parametrize("ship_type,category", [(52, "whitelisted_activity"),
                                                (31, "whitelisted_activity"),
                                                (70, "suspicious")])
def test_whitelisted_types_get_own_category_not_silence(ship_type, category):
    """Holownik nad kablem nie znika — dostaje osobną kategorię, żeby operator go zobaczył.

    Typ statku deklaruje sam statek i nikt tego nie weryfikuje, więc kasowanie takich jednostek
    byłoby dziurą: w pomiarze na prawdziwych danych holownik miał 80 pkt nad Nord Stream 2.
    """
    d = Detector(zones=zones(), ship_types={1: ship_type})
    updates = run(d, track(1, T0, [dict(lon0=25.5, lat0=60.0, lon1=25.3, lat1=60.0, sog=5.0)]))
    assert updates, "kazda z tych jednostek powinna dac alert"
    assert {u.category for u in updates} == {category}
    # punkty liczone tak samo jak dla innych: kategoria nie zmienia wyniku
    assert max(u.score for u in updates) == S.PTS_SLOW_IN_ZONE + S.PTS_DWELL


def test_fishing_halves_the_score():
    legs = [dict(lon0=25.5, lat0=60.0, lon1=25.3, lat1=60.0, sog=5.0)]
    normal = Detector(zones=zones(), ship_types={1: 70})
    fishing = Detector(zones=zones(), ship_types={1: 30})
    a = max(run(normal, track(1, T0, legs)), key=lambda u: u.score)
    b = run(fishing, track(1, T0, legs))
    assert a.category == "suspicious"
    assert all(u.category == "accidental_risk" for u in b)
    if b:
        assert max(u.score for u in b) <= a.score * S.FISHING_SCORE_FACTOR + 1


def test_anchored_but_moving_is_not_exempt():
    """Statek deklarujący „na kotwicy", a sunący 4 kn nad kablem, to wzorzec wleczenia."""
    d = Detector(zones=zones(), ship_types={1: 80})
    pings = track(1, T0, [dict(lon0=25.5, lat0=60.0, lon1=25.3, lat1=60.0, sog=4.0, nav_stat=1)])
    assert "slow_in_zone" in fired(run(d, pings))


def test_really_anchored_vessel_over_pipeline_is_quiet():
    """Tankowiec stojący 0.1 kn na kotwicowisku nad rurociągiem (prawdziwy przypadek z nocy)."""
    d = Detector(zones=zones(), ship_types={1: 80})
    pings = track(1, T0, [dict(lon0=25.40, lat0=60.0, lon1=25.399, lat1=60.0, sog=0.1,
                               nav_stat=1, step_s=180)])
    assert run(d, pings) == []


def test_speed_drop_ignores_noise_at_standstill():
    """0.2 -> 0.1 kn to szum, nie zwolnienie."""
    d = Detector(zones=zones(), ship_types={1: 80})
    pings = track(1, T0, [dict(lon0=25.45, lat0=60.0, lon1=25.44, lat1=60.0, sog=0.2, step_s=180),
                          dict(lon0=25.44, lat0=60.0, lon1=25.43, lat1=60.0, sog=0.1, step_s=180)])
    assert "speed_drop" not in fired(run(d, pings))
