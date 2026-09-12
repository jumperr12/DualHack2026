export const API = import.meta.env.VITE_API_URL ?? "";

export type Health = {
  ok: boolean;
  now: number;
  workers: Record<string, { last_write: number | null; age_s: number | null; ok: boolean }>;
};

export type VesselProps = {
  mmsi: number; ts: number; sog: number | null; cog: number | null; heading: number | null;
  nav_stat: number | null; is_replay: number; name: string | null; ship_type: number | null;
  score: number | null; level: string | null; zone: string | null; category: string | null;
};

/** Holowniki, piloty i służby: mają prawo pracować przy infrastrukturze, więc trzymamy je
 *  poza główną listą — ale nie kasujemy, bo typ statku jest deklarowany przez sam statek. */
export const WHITELISTED = "whitelisted_activity";

export type VesselsFC = GeoJSON.FeatureCollection<GeoJSON.Point, VesselProps> & { ts: number };

export type Reason = { rule: string; points: number; detail: string };

export type Alert = {
  id: number; mmsi: number; asset: string; category: string; level: "watch" | "alarm";
  score: number; reasons: Reason[]; ts_start: number; ts_last: number; status: string;
  is_replay: number;
};

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export const getHealth = () => get<Health>("/health");
export const getInfrastructure = () => get<GeoJSON.FeatureCollection>("/infrastructure");
export const getVessels = (bbox?: string) =>
  get<VesselsFC>(`/vessels${bbox ? `?bbox=${bbox}` : ""}`);
export const getTrack = (mmsi: number, hours = 6) =>
  get<GeoJSON.Feature<GeoJSON.LineString>>(`/vessels/${mmsi}/track?hours=${hours}`);
export const getAlerts = (status = "open") => get<Alert[]>(`/alerts?status=${status}`);

/** SSE: nowe i zaktualizowane alerty. Zwraca funkcję zamykającą strumień. */
export function subscribeAlerts(onAlert: (a: Alert) => void): () => void {
  const es = new EventSource(`${API}/alerts/stream`);
  es.addEventListener("alert", (e) => onAlert(JSON.parse((e as MessageEvent).data)));
  es.onerror = () => console.warn("alert stream reconnecting");
  return () => es.close();
}
