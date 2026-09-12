"""Testy sygnatury wleczenia. Najważniejszy jest test kontroli środowiskowej (sekcja 13.2)."""

from kotwica.config import Settings
from kotwica.detector.signature import angular_diff, compute, delta_of, fleet_reference
from kotwica.detector.state import Sample, VesselState

S = Settings()
T0 = 1_700_000_000


def sample(ts, delta_deg=0.0, sog=6.0, x=0.0, y=0.0, cog=90.0):
    """Ping z zadanym rozjazdem dziób/kurs."""
    return Sample(ts=ts, x=x, y=y, sog=sog, cog=cog, heading=(cog + delta_deg) % 360,
                  nav_stat=0, zone="Estlink 2")


def fleet(states: dict, n: int, delta_deg: float, ts: int, x=0.0, y=0.0):
    """n statków w pobliżu, każdy z tym samym rozjazdem (np. od wiatru)."""
    for i in range(n):
        st = VesselState(mmsi=1000 + i)
        s = sample(ts, delta_deg, x=x + 100 * i, y=y)
        s.delta = delta_of(s, S.SIG_SOG_MIN)
        st.samples.append(s)
        states[1000 + i] = st
    return states


def neighbors(states: dict, mmsi: int):
    """To, co w silniku daje indeks siatkowy: ostatnie pingi innych statków."""
    return [st.last for m, st in states.items() if m != mmsi and st.last is not None]


def test_angular_diff():
    assert angular_diff(10, 350) == 20
    assert angular_diff(350, 10) == 20
    assert angular_diff(0, 180) == 180
    assert angular_diff(90, 95) == 5


def test_delta_ignores_slow_and_missing_data():
    assert delta_of(sample(T0, 20), S.SIG_SOG_MIN) == 20
    assert delta_of(sample(T0, 20, sog=0.5), S.SIG_SOG_MIN) is None   # COG przy 0.5 kn to szum
    s = sample(T0, 20)
    s.heading = None
    assert delta_of(s, S.SIG_SOG_MIN) is None


def test_fleet_reference_needs_enough_vessels():
    states = fleet({}, S.FLEET_MIN_N - 1, 5.0, T0)
    assert fleet_reference(neighbors(states, 1), 0.0, 0.0, T0, S) is None
    states = fleet({}, S.FLEET_MIN_N, 5.0, T0)
    ref = fleet_reference(neighbors(states, 1), 0.0, 0.0, T0, S)
    assert ref is not None and ref.median == 5.0 and ref.n == S.FLEET_MIN_N


def test_fleet_reference_ignores_distant_and_stale():
    states = fleet({}, 6, 5.0, T0, x=S.FLEET_RADIUS_M + 5000)     # za daleko
    assert fleet_reference(neighbors(states, 1), 0.0, 0.0, T0, S) is None
    states = fleet({}, 6, 5.0, T0 - S.FLEET_BUCKET_MIN * 60 - 60)  # za stare
    assert fleet_reference(neighbors(states, 1), 0.0, 0.0, T0, S) is None


def test_vessel_with_drag_stands_out_from_calm_fleet():
    """Statek z rozjazdem 20°, flota bez rozjazdu -> wysokie z, sygnatura wykryta."""
    states = fleet({}, 8, 1.0, T0)
    st = VesselState(mmsi=1)
    states[1] = st
    sig = None
    for i in range(60):                       # 30 min po 30 s
        ts = T0 + i * 30
        for f in states.values():             # flota nadaje dalej, bez rozjazdu
            if f.mmsi != 1:
                s = sample(ts, 1.0, x=f.samples[-1].x)
                s.delta = delta_of(s, S.SIG_SOG_MIN)
                f.samples.append(s)
        s = sample(ts, 20.0, x=i * 90.0)      # nasz statek płynie prosto na wschód
        st.add(s, 7200)
        sig = compute(st, neighbors(states, 1), s, S)
    assert sig.z > S.Z_SIG
    assert sig.persistence > S.SIG_PERSIST_MIN
    assert sig.straightness > S.STRAIGHT_MIN
    assert sig.hdg_coverage == 1.0
    assert sig.fleet_n == 8


def test_whole_fleet_drifting_gives_no_signal():
    """KLUCZOWY TEST: silny wiatr znosi wszystkich o 20°. Nikt nie może zostać oznaczony."""
    states = fleet({}, 8, 20.0, T0)
    st = VesselState(mmsi=1)
    states[1] = st
    sig = None
    for i in range(60):
        ts = T0 + i * 30
        for f in states.values():
            if f.mmsi != 1:
                s = sample(ts, 20.0 + (f.mmsi % 3), x=f.samples[-1].x)   # lekki rozrzut
                s.delta = delta_of(s, S.SIG_SOG_MIN)
                f.samples.append(s)
        s = sample(ts, 20.0, x=i * 90.0)
        st.add(s, 7200)
        sig = compute(st, neighbors(states, 1), s, S)
    assert abs(sig.z) < S.Z_SIG
    assert sig.persistence == 0.0


def test_signature_unavailable_without_fleet():
    st = VesselState(mmsi=1)
    s = sample(T0, 20.0)
    st.add(s, 7200)
    sig = compute(st, [], s, S)
    assert not sig.available and sig.fleet_n == 0 and sig.persistence == 0.0
