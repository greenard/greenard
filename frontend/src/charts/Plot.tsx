import { useEffect, useRef, useState } from "react";

// Plotly (≈ 4 Mo) n'est chargé qu'à l'affichage du premier graphique.
let plotlyPromise: Promise<any> | null = null;
function loadPlotly() {
  plotlyPromise ??= import("plotly.js-dist-min").then((m: any) => m.default ?? m);
  return plotlyPromise;
}

interface Props {
  data: any[];
  layout?: Record<string, any>;
  height?: number;
}

const BASE_LAYOUT = {
  margin: { l: 55, r: 20, t: 36, b: 45 },
  font: { family: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", size: 12, color: "#1c2330" },
  paper_bgcolor: "#fff",
  plot_bgcolor: "#fff",
  legend: { orientation: "h", x: 0, y: 1.0, yanchor: "bottom" },
  hovermode: "x unified",
};

export default function Plot({ data, layout = {}, height = 340 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    loadPlotly()
      .then((Plotly) => {
        if (!alive || !ref.current) return;
        void Plotly.react(ref.current, data, { ...BASE_LAYOUT, height, ...layout }, { responsive: true, displaylogo: false });
      })
      .catch((e) => setError(String(e)));
    return () => {
      alive = false;
    };
  }, [data, layout, height]);
  useEffect(() => {
    const el = ref.current;
    return () => {
      if (el && plotlyPromise) void plotlyPromise.then((P) => P.purge(el));
    };
  }, []);
  return error ? <div className="alert error">{error}</div> : <div ref={ref} style={{ width: "100%", minHeight: height }} />;
}
