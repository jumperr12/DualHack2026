"""Pomiar alertów na prawdziwych danych (sekcja 13.3, liczba na slajd).

Puszcza nagrane pozycje przez ten sam silnik co produkcja, raz z warstwą wyłączeń i raz bez,
i liczy alerty na dobę na 100 km chronionej infrastruktury. Niczego nie zapisuje do bazy.

    python scripts/measure_alerts.py [--hours 24] [--top 10]
"""

import argparse
import logging
import time
from dataclasses import dataclass

import shapely

from kotwica import db
from kotwica.config import settings
from kotwica.detector.engine import Detector
from kotwica.geo import Zones
from kotwica.log import setup_logging
from workers.detector import ping_from_row

log = logging.getLogger("measure_alerts")


@dataclass
class Result:
    alerts: list
    positions: int
    vessels: int
    seconds: float
    span_days: float
    km: float

    @property
    def per_day_per_100km(self) -> float:
        if self.span_days <= 0 or self.km <= 0:
            return 0.0
        return len(self.alerts) / self.span_days * (100 / self.km)


def run(conn, zones: Zones, hours: float) -> Result:
    last_ts = conn.execute("SELECT max(ts) FROM positions").fetchone()[0]
    if last_ts is None:
        raise SystemExit("brak pozycji w bazie")
    rows = conn.execute("SELECT rowid AS rid, * FROM positions WHERE ts > ? AND is_replay = 0 "
                        "ORDER BY rowid", (last_ts - int(hours * 3600),)).fetchall()
    ship_types = {r["mmsi"]: r["ship_type"] for r in conn.execute("SELECT mmsi, ship_type FROM vessels")}
    det = Detector(zones=zones, s=settings, ship_types=ship_types)
    t0 = time.monotonic()
    alerts = {}
    for row in rows:
        for u in det.process(ping_from_row(row)):
            key = (u.mmsi, u.asset)
            if key not in alerts or u.score > alerts[key].score:
                alerts[key] = u
    span = (max(r["ts"] for r in rows) - min(r["ts"] for r in rows)) / 86400 if rows else 0
    km = sum(shapely.length(a.line) for a in zones.assets) / 1000
    return Result(alerts=list(alerts.values()), positions=len(rows),
                  vessels=len({r["mmsi"] for r in rows}), seconds=time.monotonic() - t0,
                  span_days=span, km=km)


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    conn = db.connect(settings.DB_PATH, readonly=True)
    with_excl = Zones.from_static(settings.STATIC_DIR, settings.ZONE_BUFFER_M)
    without_excl = Zones(with_excl.assets, settings.ZONE_BUFFER_M, exclusions=[])

    print(f"\n=== Pomiar na {args.hours:g} h nagranych danych ===")
    results = {}
    for label, zones in (("bez wyłączeń", without_excl), ("z wyłączeniami", with_excl)):
        r = run(conn, zones, args.hours)
        results[label] = r
        watch = sum(1 for a in r.alerts if a.level == "watch")
        alarm = sum(1 for a in r.alerts if a.level == "alarm")
        print(f"\n{label}:")
        print(f"  pozycje: {r.positions}, statki: {r.vessels}, okno: {r.span_days * 24:.1f} h, "
              f"czas liczenia: {r.seconds:.1f} s")
        print(f"  infrastruktura: {r.km:.0f} km, alerty: {len(r.alerts)} "
              f"(watch {watch}, alarm {alarm})")
        print(f"  ALERTY NA DOBĘ NA 100 KM: {r.per_day_per_100km:.2f}")

    a, b = results["bez wyłączeń"], results["z wyłączeniami"]
    if a.alerts:
        print(f"\nWyłączenia zdejmują {len(a.alerts) - len(b.alerts)} z {len(a.alerts)} alertów "
              f"({(1 - len(b.alerts) / max(len(a.alerts), 1)) * 100:.0f}%).")

    print(f"\n--- {args.top} najwyżej punktowanych (do ręcznego przeglądu) ---")
    names = {r["mmsi"]: r["name"] for r in
             db.connect(settings.DB_PATH, readonly=True).execute("SELECT mmsi, name FROM vessels")}
    for a in sorted(b.alerts, key=lambda x: -x.score)[:args.top]:
        print(f"\n  MMSI {a.mmsi} ({names.get(a.mmsi) or '?'}) — {a.asset} — {a.score} pkt {a.level}")
        for r in a.reasons:
            print(f"      [{r.points:>3}] {r.rule}: {r.detail}")


if __name__ == "__main__":
    main()
