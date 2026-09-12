"""Scenariusze end-to-end przez prawdziwy silnik detektora (sekcja 13.2)."""

import pytest
from shapely.geometry import LineString, Point

from kotwica.config import Settings
from kotwica.detector.engine import Detector
from kotwica.geo import Asset, Zones, geom_to_3035
from tests.scenario import background_fleet, merge, track

T0 = 1_700_000_000
# Kabel równoleżnikowy na 60.0°N, od 24.5°E do 25.6°E (ok. 60 km) — geometria zastępcza do testów.
CABLE = LineString([(24.5, 60.0), (25.6, 60.0)])


def zones(exclusion: Point | None = None) -> Zones:
    excl = [geom_to_3035(exclusion)] if exclusion is not None else []
    return Zones([Asset("Estlink 2", "cables", "power", geom_to_3035(CABLE))],
                 buffer_m=Settings().ZONE_BUFFER_M, exclusions=excl)


def run(detector: Detector, pings) -> list:
    updates = []
    for p in pings:
        updates += detector.process(p)
    return updates


def alerts_for(updates, mmsi):
    return [u for u in updates if u.mmsi == mmsi]


def eagle_s_pings(hdg_offset: float = 20.0, drag_sog: float = 6.0):
    """Zbliżenie 11 kn z zachodu, 2 h wleczenia wzdłuż kabla, potem odejście."""
    return track(999000001, T0, [
        # 2 h szybkiego marszu wzdłuż 60.05°N (poza strefą 2 km), z 26.3°E do 25.62°E
        dict(lon0=26.30, lat0=60.05, lon1=25.62, lat1=60.05, sog=11.0),
        # wejście w strefę i 2 h wolnego marszu wzdłuż kabla
        dict(lon0=25.62, lat0=60.0, lon1=25.22, lat1=60.0, sog=drag_sog, hdg_offset=hdg_offset),
        # przyspieszenie i odejście na północ
        dict(lon0=25.22, lat0=60.0, lon1=25.0, lat1=60.2, sog=11.0),
    ])


@pytest.fixture
def detector():
    return Detector(zones=zones(), ship_types={999000001: 80})


def test_eagle_s_gives_alarm_with_expected_rules(detector):
    fleet = background_fleet(T0, 8, lon=25.4, lat=60.0, minutes=300)
    updates = run(detector, merge(eagle_s_pings(), fleet))
    mine = alerts_for(updates, 999000001)
    assert mine, "scenariusz Eagle S nie dał żadnego alertu"
    best = max(mine, key=lambda u: u.score)
    fired = {r.rule for r in best.reasons}
    assert best.level == "alarm", f"score {best.score}, reguły {fired}"
    assert {"slow_in_zone", "speed_drop", "dwell"} <= fired
    assert "drag_signature" in fired, "sygnatura wleczenia nie zadziałała"
    assert best.asset == "Estlink 2" and best.category == "suspicious"


def test_normal_tanker_crossing_gives_no_alert():
    """Tankowiec 11 kn przecinający kabel prostopadle nie może dać alertu."""
    d = Detector(zones=zones(), ship_types={111: 80})
    pings = track(111, T0, [dict(lon0=25.0, lat0=59.8, lon1=25.0, lat1=60.2, sog=11.0)])
    assert run(d, pings) == []


def test_trawler_over_cable_is_at_most_accidental_risk():
    """Kuter trałujący wolno nad kablem: to ryzyko przypadkowe, nie sabotaż."""
    d = Detector(zones=zones(), ship_types={222: 30})
    pings = track(222, T0, [dict(lon0=25.6, lat0=60.0, lon1=25.1, lat1=60.0, sog=3.0,
                                 hdg_offset=15.0, nav_stat=7)])
    updates = run(d, merge(pings, background_fleet(T0, 8, lon=25.4, lat=60.0, minutes=200)))
    assert all(u.category == "accidental_risk" and u.level != "alarm" for u in updates)


def test_slowing_inside_anchorage_gives_no_alert():
    """Wyłączenia: statek zwalniający na kotwicowisku nad kablem jest normalny."""
    anchorage = Point(25.4, 60.0).buffer(0.15)
    d = Detector(zones=zones(exclusion=anchorage), ship_types={333: 70})
    pings = track(333, T0, [dict(lon0=25.5, lat0=60.0, lon1=25.3, lat1=60.0, sog=2.0)])
    assert run(d, pings) == []


def test_alert_closes_after_leaving_zone(detector):
    """Statek odchodzi od kabla i nadaje jeszcze ponad godzinę — alert ma się zamknąć sam."""
    fleet = background_fleet(T0, 8, lon=25.4, lat=60.0, minutes=300)
    updates = run(detector, merge(eagle_s_pings(), fleet))
    assert any(u.status == "open" for u in updates)
    closed = [u for u in updates if u.status == "closed"]
    assert len(closed) == 1 and closed[-1].mmsi == 999000001
    assert detector.open_alerts == {}
    # zamknięcie nastąpiło dopiero po ALERT_CLOSE_MIN poza strefą
    opened = [u for u in updates if u.status == "open"]
    assert closed[0].ts >= max(u.ts for u in opened)


def test_whitelisted_tug_is_ignored():
    d = Detector(zones=zones(), ship_types={444: 52})     # 52 = holownik
    updates = run(d, merge(eagle_s_pings(), background_fleet(T0, 8, 25.4, 60.0, 300)))
    assert alerts_for(updates, 444) == []
