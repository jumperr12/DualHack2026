"""Silnik detektora: Detector.process(ping) -> list[AlertUpdate].

Ten sam kod obsługuje dane na żywo, replay i forensykę. Zero zahardkodowanych wyników.
Czas bierzemy wyłącznie z `ts` pingów.
"""

import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field

from kotwica.config import Settings, settings as default_settings
from kotwica.detector import rules as R
from kotwica.detector import signature as sig_mod
from kotwica.detector.state import VesselState, sample_from_ping
from kotwica.geo import Zones
from kotwica.models import Ping

log = logging.getLogger("detector")


@dataclass(slots=True)
class Reason:
    rule: str
    points: int
    detail: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "points": self.points, "detail": self.detail}


@dataclass(slots=True)
class AlertUpdate:
    mmsi: int
    asset: str
    category: str            # suspicious | accidental_risk
    level: str               # watch | alarm
    score: int
    reasons: list[Reason]
    ts: int
    status: str              # open | closed
    is_replay: int = 0

    def reasons_json(self) -> list[dict]:
        return [r.as_dict() for r in self.reasons]


@dataclass
class OpenAlert:
    mmsi: int
    asset: str
    category: str
    score: int
    reasons: list[Reason]
    ts_start: int
    ts_last: int
    is_replay: int = 0
    db_id: int | None = None


CELL_M = 25_000     # bok komórki indeksu sąsiedztwa, zbliżony do FLEET_RADIUS_M


@dataclass
class Detector:
    zones: Zones
    s: Settings = field(default_factory=lambda: default_settings)
    ship_types: dict[int, int | None] = field(default_factory=dict)
    states: dict[int, VesselState] = field(default_factory=dict)
    open_alerts: dict[tuple[int, str], OpenAlert] = field(default_factory=dict)
    # indeks siatkowy: komórka -> MMSI. Bez niego każdy ping przeszukiwałby wszystkie statki.
    cells: dict[tuple[int, int], set[int]] = field(default_factory=lambda: defaultdict(set))
    vessel_cell: dict[int, tuple[int, int]] = field(default_factory=dict)
    # Najwyższy surowy wynik per statek, także poniżej progu alertu. Forensyka potrzebuje
    # punktacji każdego kandydata, a nie tylko tych, którzy przekroczyli 50.
    best_raw: dict[int, tuple[int, list, int, str | None]] = field(default_factory=dict)

    def _reindex(self, mmsi: int, x: float, y: float) -> None:
        cell = (int(x // CELL_M), int(y // CELL_M))
        old = self.vessel_cell.get(mmsi)
        if old == cell:
            return
        if old is not None:
            self.cells[old].discard(mmsi)
        self.cells[cell].add(mmsi)
        self.vessel_cell[mmsi] = cell

    def neighbors(self, mmsi: int, x: float, y: float) -> Iterator:
        """Ostatnie pingi innych statków z komórki i ośmiu sąsiednich."""
        cx, cy = int(x // CELL_M), int(y // CELL_M)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in self.cells.get((cx + dx, cy + dy), ()):
                    if other == mmsi:
                        continue
                    st = self.states.get(other)
                    if st is not None and st.last is not None:
                        yield st.last

    # --- API silnika ---

    def process(self, ping: Ping) -> list[AlertUpdate]:
        state = self.states.setdefault(ping.mmsi, VesselState(mmsi=ping.mmsi))
        prev = state.last

        zone_raw = self.zones.zone_at(ping.x, ping.y)
        excluded = self.zones.in_exclusion(ping.x, ping.y)
        zone = None if excluded else zone_raw

        sample = sample_from_ping(ping, zone)
        keep_s = int(max(self.s.SPEED_WINDOW_H * 3600, self.s.SIG_WINDOW_MIN * 60,
                         self.s.REPEAT_CROSSING_H * 3600))
        state.add(sample, keep_s)
        if prev is not None:
            for asset in self.zones.crossings(prev.x, prev.y, ping.x, ping.y):
                state.crossings.append((ping.ts, asset))
        state.update_zone(zone, ping.ts)
        self._reindex(ping.mmsi, ping.x, ping.y)

        # Sygnaturę liczymy tylko w strefie: poza nią `drag_signature` i tak nie zadziała,
        # a przeszukiwanie sąsiedztwa dla każdego pingu z całego Bałtyku byłoby najdroższą
        # operacją w całym systemie. Sam `delta` liczymy zawsze — flota go potrzebuje.
        if zone is not None:
            sig = sig_mod.compute(state, self.neighbors(ping.mmsi, ping.x, ping.y), sample, self.s)
        else:
            sample.delta = sig_mod.delta_of(sample, self.s.SIG_SOG_MIN)
            sig = sig_mod.Signature(z=None, persistence=0.0, straightness=None,
                                    hdg_coverage=0.0, fleet_n=0, fleet_median=None,
                                    delta=sample.delta)
        ship_type = self.ship_types.get(ping.mmsi)

        updates = self._close_stale(ping.ts, ping.mmsi)
        ctx = R.RuleCtx(sample=sample, state=state, states=self.states, zone=zone,
                        excluded=excluded, ship_type=ship_type, sig=sig, s=self.s)
        reasons: list[Reason] = []
        for rule in R.RULES:
            hit = rule(ctx)
            if hit:
                points, detail = hit
                reasons.append(Reason(rule.__name__, points, detail))
        raw = sum(r.points for r in reasons)
        bonus = R.tanker_bonus(ctx, raw)
        if bonus:
            reasons.append(Reason("tanker_bonus", bonus[0], bonus[1]))
            raw += bonus[0]

        # Holowniki, prace podwodne i służby mają prawo pracować wolno przy infrastrukturze, więc
        # nie trafiają do głównej listy. Ale ich NIE kasujemy: `ship_type` deklaruje sam statek,
        # nikt go nie weryfikuje, a pomiar pokazał holownik z wynikiem 80 nad Nord Stream 2.
        # Operator ma je zobaczyć osobno i sam zdecydować.
        category = "suspicious"
        if R.is_whitelisted(ship_type, self.s):
            category = "whitelisted_activity"
        elif R.is_fishing(ship_type, sample.nav_stat, self.s):
            category = "accidental_risk"
            raw = int(raw * self.s.FISHING_SCORE_FACTOR)

        asset = zone or (prev.zone if prev else None)
        best = self.best_raw.get(ping.mmsi)
        if raw > 0 and (best is None or raw > best[0]):
            self.best_raw[ping.mmsi] = (raw, reasons, ping.ts, asset)
        if raw >= self.s.LEVEL_WATCH and asset:
            updates.append(self._upsert(ping, asset, category, raw, reasons))
        return updates

    def tick(self, now_ts: int) -> list[AlertUpdate]:
        """Zamyka alerty statków, które dawno wyszły ze strefy (albo przestały nadawać)."""
        return self._close_stale(now_ts, None)

    # --- cykl życia alertu (sekcja 8.3) ---

    def _upsert(self, ping: Ping, asset: str, category: str, score: int,
                reasons: list[Reason]) -> AlertUpdate:
        key = (ping.mmsi, asset)
        alert = self.open_alerts.get(key)
        if alert is None:
            alert = OpenAlert(mmsi=ping.mmsi, asset=asset, category=category, score=score,
                              reasons=reasons, ts_start=ping.ts, ts_last=ping.ts,
                              is_replay=ping.is_replay)
            self.open_alerts[key] = alert
            log.info("alert opened mmsi=%s asset=%s score=%d", ping.mmsi, asset, score)
        else:
            alert.ts_last = ping.ts
            if score >= alert.score:            # score = max, z uzasadnieniem z najwyższego pingu
                alert.score = score
                alert.reasons = reasons
            alert.category = category
        return self._to_update(alert, "open")

    def _close_stale(self, now_ts: int, only_mmsi: int | None) -> list[AlertUpdate]:
        out = []
        limit = self.s.ALERT_CLOSE_MIN * 60
        for key, alert in list(self.open_alerts.items()):
            if only_mmsi is not None and key[0] != only_mmsi:
                continue
            state = self.states.get(key[0])
            outside = state is None or state.zone is None
            if outside and now_ts - alert.ts_last > limit:
                del self.open_alerts[key]
                log.info("alert closed mmsi=%s asset=%s score=%d", alert.mmsi, alert.asset, alert.score)
                out.append(self._to_update(alert, "closed"))
        return out

    def _to_update(self, alert: OpenAlert, status: str) -> AlertUpdate:
        return AlertUpdate(mmsi=alert.mmsi, asset=alert.asset, category=alert.category,
                           level=self.level(alert.score), score=alert.score,
                           reasons=alert.reasons, ts=alert.ts_last, status=status,
                           is_replay=alert.is_replay)

    def level(self, score: int) -> str:
        return "alarm" if score >= self.s.LEVEL_ALARM else "watch"
