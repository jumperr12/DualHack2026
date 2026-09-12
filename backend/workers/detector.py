"""Worker detektora: co 60 s bierze nowe pingi z bazy i przepuszcza przez silnik.

Jedyny pisarz tabel `alerts` i `vessel_state`. Kursorem jest `rowid`, nie `ts`:
replay wstawia pingi z czasem scenariusza, więc kursor po czasie albo przeskoczyłby żywe pingi,
albo pominął replay. Stan po restarcie odtwarzamy z ostatnich 3 h pozycji.
"""

import json
import logging
import signal
import time

from kotwica import db
from kotwica.config import Settings, settings
from kotwica.detector.engine import AlertUpdate, Detector, OpenAlert, Reason
from kotwica.geo import Zones
from kotwica.log import setup_logging
from kotwica.models import Ping

log = logging.getLogger("detector_worker")

CURSOR_KEY = "detector_last_rowid"
RUN_KEY = "detector_last_run"
WARMUP_H = 3
BATCH = 20_000


def ping_from_row(row) -> Ping:
    return Ping(mmsi=row["mmsi"], ts=row["ts"], lat=row["lat"], lon=row["lon"], x=row["x"],
                y=row["y"], sog=row["sog"], cog=row["cog"], heading=row["heading"],
                rot=row["rot"], nav_stat=row["nav_stat"], is_replay=row["is_replay"])


class DetectorWorker:
    def __init__(self, conn, s: Settings = settings):
        self.conn = conn
        self.s = s
        db.init_schema(conn)
        zones = Zones.from_static(s.STATIC_DIR, s.ZONE_BUFFER_M)
        self.detector = Detector(zones=zones, s=s, ship_types=self._ship_types())
        self.alert_ids: dict[tuple[int, str], int] = {}
        self._load_open_alerts()
        self._warmup()

    # --- start ---

    def _ship_types(self) -> dict[int, int | None]:
        return {r["mmsi"]: r["ship_type"] for r in
                self.conn.execute("SELECT mmsi, ship_type FROM vessels")}

    def _load_open_alerts(self) -> None:
        """Alerty otwarte przed restartem wracają do pamięci, żeby ich nie zdublować."""
        for r in self.conn.execute("SELECT * FROM alerts WHERE status = 'open'"):
            key = (r["mmsi"], r["asset"])
            self.alert_ids[key] = r["id"]
            self.detector.open_alerts[key] = OpenAlert(
                mmsi=r["mmsi"], asset=r["asset"], category=r["category"], score=r["score"],
                reasons=[Reason(**x) for x in json.loads(r["reasons"] or "[]")],
                ts_start=r["ts_start"], ts_last=r["ts_last"], is_replay=r["is_replay"] or 0,
                db_id=r["id"])
        log.info("restored %d open alerts", len(self.alert_ids))

    def _warmup(self) -> None:
        """Odtwarza stan statków z ostatnich 3 h — ale tylko z pingów JUŻ przetworzonych.

        Ich alerty są w bazie, więc wyniki rozgrzewki odrzucamy i nie ruszamy kursora.
        Pingi za kursorem (także przy pierwszym starcie na pełnej bazie) obsłuży zwykły cykl.
        """
        cursor = int(db.get_meta(self.conn, CURSOR_KEY, "0"))
        if cursor == 0:
            log.info("no cursor yet, skipping warm-up")
            return
        last_ts = self.conn.execute("SELECT max(ts) FROM positions WHERE rowid <= ?",
                                    (cursor,)).fetchone()[0]
        if last_ts is None:
            return
        rows = self.conn.execute(
            "SELECT rowid AS rid, * FROM positions WHERE rowid <= ? AND ts > ? ORDER BY rowid",
            (cursor, last_ts - WARMUP_H * 3600)).fetchall()
        for row in rows:
            self.detector.process(ping_from_row(row))
        log.info("warmed up on %d positions, %d vessels in state", len(rows), len(self.detector.states))

    # --- pętla ---

    def cycle(self) -> int:
        cursor = int(db.get_meta(self.conn, CURSOR_KEY, "0"))
        rows = self.conn.execute(
            "SELECT rowid AS rid, * FROM positions WHERE rowid > ? ORDER BY rowid LIMIT ?",
            (cursor, BATCH)).fetchall()
        updates: list[AlertUpdate] = []
        touched: dict[int, Ping] = {}
        for row in rows:
            ping = ping_from_row(row)
            updates += self.detector.process(ping)
            touched[ping.mmsi] = ping
        if rows:
            last_ts = max(r["ts"] for r in rows)
            updates += self.detector.tick(last_ts)
            cursor = rows[-1]["rid"]

        with self.conn:
            for u in updates:
                self._write_alert(u)
            self._write_vessel_state(touched)
            db.set_meta(self.conn, CURSOR_KEY, cursor)
            db.set_meta(self.conn, RUN_KEY, int(time.time()))
        if rows:
            log.info("processed %d positions, %d alert updates (%d open)",
                     len(rows), len(updates), len(self.detector.open_alerts))
        return len(rows)

    def _write_alert(self, u: AlertUpdate) -> None:
        key = (u.mmsi, u.asset)
        reasons = json.dumps(u.reasons_json(), ensure_ascii=False)
        alert_id = self.alert_ids.get(key)
        if alert_id is None:
            cur = self.conn.execute(
                "INSERT INTO alerts(mmsi, asset, category, level, score, reasons, ts_start, "
                "ts_last, status, is_replay) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (u.mmsi, u.asset, u.category, u.level, u.score, reasons, u.ts, u.ts, u.status,
                 u.is_replay))
            self.alert_ids[key] = cur.lastrowid
        else:
            self.conn.execute(
                "UPDATE alerts SET category = ?, level = ?, score = ?, reasons = ?, ts_last = ?, "
                "status = ? WHERE id = ?",
                (u.category, u.level, u.score, reasons, u.ts, u.status, alert_id))
        if u.status == "closed":
            self.alert_ids.pop(key, None)

    def _write_vessel_state(self, touched: dict[int, Ping]) -> None:
        rows = []
        for mmsi, ping in touched.items():
            state = self.detector.states.get(mmsi)
            open_for = [a for (m, _), a in self.detector.open_alerts.items() if m == mmsi]
            score = max((a.score for a in open_for), default=0)
            level = self.detector.level(score) if open_for else None
            rows.append((mmsi, ping.ts, ping.lat, ping.lon, ping.sog, ping.cog, score, level,
                         state.zone if state else None, ping.is_replay))
        self.conn.executemany(
            "INSERT INTO vessel_state(mmsi, last_ts, lat, lon, sog, cog, score, level, zone, "
            "is_replay) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(mmsi) DO UPDATE SET "
            "last_ts=excluded.last_ts, lat=excluded.lat, lon=excluded.lon, sog=excluded.sog, "
            "cog=excluded.cog, score=excluded.score, level=excluded.level, zone=excluded.zone, "
            "is_replay=excluded.is_replay", rows)

    def refresh_ship_types(self) -> None:
        self.detector.ship_types = self._ship_types()


def main() -> None:
    setup_logging()
    conn = db.connect(settings.DB_PATH)
    worker = DetectorWorker(conn, settings)
    stop = False

    def shutdown(*_):
        nonlocal stop
        stop = True
        log.info("shutting down")

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("detector loop started")
    ticks = 0
    while not stop:
        try:
            worker.cycle()
            ticks += 1
            if ticks % 10 == 0:            # metadane statków dochodzą z opóźnieniem
                worker.refresh_ship_types()
        except Exception:
            log.exception("cycle failed, retrying next tick")
        for _ in range(60):                # przerwa 60 s, ale reagujemy na sygnał
            if stop:
                break
            time.sleep(1)
    conn.close()


if __name__ == "__main__":
    main()
