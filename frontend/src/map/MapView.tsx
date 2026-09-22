import maplibregl, { type GeoJSONSource, type StyleSpecification } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { GridPoint, ModelInfo, Site } from "../api/types";

export type Basemap = "osm" | "offline";

const OSM_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

// Fond hors-ligne : terres émergées Natural Earth 1:50m (domaine public), sans frontières.
const OFFLINE_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    land: { type: "geojson", data: "/basemap/land.geojson", attribution: "Natural Earth" },
  },
  layers: [
    { id: "sea", type: "background", paint: { "background-color": "#cfe3f1" } },
    { id: "land", type: "fill", source: "land", paint: { "fill-color": "#f3efe6" } },
    { id: "coast", type: "line", source: "land", paint: { "line-color": "#7c9cb4", "line-width": 0.8 } },
  ],
};

function styleFor(b: Basemap): StyleSpecification | string {
  if (b === "osm") return import.meta.env.VITE_MAP_STYLE_URL || OSM_STYLE;
  return OFFLINE_STYLE;
}

interface Props {
  sites: Site[];
  activeSiteId: number | null;
  points: GridPoint[];
  models: ModelInfo[];
  visibleModels: Record<string, boolean>;
  preview: { lat: number; lon: number } | null;
  domains: ModelInfo[];
  onMapClick: (lat: number, lon: number) => void;
  onSiteClick: (id: number) => void;
  onPointClick: (p: GridPoint) => void;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

function addAppLayers(map: maplibregl.Map) {
  const ensure = (id: string) => {
    if (!map.getSource(id)) map.addSource(id, { type: "geojson", data: EMPTY });
    return map.getSource(id) as GeoJSONSource;
  };
  ensure("domains");
  ensure("links");
  ensure("gridpoints");
  ensure("sites");
  ensure("preview");
  if (!map.getLayer("domain-lines")) {
    map.addLayer({
      id: "domain-lines",
      type: "line",
      source: "domains",
      paint: { "line-color": ["get", "color"], "line-width": 1.5, "line-dasharray": [3, 2] },
    });
    map.addLayer({
      id: "link-lines",
      type: "line",
      source: "links",
      paint: { "line-color": ["get", "color"], "line-width": 1, "line-opacity": 0.6 },
    });
    map.addLayer({
      id: "gp-circles",
      type: "circle",
      source: "gridpoints",
      paint: {
        "circle-radius": ["case", ["get", "selected"], 8, 5.5],
        "circle-color": ["get", "color"],
        "circle-stroke-color": ["case", ["get", "alert"], "#d00", "#fff"],
        "circle-stroke-width": ["case", ["get", "selected"], 3, 1.5],
        "circle-opacity": ["case", ["get", "land"], 1, 0.55],
      },
    });
    map.addLayer({
      id: "site-circles",
      type: "circle",
      source: "sites",
      paint: {
        "circle-radius": ["case", ["get", "active"], 8, 6],
        "circle-color": ["case", ["get", "active"], "#111", "#555"],
        "circle-stroke-color": "#ffd400",
        "circle-stroke-width": 2,
      },
    });
    map.addLayer({
      id: "preview-circle",
      type: "circle",
      source: "preview",
      paint: { "circle-radius": 7, "circle-color": "#ffd400", "circle-stroke-color": "#111", "circle-stroke-width": 2 },
    });
  }
}

export default function MapView(props: Props) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const [basemap, setBasemap] = useState<Basemap>(() => {
    try {
      return (localStorage.getItem("greenard.basemap") as Basemap) || "osm";
    } catch {
      return "osm";
    }
  });
  const [styleVersion, setStyleVersion] = useState(0);

  // création de la carte
  useEffect(() => {
    if (!ref.current) return;
    const map = new maplibregl.Map({
      container: ref.current,
      style: styleFor(basemap),
      center: [-8.5, 30.5],
      zoom: 4.6,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
    map.on("click", (e) => {
      const hit = map.queryRenderedFeatures(e.point, { layers: ["gp-circles", "site-circles"].filter((l) => map.getLayer(l)) });
      const f = hit[0];
      if (f?.layer.id === "gp-circles") {
        const p = propsRef.current.points.find((x) => x.grid_point_id === f.properties?.id);
        if (p) propsRef.current.onPointClick(p);
      } else if (f?.layer.id === "site-circles") {
        propsRef.current.onSiteClick(Number(f.properties?.id));
      } else {
        propsRef.current.onMapClick(e.lngLat.lat, e.lngLat.lng);
      }
    });
    for (const layer of ["gp-circles", "site-circles"]) {
      map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
    }
    // info-bulle : distance / azimut / altitudes du point de grille
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
    map.on("mousemove", "gp-circles", (e) => {
      const p = propsRef.current.points.find((x) => x.grid_point_id === e.features?.[0]?.properties?.id);
      if (!p) return;
      const m = propsRef.current.models.find((x) => x.code === p.model);
      popup
        .setLngLat([p.lon, p.lat])
        .setHTML(
          `<b>${m?.name ?? p.model}</b><br/>${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}<br/>` +
            `${(p.distance_m / 1000).toFixed(2)} km · ${p.azimuth_deg.toFixed(0)}°<br/>` +
            `z ${p.model_elevation_m === null ? "—" : p.model_elevation_m.toFixed(0)} m` +
            (p.elevation_diff_m === null ? "" : ` (Δ ${p.elevation_diff_m > 0 ? "+" : ""}${p.elevation_diff_m.toFixed(0)} m)`) +
            (p.is_land === null ? "" : p.is_land ? " · land" : " · sea"),
        )
        .addTo(map);
    });
    map.on("mouseleave", "gp-circles", () => popup.remove());
    map.on("style.load", () => {
      // Les sources et couches applicatives disparaissent à chaque changement de style.
      addAppLayers(map);
      setStyleVersion((v) => v + 1);
    });
    mapRef.current = map;
    return () => map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // changement de fond de carte
  const appliedBasemap = useRef(basemap);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || appliedBasemap.current === basemap) return;
    appliedBasemap.current = basemap;
    try {
      localStorage.setItem("greenard.basemap", basemap);
    } catch {
      /* stockage indisponible */
    }
    // diff: false → rechargement complet et événement "style.load" (sinon le diff de style
    // supprimerait silencieusement les couches applicatives).
    map.setStyle(styleFor(basemap), { diff: false });
  }, [basemap]);

  // données
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getSource("sites")) return;
    const colors = Object.fromEntries(props.models.map((m) => [m.code, m.color]));
    const active = props.sites.find((s) => s.id === props.activeSiteId);
    const visible = props.points.filter((p) => props.visibleModels[p.model] !== false);
    (map.getSource("sites") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: props.sites.map((s) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [s.lon, s.lat] },
        properties: { id: s.id, name: s.name, active: s.id === props.activeSiteId },
      })),
    });
    (map.getSource("gridpoints") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: visible.map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        properties: {
          id: p.grid_point_id,
          color: colors[p.model] ?? "#333",
          selected: p.selected,
          alert: p.elevation_alert || p.land_sea_mismatch,
          land: p.is_land !== false,
        },
      })),
    });
    (map.getSource("links") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: active
        ? visible.map((p) => ({
            type: "Feature",
            geometry: { type: "LineString", coordinates: [[active.lon, active.lat], [p.lon, p.lat]] },
            properties: { color: colors[p.model] ?? "#333" },
          }))
        : [],
    });
    (map.getSource("domains") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: props.domains
        .filter((m) => m.domain && props.visibleModels[m.code] !== false)
        .map((m) => {
          const d = m.domain!;
          return {
            type: "Feature",
            geometry: {
              type: "LineString",
              coordinates: [
                [d.lon_min, d.lat_min],
                [d.lon_max, d.lat_min],
                [d.lon_max, d.lat_max],
                [d.lon_min, d.lat_max],
                [d.lon_min, d.lat_min],
              ],
            },
            properties: { color: m.color },
          };
        }),
    });
    (map.getSource("preview") as GeoJSONSource).setData(
      props.preview
        ? { type: "Feature", geometry: { type: "Point", coordinates: [props.preview.lon, props.preview.lat] }, properties: {} }
        : EMPTY,
    );
  }, [props, styleVersion]);

  // recentrage sur le site actif (dès que ses coordonnées sont connues)
  const active = props.sites.find((x) => x.id === props.activeSiteId);
  const activeKey = active ? `${active.id}:${active.lat}:${active.lon}` : "";
  useEffect(() => {
    const map = mapRef.current;
    if (map && active) map.flyTo({ center: [active.lon, active.lat], zoom: Math.max(map.getZoom(), 8.5), essential: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey]);

  return (
    <div className="map-wrap">
      <div ref={ref} className="map" />
      <div className="map-basemap card">
        <label>
          {t("map.basemap")}
          <select value={basemap} onChange={(e) => setBasemap(e.target.value as Basemap)}>
            <option value="osm">{t("map.osm")}</option>
            <option value="offline">{t("map.offline")}</option>
          </select>
        </label>
      </div>
    </div>
  );
}
