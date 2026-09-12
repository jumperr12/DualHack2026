"""Tryb do tyłu: „kabel pękł tutaj i wtedy" -> ranking jednostek, które mogły to zrobić (sekcja 9).

To jest serce projektu. Operator zna czas i miejsce uszkodzenia (zabezpieczenia kabla reagują
w sekundach, reflektometr podaje miejsce z dokładnością do metrów), a my rekonstruujemy ruch
w oknie wokół tego zdarzenia.

Reguły liczy DOKŁADNIE ten sam silnik co dane na żywo — żadnej drugiej implementacji punktacji.
System ma prawo powiedzieć „nie wiem": jeśli nikt nie przekroczy progu, ranking jest pusty.
"""

import json
import logging
import math
import time
from dataclasses import dataclass, field

from kotwica.config import Settings, settings as default_settings
from kotwica.detector.engine import Detector, Reason
from kotwica.geo import Zones, to_3035
from kotwica.models import Ping

log = logging.getLogger("forensics")

CROSS_TOL_M = 200          # tolerancja przecięcia linii obiektu
BBOX_MARGIN = 1.5          # zapas wokół promienia przy pobieraniu pozycji z bazy


class ForensicLimit(Exception):
    """Okno za duże: za dużo wierszy albo za długo. Sekcja 9 każe wtedy zawęzić zapytanie."""


@dataclass(slots=True)
class FaultQuery:
    lat: float
    lon: float
    fault_ts: int
    asset: str | None = None
    radius_m: float | None = None
    win_back_s: int | None = None
    win_fwd_s: int | None = None

    def resolved(self, s: Settings) -> "FaultQuery":
        return FaultQuery(self.lat, self.lon, self.fault_ts, self.asset,
                          self.radius_m or s.FORENSIC_RADIUS_M,
                          self.win_back_s if self.win_back_s is not None else s.WIN_BACK_S,
                          self.win_fwd_s if self.win_fwd_s is not None else s.WIN_FWD_S)


@dataclass
class Candidate:
    mmsi: int
    name: str | None
    ship_type: int | None
    score: int
    min_dist_m: float
    tca_ts: int
    crossed: bool
    reasons: list[Reason] = field(default_factory=list)
    sig_persistence: float = 0.0
    hdg_coverage: float = 0.0
    post_event: str | None = None
    rank: int = 0

    def as_dict(self) -> dict:
        return {"rank": self.rank, "mmsi": self.mmsi, "name": self.name,
                "ship_type": self.ship_type, "score": self.score,
                "min_dist_m": round(self.min_dist_m, 1), "tca_ts": self.tca_ts,
                "crossed": self.crossed, "sig_persistence": round(self.sig_persistence, 3),
                "hdg_coverage": round(self.hdg_coverage, 3), "post_event": self.post_event,
                "reasons": [r.as_dict() for r in self.reasons]}


@dataclass
class CaseResult:
    query: FaultQuery
    candidates: list[Candidate]
    n_vessels: int
    positions_scanned: int
    runtime_ms: int
    quiet_note: str | None
    note: str | None = None          # wyjaśnienie, gdy ranking jest pusty
    case_id: int | None = None

    def as_dict(self) -> dict:
        q = self.query
        return {"case_id": self.case_id, "asset": q.asset, "fault_lat": q.lat, "fault_lon": q.lon,
                "fault_ts": q.fault_ts, "radius_m": q.radius_m, "win_back_s": q.win_back_s,
                "win_fwd_s": q.win_fwd_s, "n_vessels": self.n_vessels,
                "positions_scanned": self.positions_scanned, "runtime_ms": self.runtime_ms,
                "quiet_note": self.quiet_note, "note": self.note,
                "candidates": [c.as_dict() for c in self.candidates]}


def _closest_on_track(track: list[Ping], fx: float, fy: float) -> tuple[float, int]:
    """Najmniejsza odległość trasy od punktu awarii i czas największego zbliżenia.

    Liczymy po odcinkach, nie po samych pingach: przy 30 s odstępu statek 11 kn robi 170 m,
    więc punkt najbliższego podejścia zwykle leży POMIĘDZY pingami.
    """
    best_d, best_ts = math.inf, track[0].ts
    for a, b in zip(track, track[1:]):
        dx, dy = b.x - a.x, b.y - a.y
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((fx - a.x) * dx + (fy - a.y) * dy) / seg2))
        px, py = a.x + t * dx, a.y + t * dy
        d = math.dist((px, py), (fx, fy))
        if d < best_d:
            best_d, best_ts = d, int(a.ts + t * (b.ts - a.ts))
    last = track[-1]
    d_last = math.dist((last.x, last.y), (fx, fy))
    if d_last < best_d:
        best_d, best_ts = d_last, last.ts
    return best_d, best_ts


def _post_event(track: list[Ping], fault_ts: int, s: Settings) -> str | None:
    """Zachowanie po zdarzeniu: zwolnił i zawrócił czy płynął dalej jak gdyby nigdy nic."""
    before = [p for p in track if p.ts <= fault_ts and p.sog is not None]
    after = [p for p in track if fault_ts < p.ts <= fault_ts + s.POST_EVENT_MIN * 60
             and p.sog is not None]
    if not before or not after:
        return None
    sog_before = sum(p.sog for p in before[-10:]) / len(before[-10:])
    sog_after = sum(p.sog for p in after) / len(after)
    cog_before = next((p.cog for p in reversed(before) if p.cog is not None), None)
    cog_after = next((p.cog for p in reversed(after) if p.cog is not None), None)
    turn = None
    if cog_before is not None and cog_after is not None:
        turn = abs((cog_after - cog_before + 180) % 360 - 180)
    parts = [f"SOG {sog_before:.1f} -> {sog_after:.1f} kn"]
    if turn is not None:
        parts.append(f"zmiana kursu {turn:.0f}°")
    return ", ".join(parts) + f" w {s.POST_EVENT_MIN} min po zdarzeniu"


def _quiet_note(conn, q: FaultQuery, bbox, n_now: int) -> str | None:
    """Cisza jako przesłanka: czy w oknie nadawało nietypowo mało jednostek (sekcja 9.6)."""
    x0, y0, x1, y1 = bbox
    span = q.win_back_s + q.win_fwd_s
    counts = []
    for day in range(1, 8):
        t = q.fault_ts - day * 86400
        n = conn.execute(
            "SELECT count(DISTINCT mmsi) FROM positions WHERE ts BETWEEN ? AND ? "
            "AND x BETWEEN ? AND ? AND y BETWEEN ? AND ? AND is_replay = 0",
            (t - q.win_back_s, t + q.win_fwd_s, x0, x1, y0, y1)).fetchone()[0]
        if n:
            counts.append(n)
    if not counts:
        return None
    counts.sort()
    med = counts[len(counts) // 2]
    weak = " (słaba baza: mniej niż 2 dni historii)" if len(counts) < 2 else ""
    if med and n_now < 0.5 * med:
        return (f"nietypowo mała liczba nadających jednostek w oknie: {n_now} wobec mediany {med} "
                f"z {len(counts)} poprzednich dni o tej porze{weak}")
    return None


def analyse(conn, query: FaultQuery, zones: Zones, s: Settings = default_settings) -> CaseResult:
    t0 = time.monotonic()
    q = query.resolved(s)
    fx, fy = to_3035(q.lon, q.lat)
    margin = q.radius_m * BBOX_MARGIN
    bbox = (fx - margin, fy - margin, fx + margin, fy + margin)

    rows = conn.execute(
        "SELECT mmsi, ts, lat, lon, x, y, sog, cog, heading, rot, nav_stat, is_replay "
        "FROM positions WHERE ts BETWEEN ? AND ? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ? "
        "ORDER BY ts LIMIT ?",
        (q.fault_ts - q.win_back_s, q.fault_ts + q.win_fwd_s, bbox[0], bbox[2], bbox[1], bbox[3],
         s.FORENSIC_MAX_ROWS + 1)).fetchall()
    if len(rows) > s.FORENSIC_MAX_ROWS:
        raise ForensicLimit(f"okno obejmuje ponad {s.FORENSIC_MAX_ROWS} pozycji — zawęź czas lub promień")

    pings = [Ping(mmsi=r["mmsi"], ts=r["ts"], lat=r["lat"], lon=r["lon"], x=r["x"], y=r["y"],
                  sog=r["sog"], cog=r["cog"], heading=r["heading"], rot=r["rot"],
                  nav_stat=r["nav_stat"], is_replay=r["is_replay"]) for r in rows]
    tracks: dict[int, list[Ping]] = {}
    for p in pings:
        tracks.setdefault(p.mmsi, []).append(p)

    # Ten sam silnik co produkcja. Karmimy go wszystkimi pingami z okna, bo sygnatura i reguła
    # `ais_gap` potrzebują sąsiadów, nie tylko kandydata.
    meta = {r["mmsi"]: r["ship_type"] for r in conn.execute("SELECT mmsi, ship_type FROM vessels")}
    det = Detector(zones=zones, s=s, ship_types=meta)
    for p in pings:
        det.process(p)
        if time.monotonic() - t0 > s.FORENSIC_TIMEOUT_S:
            raise ForensicLimit(f"analiza przekroczyła {s.FORENSIC_TIMEOUT_S} s — zawęź okno")

    names = {r["mmsi"]: r["name"] for r in conn.execute("SELECT mmsi, name FROM vessels")}
    asset_line = zones.line_of(q.asset) if q.asset else None
    candidates: list[Candidate] = []
    for mmsi, track in tracks.items():
        min_d, tca = _closest_on_track(track, fx, fy)
        if min_d > q.radius_m:
            continue
        raw, reasons, _, _ = det.best_raw.get(mmsi, (0, [], 0, None))
        reasons = list(reasons)
        score = raw
        # Bliskość i przecięcie to przesłanki niezależne od reguł czasu rzeczywistego.
        prox = int(max(0.0, 30 * (1 - min_d / q.radius_m)))
        if prox:
            reasons.append(Reason("proximity", prox,
                                  f"najmniejsze zbliżenie {min_d:.0f} m od punktu uszkodzenia"))
            score += prox
        crossed = asset_line is not None and zones.track_crosses(track, asset_line, CROSS_TOL_M)
        if crossed:
            reasons.append(Reason("crossed_asset", 25, f"trasa przecina obiekt {q.asset}"))
            score += 25
        st = det.states.get(mmsi)
        candidates.append(Candidate(
            mmsi=mmsi, name=names.get(mmsi), ship_type=meta.get(mmsi), score=score,
            min_dist_m=min_d, tca_ts=tca, crossed=bool(crossed), reasons=reasons,
            hdg_coverage=st.hdg_coverage(s.SIG_WINDOW_MIN * 60) if st else 0.0,
            post_event=_post_event(track, q.fault_ts, s)))

    kept = sorted([c for c in candidates if c.score >= s.CANDIDATE_MIN_SCORE],
                  key=lambda c: -c.score)
    for i, c in enumerate(kept, 1):
        c.rank = i
    note = None
    if not kept:
        note = (f"żadna z {len(candidates)} jednostek w promieniu {q.radius_m / 1000:.0f} km "
                f"nie przekroczyła progu {s.CANDIDATE_MIN_SCORE} pkt — brak wskazania")
    result = CaseResult(query=q, candidates=kept, n_vessels=len(tracks),
                        positions_scanned=len(pings),
                        runtime_ms=int((time.monotonic() - t0) * 1000),
                        quiet_note=_quiet_note(conn, q, bbox, len(tracks)), note=note)
    log.info("forensics: %d pozycji, %d jednostek, %d kandydatów, %d ms",
             result.positions_scanned, result.n_vessels, len(kept), result.runtime_ms)
    return result


def save(conn, result: CaseResult) -> int:
    q = result.query
    cur = conn.execute(
        "INSERT INTO forensic_cases(asset, fault_lat, fault_lon, fault_ts, radius_m, win_back_s, "
        "win_fwd_s, n_vessels, created_at, runtime_ms, positions_scanned, quiet_note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (q.asset, q.lat, q.lon, q.fault_ts, q.radius_m, q.win_back_s, q.win_fwd_s,
         result.n_vessels, int(time.time()), result.runtime_ms, result.positions_scanned,
         result.quiet_note))
    case_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO forensic_candidates(case_id, mmsi, rank, score, min_dist_m, tca_ts, crossed, "
        "sig_persistence, hdg_coverage, reasons) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(case_id, c.mmsi, c.rank, c.score, c.min_dist_m, c.tca_ts, int(c.crossed),
          c.sig_persistence, c.hdg_coverage, json.dumps([r.as_dict() for r in c.reasons],
                                                        ensure_ascii=False))
         for c in result.candidates])
    conn.commit()
    result.case_id = case_id
    return case_id
