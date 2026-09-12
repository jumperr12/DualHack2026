import { useState } from "react";
import { Candidate, ForensicCase, postForensics } from "../api";

/** Główny element interfejsu: operator podaje czas i miejsce awarii, dostaje ranking kandydatów. */
type Props = {
  point: { lat: number; lon: number } | null;   // klik na mapie
  onPick: () => void;                            // włącz tryb wskazywania punktu
  picking: boolean;
  onCase: (c: ForensicCase | null) => void;
  onSelectCandidate: (mmsi: number) => void;
  result: ForensicCase | null;
};

const HOURS = [3, 6, 12, 24];

function localInput(ts: number) {
  const d = new Date(ts * 1000 - new Date().getTimezoneOffset() * 60000);
  return d.toISOString().slice(0, 16);
}

export default function FaultPanel({ point, onPick, picking, onCase, onSelectCandidate, result }: Props) {
  const [when, setWhen] = useState(() => localInput(Date.now() / 1000 - 3600));
  const [hours, setHours] = useState(6);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (!point) return;
    setBusy(true);
    setError(null);
    try {
      onCase(await postForensics({
        lat: point.lat, lon: point.lon,
        fault_ts: Math.round(new Date(when).getTime() / 1000),
        win_back_s: hours * 3600,
      }));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      onCase(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="fault">
      <h2>Cable fault</h2>
      <div className="row">
        <button className={`pick ${picking ? "on" : ""}`} onClick={onPick}>
          {picking ? "Click the map…" : point ? "Change location" : "Pick location on map"}
        </button>
      </div>
      {point && (
        <div className="coords">{point.lat.toFixed(4)}°N {point.lon.toFixed(4)}°E</div>
      )}
      <div className="row">
        <label>Time (UTC+local)
          <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
        </label>
      </div>
      <div className="row">
        <label>Look back
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {HOURS.map((h) => <option key={h} value={h}>{h} h</option>)}
          </select>
        </label>
      </div>
      <button className="run" disabled={!point || busy} onClick={run}>
        {busy ? "Analysing…" : "Find candidates"}
      </button>
      {error && <p className="err">{error}</p>}

      {result && (
        <div className="case">
          <div className="case-head">
            <b>{result.asset ?? "no asset nearby"}</b>
            <span>{result.n_vessels} vessels · {result.positions_scanned} positions · {result.runtime_ms} ms</span>
          </div>
          {result.quiet_note && <p className="quiet">⚠ {result.quiet_note}</p>}
          {result.note && <p className="empty">{result.note}</p>}
          <ol className="cands">
            {result.candidates.map((c) => <CandidateRow key={c.mmsi} c={c} onSelect={onSelectCandidate} />)}
          </ol>
          <p className="disclaimer">
            Grounds requiring verification. This does not establish responsibility.
          </p>
        </div>
      )}
    </section>
  );
}

function CandidateRow({ c, onSelect }: { c: Candidate; onSelect: (mmsi: number) => void }) {
  return (
    <li className="cand" onClick={() => onSelect(c.mmsi)}>
      <div className="head">
        <span className="badge">{c.score}</span>
        <span className="asset">{c.name ?? `MMSI ${c.mmsi}`}</span>
        <span className="when">{(c.min_dist_m / 1000).toFixed(2)} km</span>
      </div>
      <div className="sub">
        MMSI {c.mmsi} · closest approach {new Date(c.tca_ts * 1000).toISOString().slice(11, 16)} UTC
        {c.crossed && <em> · crossed the asset</em>}
      </div>
      <ul className="reasons">
        {c.reasons.map((r, i) => <li key={i}><b>+{r.points}</b> {r.detail}</li>)}
        {c.post_event && <li><b>after</b> {c.post_event}</li>}
        <li className="cov">HDG available in {(c.hdg_coverage * 100).toFixed(0)}% of pings</li>
      </ul>
    </li>
  );
}
