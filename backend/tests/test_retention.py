"""Usuwanie czystych rejsów: zacumowany >= 30 min, bez alertu, po 24 h karencji."""

from kotwica.config import Settings
from kotwica.models import Ping
from workers.ais_ingest import VoyageTracker, apply_retention

S = Settings(CLEAN_VOYAGES=True)   # testy mechanizmu; domyślnie jest wyłączony
T0 = 1_700_000_000
H = 3600
MMSI = 230000001


def ping(ts, sog, nav_stat, mmsi=MMSI):
    return Ping(mmsi=mmsi, ts=ts, lat=60.0, lon=25.0, x=0.0, y=0.0, sog=sog, cog=None,
                heading=None, rot=None, nav_stat=nav_stat)


def run_voyage(conn, dock_minutes, nav_stat_in_port=5, mmsi=MMSI):
    """3 h rejsu, potem postój przez `dock_minutes`. Zwraca tracker."""
    tracker = VoyageTracker(S.PORT_SOG_MAX, S.PORT_NAV_STATS, S.PORT_STATIONARY_MIN * 60)
    pings = [ping(T0 + i * 60, 11.0, 0, mmsi) for i in range(180)]
    dock_start = T0 + 180 * 60
    pings += [ping(dock_start + i * 60, 0.1, nav_stat_in_port, mmsi) for i in range(dock_minutes + 1)]
    for p in pings:
        tracker.update(p)
    conn.executemany("INSERT INTO positions(mmsi, ts, lat, lon, x, y, sog, nav_stat) "
                     "VALUES (?, ?, 60, 25, 0, 0, ?, ?)",
                     [(p.mmsi, p.ts, p.sog, p.nav_stat) for p in pings])
    conn.commit()
    return tracker, dock_start


def count(conn, mmsi=MMSI):
    return conn.execute("SELECT count(*) FROM positions WHERE mmsi = ?", (mmsi,)).fetchone()[0]


def test_clean_voyage_deleted_after_grace(conn):
    tracker, dock_start = run_voyage(conn, dock_minutes=30)
    before = count(conn)
    # przed upływem karencji nic nie znika
    assert apply_retention(conn, tracker, dock_start + 23 * H, S) == (0, 0)
    assert count(conn) == before
    # po karencji znika rejs (do początku postoju włącznie), pingi z portu zostają
    _, clean = apply_retention(conn, tracker, dock_start + 25 * H, S)
    assert clean == 181
    assert count(conn) == before - 181
    assert tracker.voyage_end == {}


def test_clean_voyages_disabled_by_default(conn):
    tracker, dock_start = run_voyage(conn, dock_minutes=30)
    before = count(conn)
    assert Settings().CLEAN_VOYAGES is False
    assert apply_retention(conn, tracker, dock_start + 25 * H, Settings()) == (0, 0)
    assert count(conn) == before


def test_voyage_with_alert_kept(conn):
    tracker, dock_start = run_voyage(conn, dock_minutes=30)
    conn.execute("INSERT INTO alerts(mmsi, asset, status) VALUES (?, 'Estlink 2', 'closed')", (MMSI,))
    before = count(conn)
    assert apply_retention(conn, tracker, dock_start + 25 * H, S) == (0, 0)
    assert count(conn) == before


def test_anchored_at_sea_not_a_voyage_end(conn):
    tracker, dock_start = run_voyage(conn, dock_minutes=60, nav_stat_in_port=1)
    before = count(conn)
    assert apply_retention(conn, tracker, dock_start + 25 * H, S) == (0, 0)
    assert count(conn) == before


def test_short_stop_not_a_voyage_end(conn):
    tracker, dock_start = run_voyage(conn, dock_minutes=20)
    before = count(conn)
    assert apply_retention(conn, tracker, dock_start + 25 * H, S) == (0, 0)
    assert count(conn) == before


def test_hard_retention_removes_old_rows(conn):
    tracker, dock_start = run_voyage(conn, dock_minutes=0)
    total = count(conn)
    hard, _ = apply_retention(conn, tracker, T0 + S.RETENTION_DAYS * 86400 + 90 * 60, S)
    assert hard == 90           # pierwsze 90 minut rejsu przekroczyło 7 dni
    assert count(conn) == total - 90


def test_other_vessels_untouched(conn):
    tracker, dock_start = run_voyage(conn, dock_minutes=30)
    run_voyage(conn, dock_minutes=0, mmsi=999)   # inny statek, bez postoju
    other = count(conn, 999)
    apply_retention(conn, tracker, dock_start + 25 * H, S)
    assert count(conn, 999) == other
