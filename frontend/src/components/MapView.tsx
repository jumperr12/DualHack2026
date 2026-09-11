import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { getInfrastructure, getTrack, getVessels, VesselProps } from "../api";

const STYLE = "https://tiles.openfreemap.org/styles/liberty";
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

// Warstwy statyczne: nazwa -> kolor. Filtrujemy po properties.layer z /infrastructure.
const STATIC_LAYERS: Record<string, string> = {
  cables: "#f5c542", pipelines: "#ff8c42", windfarms: "#6fd3ff", exclusions: "#9aa5b1",
};

type Props = { onSelect: (v: VesselProps | null) => void; onVesselCount: (n: number) => void };

export default function MapView({ onSelect, onVesselCount }: Props) {
  const div = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [visible, setVisible] = useState<Record<string, boolean>>(
    { cables: true, pipelines: true, windfarms: false, exclusions: false, vessels: true });

  useEffect(() => {
    if (!div.current) return;
    const map = new maplibregl.Map({
      container: div.current, style: STYLE, center: [24.5, 59.9], zoom: 6.5, attributionControl: {},
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl(), "top-left");

    map.on("load", async () => {
      map.addSource("infra", { type: "geojson", data: EMPTY });
      map.addSource("vessels", { type: "geojson", data: EMPTY });
      map.addSource("track", { type: "geojson", data: EMPTY });

      map.addLayer({ id: "exclusions", type: "fill", source: "infra",
        filter: ["==", ["get", "layer"], "exclusions"],
        paint: { "fill-color": STATIC_LAYERS.exclusions, "fill-opacity": 0.25 } });
      map.addLayer({ id: "windfarms", type: "fill", source: "infra",
        filter: ["==", ["get", "layer"], "windfarms"],
        paint: { "fill-color": STATIC_LAYERS.windfarms, "fill-opacity": 0.3 } });
      for (const id of ["pipelines", "cables"]) {
        map.addLayer({ id, type: "line", source: "infra", filter: ["==", ["get", "layer"], id],
          paint: { "line-color": STATIC_LAYERS[id], "line-width": 2 } });
      }
      map.addLayer({ id: "track", type: "line", source: "track",
        paint: { "line-color": "#ffffff", "line-width": 2, "line-opacity": 0.8 } });
      // Statki: kolor wg level z detektora (M1); do tego czasu wszystkie szare.
      map.addLayer({ id: "vessels", type: "circle", source: "vessels", paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 5, 2.5, 10, 6],
        "circle-color": ["match", ["get", "level"], "alarm", "#f85149", "watch", "#f5c542", "#9aa5b1"],
        "circle-stroke-color": ["case", ["==", ["get", "is_replay"], 1], "#ff00ff", "#0f1419"],
        "circle-stroke-width": 1 } });

      try {
        (map.getSource("infra") as maplibregl.GeoJSONSource).setData(await getInfrastructure());
      } catch (e) { console.warn("infrastructure", e); }

      map.on("click", "vessels", async (e) => {
        const p = e.features?.[0]?.properties as VesselProps | undefined;
        if (!p) return;
        onSelect(p);
        try {
          (map.getSource("track") as maplibregl.GeoJSONSource).setData(await getTrack(p.mmsi));
        } catch (err) { console.warn("track", err); }
      });
      map.on("click", (e) => {
        if (!map.queryRenderedFeatures(e.point, { layers: ["vessels"] }).length) {
          onSelect(null);
          (map.getSource("track") as maplibregl.GeoJSONSource).setData(EMPTY);
        }
      });
      map.on("mouseenter", "vessels", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "vessels", () => { map.getCanvas().style.cursor = ""; });
    });
    return () => map.remove();
  }, []);

  // Odświeżanie statków co 5 s dla widocznego obszaru.
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      const map = mapRef.current;
      if (!map || !map.getSource("vessels")) return;
      const b = map.getBounds();
      const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((n) => n.toFixed(3)).join(",");
      try {
        const fc = await getVessels(bbox);
        if (stop) return;
        (map.getSource("vessels") as maplibregl.GeoJSONSource).setData(fc);
        onVesselCount(fc.features.length);
      } catch (e) { console.warn("vessels", e); }
    };
    const id = setInterval(tick, 5000);
    tick();
    return () => { stop = true; clearInterval(id); };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => { for (const [id, on] of Object.entries(visible)) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
    } };
    map.loaded() ? apply() : map.once("load", apply);
  }, [visible]);

  return (
    <div className="map">
      <div ref={div} className="canvas" />
      <div className="legend">
        {Object.entries({ ...STATIC_LAYERS, vessels: "#9aa5b1" }).map(([id, color]) => (
          <label key={id}>
            <input type="checkbox" checked={visible[id]}
              onChange={(e) => setVisible({ ...visible, [id]: e.target.checked })} />
            <span className="sw" style={{ background: color }} /> {id}
          </label>
        ))}
      </div>
    </div>
  );
}
