"""Worker AIS: MQTT Digitraffic -> SQLite.

Callback MQTT tylko wrzuca surowe wiadomości do kolejki. Cała reszta (parsowanie, downsampling,
śledzenie cumowania, zapis, retencja) dzieje się w jednym wątku zapisującym, który ma własne
połączenie z bazą. Ten worker jest jedynym pisarzem tabel `positions` i `vessels`.
"""

import json
import logging
import queue
import signal
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from kotwica import db
from kotwica.config import Settings, settings
from kotwica.geo import in_bbox, to_3035
from kotwica.log import setup_logging
from kotwica.models import Ping, VesselMeta

log = logging.getLogger("ais_ingest")

# Wartości AIS oznaczające „niedostępne”. Digitraffic podaje sog/cog już zdekodowane (102.3 / 360),
# ale progi >= łapią też surowe kodowanie (1023 / 3600), gdyby kiedyś przyszło.
NA_SOG = 102.3
NA_COG = 360.0
NA_HEADING = 511
NA_ROT = {-128, 128}   # -128 to standard AIS; 128 na wypadek, gdyby źródło podawało bez znaku

SUMMARY_EVERY_S = 60


def _clean_str(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().strip("@").strip()
    return value or None


def parse_location(mmsi: int, p: dict, bbox) -> Ping | None:
    lat, lon, ts = p.get("lat"), p.get("lon"), p.get("time")
    if lat is None or lon is None or ts is None:
        return None
    if abs(lat) > 90 or abs(lon) > 180 or not in_bbox(lon, lat, bbox):
        return None
    sog, cog, heading, rot = p.get("sog"), p.get("cog"), p.get("heading"), p.get("rot")
    x, y = to_3035(lon, lat)
    return Ping(
        mmsi=mmsi, ts=int(ts), lat=lat, lon=lon, x=x, y=y,
        sog=None if sog is None or sog >= NA_SOG else sog,
        cog=None if cog is None or cog >= NA_COG else cog,
        heading=None if heading is None or heading >= NA_HEADING or heading >= 360 else heading,
        rot=None if rot is None or rot in NA_ROT else rot,
        nav_stat=p.get("navStat"),
    )


def parse_metadata(mmsi: int, p: dict) -> VesselMeta:
    ts_ms = p.get("timestamp")
    draught = p.get("draught")
    return VesselMeta(
        mmsi=mmsi,
        name=_clean_str(p.get("name")),
        ship_type=p.get("type") or None,
        imo=p.get("imo") or None,
        call_sign=_clean_str(p.get("callSign")),
        destination=_clean_str(p.get("destination")),
        draught=draught / 10 if draught else None,   # Digitraffic podaje dziesiąte części metra
        updated_at=int(ts_ms) // 1000 if ts_ms else int(time.time()),
    )


def parse_topic(topic: str) -> tuple[int, str] | None:
    """`vessels-v2/<mmsi>/location(s)|metadata` -> (mmsi, 'location'|'metadata')."""
    parts = topic.split("/")
    if len(parts) != 3 or not parts[1].isdigit():
        return None
    kind = parts[2]
    if kind.startswith("location"):
        return int(parts[1]), "location"
    if kind == "metadata":
        return int(parts[1]), "metadata"
    return None


class Downsampler:
    """Max 1 ping na MMSI na `interval_s`. Pingi starsze niż ostatni przyjęty też odpadają."""

    def __init__(self, interval_s: int):
        self.interval_s = interval_s
        self.last: dict[int, int] = {}

    def accept(self, mmsi: int, ts: int) -> bool:
        last = self.last.get(mmsi)
        if last is not None and ts - last < self.interval_s:
            return False
        self.last[mmsi] = ts
        return True


class VoyageTracker:
    """Wykrywa koniec rejsu: statek zacumowany i nieruchomy przez `stationary_s`.

    `voyage_end[mmsi]` to czas pierwszego pingu postoju, czyli granica rejsu. Wszystko do tej
    chwili to zakończony rejs, który można usunąć po okresie karencji, jeśli statek nie miał alertu.
    Stan tylko w pamięci: po restarcie nic nie zostanie usunięte, dopóki postój nie zostanie
    ponownie zaobserwowany (bezpieczny kierunek).
    """

    def __init__(self, sog_max: float, nav_stats: tuple[int, ...], stationary_s: int):
        self.sog_max = sog_max
        self.nav_stats = nav_stats
        self.stationary_s = stationary_s
        self.docked_since: dict[int, int] = {}
        self.voyage_end: dict[int, int] = {}

    def update(self, ping: Ping) -> None:
        docked = (ping.nav_stat in self.nav_stats
                  and ping.sog is not None and ping.sog < self.sog_max)
        if not docked:
            self.docked_since.pop(ping.mmsi, None)
            return
        since = self.docked_since.setdefault(ping.mmsi, ping.ts)
        if ping.ts - since >= self.stationary_s:
            self.voyage_end[ping.mmsi] = since

    def due(self, cutoff: int) -> list[tuple[int, int]]:
        return [(m, end) for m, end in self.voyage_end.items() if end <= cutoff]

    def done(self, mmsi: int) -> None:
        self.voyage_end.pop(mmsi, None)


def apply_retention(conn, tracker: VoyageTracker, now: int, s: Settings) -> tuple[int, int]:
    """Twarda retencja (RETENTION_DAYS) + usuwanie czystych rejsów po CLEAN_GRACE_H.

    Zwraca (usunięte_twardo, usunięte_czyste_rejsy).
    """
    hard = conn.execute("DELETE FROM positions WHERE ts < ?",
                        (now - s.RETENTION_DAYS * 86400,)).rowcount
    clean = 0
    if not s.CLEAN_VOYAGES:
        conn.commit()
        return hard, clean
    alerted = {row[0] for row in conn.execute("SELECT DISTINCT mmsi FROM alerts")}
    for mmsi, end in tracker.due(now - s.CLEAN_GRACE_H * 3600):
        if mmsi not in alerted:
            clean += conn.execute(
                "DELETE FROM positions WHERE mmsi = ? AND ts <= ? AND is_replay = 0",
                (mmsi, end)).rowcount
        tracker.done(mmsi)
    conn.commit()
    return hard, clean


class Ingest:
    """Przetwarzanie wiadomości i zapis wsadowy. Używane tylko z jednego wątku."""

    def __init__(self, conn, s: Settings = settings):
        self.conn = conn
        self.s = s
        self.downsampler = Downsampler(s.DOWNSAMPLE_S)
        self.tracker = VoyageTracker(s.PORT_SOG_MAX, s.PORT_NAV_STATS, s.PORT_STATIONARY_MIN * 60)
        self.pings: list[Ping] = []
        self.metas: dict[int, VesselMeta] = {}
        self.stats = {"msgs": 0, "bad": 0, "pings": 0, "meta": 0}
        db.init_schema(conn)
        self.pings_total = int(db.get_meta(conn, "pings_total", "0"))
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('started_at', ?)",
                     (str(int(time.time())),))
        conn.commit()

    def handle(self, topic: str, payload: bytes) -> None:
        self.stats["msgs"] += 1
        parsed = parse_topic(topic)
        if parsed is None:
            return
        mmsi, kind = parsed
        try:
            data = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            self.stats["bad"] += 1
            return
        if kind == "metadata":
            self.metas[mmsi] = parse_metadata(mmsi, data)
            return
        ping = parse_location(mmsi, data, self.s.BALTIC_BBOX)
        if ping is None or not self.downsampler.accept(mmsi, ping.ts):
            return
        self.tracker.update(ping)
        self.pings.append(ping)

    def flush(self, now: int) -> None:
        if not self.pings and not self.metas:
            return
        with self.conn:
            self.conn.executemany(
                "INSERT INTO positions(mmsi, ts, lat, lon, x, y, sog, cog, heading, rot, nav_stat, is_replay) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(p.mmsi, p.ts, p.lat, p.lon, p.x, p.y, p.sog, p.cog, p.heading, p.rot, p.nav_stat,
                  p.is_replay) for p in self.pings])
            self.conn.executemany(
                "INSERT INTO vessels(mmsi, name, ship_type, imo, call_sign, destination, draught, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(mmsi) DO UPDATE SET "
                "name=excluded.name, ship_type=excluded.ship_type, imo=excluded.imo, "
                "call_sign=excluded.call_sign, destination=excluded.destination, "
                "draught=excluded.draught, updated_at=excluded.updated_at",
                [(m.mmsi, m.name, m.ship_type, m.imo, m.call_sign, m.destination, m.draught,
                  m.updated_at) for m in self.metas.values()])
            self.pings_total += len(self.pings)
            db.set_meta(self.conn, "pings_total", self.pings_total)
            db.set_meta(self.conn, "ais_last_write", now)
        self.stats["pings"] += len(self.pings)
        self.stats["meta"] += len(self.metas)
        self.pings.clear()
        self.metas.clear()

    def retention(self, now: int) -> None:
        hard, clean = apply_retention(self.conn, self.tracker, now, self.s)
        log.info("retention: deleted %d expired, %d from clean voyages", hard, clean)

    def summary(self, qsize: int) -> None:
        log.info("last %ds: msgs=%d pings=%d meta=%d bad=%d queue=%d total_pings=%d",
                 SUMMARY_EVERY_S, self.stats["msgs"], self.stats["pings"], self.stats["meta"],
                 self.stats["bad"], qsize, self.pings_total)
        self.stats = dict.fromkeys(self.stats, 0)


def writer_loop(ingest: Ingest, q: queue.Queue, stop: threading.Event) -> None:
    s = ingest.s
    next_flush = time.monotonic() + s.FLUSH_S
    next_summary = time.monotonic() + SUMMARY_EVERY_S
    next_retention = time.monotonic()
    while not stop.is_set() or not q.empty():
        try:
            topic, payload = q.get(timeout=0.5)
            ingest.handle(topic, payload)
        except queue.Empty:
            pass
        mono = time.monotonic()
        if mono >= next_flush or stop.is_set():
            try:
                ingest.flush(int(time.time()))
            except Exception:
                log.exception("flush failed, will retry")
            next_flush = mono + s.FLUSH_S
        if mono >= next_summary:
            ingest.summary(q.qsize())
            next_summary = mono + SUMMARY_EVERY_S
        if mono >= next_retention:
            try:
                ingest.retention(int(time.time()))
            except Exception:
                log.exception("retention failed")
            next_retention = mono + s.RETENTION_CHECK_S
    ingest.flush(int(time.time()))


def make_client(s: Settings, q: queue.Queue) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"{s.APP_NAME}; {uuid.uuid4()}",
                         transport="websockets")
    client.tls_set()
    client.ws_set_options(path=s.MQTT_PATH, headers={"Digitraffic-User": s.APP_NAME})
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    dropped = 0

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("mqtt connect failed: %s", reason_code)
            return
        log.info("mqtt connected to %s, subscribing %s", s.MQTT_HOST, s.MQTT_TOPIC)
        c.subscribe(s.MQTT_TOPIC, qos=0)

    def on_disconnect(c, userdata, flags, reason_code, properties):
        log.warning("mqtt disconnected: %s (auto-reconnect)", reason_code)

    def on_message(c, userdata, msg):
        nonlocal dropped
        try:
            q.put_nowait((msg.topic, msg.payload))
        except queue.Full:
            dropped += 1
            if dropped % 1000 == 1:
                log.warning("queue full, dropped %d messages so far", dropped)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def main() -> None:
    setup_logging()
    conn = db.connect(settings.DB_PATH)
    ingest = Ingest(conn, settings)
    log.info("db at %s, pings so far %d", settings.DB_PATH, ingest.pings_total)

    q: queue.Queue = queue.Queue(maxsize=200_000)
    stop = threading.Event()
    writer = threading.Thread(target=writer_loop, args=(ingest, q, stop), name="writer")
    writer.start()

    client = make_client(settings, q)

    def shutdown(*_):
        log.info("shutting down")
        stop.set()
        client.disconnect()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    client.connect_async(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=60)
    try:
        while not stop.is_set():
            client.loop_forever(retry_first_connection=True)
            if not stop.is_set():
                log.warning("mqtt loop exited unexpectedly, restarting in 5 s")
                time.sleep(5)
    finally:
        stop.set()
        writer.join(timeout=10)
        conn.close()
        log.info("stopped")


if __name__ == "__main__":
    main()
