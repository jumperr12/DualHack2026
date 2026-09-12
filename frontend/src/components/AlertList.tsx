import { Alert, WHITELISTED } from "../api";

const AGO = (ts: number) => {
  const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  return m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
};

type Props = { alerts: Alert[]; selected: number | null; onSelect: (a: Alert) => void };

/** Lista alertów od najwyższego wyniku. Każdy pokazuje rozbicie reguła po regule z `reasons`. */
export default function AlertList({ alerts, selected, onSelect }: Props) {
  if (!alerts.length) return <p className="empty">No open alerts.</p>;
  return (
    <ul className="alerts">
      {alerts.map((a) => (
        <li key={a.id} className={`alert ${a.level} ${selected === a.mmsi ? "sel" : ""}`}
            onClick={() => onSelect(a)}>
          <div className="head">
            <span className={`badge ${a.level}`}>{a.score}</span>
            <span className="asset">{a.asset}</span>
            <span className="when">{AGO(a.ts_last)}</span>
          </div>
          <div className="sub">
            MMSI {a.mmsi}
            {a.category === "accidental_risk" && <em> · accidental risk</em>}
            {a.category === WHITELISTED && <em> · service vessel</em>}
            {a.is_replay === 1 && <em> · replay</em>}
          </div>
          <ul className="reasons">
            {a.reasons.map((r, i) => (
              <li key={i}><b>+{r.points}</b> {r.detail}</li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}
