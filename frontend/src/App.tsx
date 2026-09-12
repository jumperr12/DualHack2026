import { useEffect, useState } from "react";
import { Alert, ForensicCase, getAlerts, getHealth, Health, subscribeAlerts, VesselProps,
         WHITELISTED } from "./api";
import AlertList from "./components/AlertList";
import FaultPanel from "./components/FaultPanel";
import MapView from "./components/MapView";

const NAV_STAT: Record<number, string> = {
  0: "under way", 1: "at anchor", 2: "not under command", 3: "restricted", 4: "constrained by draught",
  5: "moored", 6: "aground", 7: "fishing", 8: "sailing", 15: "undefined",
};

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [count, setCount] = useState(0);
  const [selected, setSelected] = useState<VesselProps | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [focus, setFocus] = useState<number | null>(null);
  const [showFiltered, setShowFiltered] = useState(false);
  const [picking, setPicking] = useState(false);
  const [fault, setFault] = useState<{ lat: number; lon: number } | null>(null);
  const [forensicCase, setForensicCase] = useState<ForensicCase | null>(null);

  useEffect(() => {
    const tick = () => getHealth().then(setHealth).catch(() => setHealth(null));
    tick();
    const id = setInterval(tick, 10000);
    return () => clearInterval(id);
  }, []);

  // Pełna lista raz, potem tylko zmiany przez SSE.
  useEffect(() => {
    getAlerts().then(setAlerts).catch(() => setAlerts([]));
    return subscribeAlerts((a) => setAlerts((prev) => {
      const rest = prev.filter((p) => p.id !== a.id);
      const next = a.status === "open" ? [...rest, a] : rest;
      return next.sort((x, y) => y.score - x.score);
    }));
  }, []);

  const ais = health?.workers["ais-worker"];
  const main = alerts.filter((a) => a.category !== WHITELISTED);
  const filtered = alerts.filter((a) => a.category === WHITELISTED);
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">KOTWICA</span>
        <span className="stat">vessels in view <b>{count}</b></span>
        <span className="stat">open alerts <b>{main.length}</b>
          {main.some((a) => a.level === "alarm") &&
            <b className="bad"> ({main.filter((a) => a.level === "alarm").length} alarm)</b>}
        </span>
        <span className="stat">AIS feed{" "}
          {ais ? <b className={ais.ok ? "ok" : "bad"}>{ais.ok ? `live (${ais.age_s}s)` : "stale"}</b>
               : <b className="bad">offline</b>}
        </span>
      </header>
      <MapView onSelect={setSelected} onVesselCount={setCount} focusMmsi={focus}
               picking={picking} fault={fault}
               onPoint={(p) => { setFault(p); setPicking(false); }} />
      <aside className="side">
        <FaultPanel point={fault} picking={picking} onPick={() => setPicking(!picking)}
                    result={forensicCase} onCase={setForensicCase}
                    onSelectCandidate={setFocus} />
        <h2>Alerts</h2>
        <AlertList alerts={main} selected={selected?.mmsi ?? null}
                   onSelect={(a) => setFocus(a.mmsi)} />
        {filtered.length > 0 && (
          <>
            <button className="filtered-toggle" onClick={() => setShowFiltered(!showFiltered)}>
              {showFiltered ? "▾" : "▸"} Service vessels: {filtered.length}
              <span className="hint"> tugs, pilots, SAR — self-declared type</span>
            </button>
            {showFiltered && <AlertList alerts={filtered} selected={selected?.mmsi ?? null}
                                        onSelect={(a) => setFocus(a.mmsi)} />}
          </>
        )}
        <h2>Vessel</h2>
        {selected ? (
          <dl>
            <dt>MMSI</dt><dd>{selected.mmsi}{selected.is_replay ? " (replay)" : ""}</dd>
            <dt>Name</dt><dd>{selected.name ?? "—"}</dd>
            <dt>Type</dt><dd>{selected.ship_type ?? "—"}</dd>
            <dt>SOG</dt><dd>{selected.sog ?? "—"} kn</dd>
            <dt>COG / HDG</dt><dd>{selected.cog ?? "—"}° / {selected.heading ?? "—"}°</dd>
            <dt>Status</dt><dd>{selected.nav_stat != null ? NAV_STAT[selected.nav_stat] ?? selected.nav_stat : "—"}</dd>
            <dt>Last ping</dt><dd>{new Date(selected.ts * 1000).toISOString().slice(11, 19)} UTC</dd>
            <dt>Score</dt><dd>{selected.score ?? "—"} {selected.level ?? ""}</dd>
          </dl>
        ) : <p className="empty">Click a vessel on the map or an alert above.</p>}
      </aside>
    </div>
  );
}
