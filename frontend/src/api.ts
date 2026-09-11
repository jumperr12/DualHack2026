export const API = import.meta.env.VITE_API_URL ?? "";

export type Health = {
  ok: boolean;
  now: number;
  workers: Record<string, { last_write: number | null; age_s: number | null; ok: boolean }>;
};

export type VesselProps = {
  mmsi: number; ts: number; sog: number | null; cog: number | null; heading: number | null;
  nav_stat: number | null; is_replay: number; name: string | null; ship_type: number | null;
  score: number | null; level: string | null; zone: string | null;
};

export type VesselsFC = GeoJSON.FeatureCollection<GeoJSON.Point, VesselProps> & { ts: number };

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
