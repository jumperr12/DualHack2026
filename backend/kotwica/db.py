"""Połączenie z SQLite (WAL) i schemat. Jeden proces pisze do danej tabeli, reszta czyta."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vessels(
    mmsi INTEGER PRIMARY KEY, name TEXT, ship_type INTEGER, imo INTEGER,
    call_sign TEXT, destination TEXT, draught REAL, updated_at INTEGER);

CREATE TABLE IF NOT EXISTS positions(
    mmsi INTEGER, ts INTEGER, lat REAL, lon REAL, x REAL, y REAL,
    sog REAL, cog REAL, heading REAL, rot REAL, nav_stat INTEGER, is_replay INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_positions_mmsi_ts ON positions(mmsi, ts);
CREATE INDEX IF NOT EXISTS idx_positions_ts ON positions(ts);

CREATE TABLE IF NOT EXISTS vessel_state(
    mmsi INTEGER PRIMARY KEY, last_ts INTEGER, lat REAL, lon REAL,
    sog REAL, cog REAL, score INTEGER, level TEXT, zone TEXT, category TEXT,
    is_replay INTEGER);

CREATE TABLE IF NOT EXISTS alerts(
    id INTEGER PRIMARY KEY, mmsi INTEGER, asset TEXT, category TEXT,
    level TEXT, score INTEGER, reasons TEXT, ts_start INTEGER,
    ts_last INTEGER, status TEXT, is_replay INTEGER);
CREATE INDEX IF NOT EXISTS idx_alerts_mmsi ON alerts(mmsi);

CREATE TABLE IF NOT EXISTS scenes(
    scene_id TEXT, tile_id TEXT, collection TEXT, acquired_at INTEGER,
    processed_at INTEGER, status TEXT, cloud_frac REAL,
    PRIMARY KEY (scene_id, tile_id));

CREATE TABLE IF NOT EXISTS slicks(
    id INTEGER PRIMARY KEY, scene_id TEXT, acquired_at INTEGER, geojson TEXT,
    area_km2 REAL, length_km REAL, elongation REAL, confidence REAL, near_asset TEXT);

CREATE TABLE IF NOT EXISTS slick_suspects(
    slick_id INTEGER, mmsi INTEGER, overlap REAL, angle_diff REAL,
    minutes_before INTEGER, score REAL);

CREATE TABLE IF NOT EXISTS reports(
    incident_type TEXT, incident_id INTEGER, lang TEXT, text TEXT,
    created_at INTEGER, PRIMARY KEY (incident_type, incident_id, lang));

CREATE TABLE IF NOT EXISTS forensic_cases(
    id INTEGER PRIMARY KEY, asset TEXT, fault_lat REAL, fault_lon REAL,
    fault_ts INTEGER, radius_m INTEGER, win_back_s INTEGER, win_fwd_s INTEGER,
    gnss_trust TEXT, gnss_reasons TEXT, n_vessels INTEGER, created_at INTEGER,
    runtime_ms INTEGER, positions_scanned INTEGER, quiet_note TEXT);

CREATE TABLE IF NOT EXISTS forensic_candidates(
    case_id INTEGER, mmsi INTEGER, rank INTEGER, score INTEGER,
    min_dist_m REAL, tca_ts INTEGER, crossed INTEGER,
    sig_z_med REAL, sig_persistence REAL, hdg_coverage REAL,
    reasons TEXT, intent_score REAL, intent_reasons TEXT);
CREATE INDEX IF NOT EXISTS idx_candidates_case ON forensic_candidates(case_id, rank);

CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

# Kolumny dodane po pierwszym wdrożeniu: (tabela, kolumna, typ). CREATE IF NOT EXISTS ich nie doda
# do istniejącej bazy, więc dokładamy je przez ALTER TABLE.
MIGRATIONS = [
    ("positions", "rot", "REAL"),
    ("vessel_state", "category", "TEXT"),
]


def connect(path: str, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True,
                               check_same_thread=False)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for table, column, ctype in MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default
