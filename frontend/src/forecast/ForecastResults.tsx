import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { downloadFile, get } from "../api/client";
import type { Comparison, ForecastJob, ModelInfo, SeriesModel, SeriesResponse, WindRose } from "../api/types";
import Plot from "../charts/Plot";
import { codeText, errorText, fmt } from "../utils/format";
import { usePrefs } from "../utils/prefs";

const STEPS = ["10min", "15min", "1h", "3h"];
const OTHER_VARS = ["gust_10m", "t_2m", "rh_2m", "sp", "msl"];
const ROSE_COLORS = ["#d9f0e3", "#a6dbbf", "#69c095", "#2f9c6c", "#0b6e4f", "#063d2c"];

interface Props {
  job: ForecastJob;
  models: ModelInfo[];
}

function hexAlpha(hex: string, a: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

export default function ForecastResults({ job, models }: Props) {
  const { t } = useTranslation();
  const { tz } = usePrefs();
  const [step, setStep] = useState("1h");
  const [method, setMethod] = useState<"linear" | "pchip">("linear");
  const [speedMethod, setSpeedMethod] = useState<"scalar" | "vector">("scalar");
  const [pointRank, setPointRank] = useState(0);
  const [heightSel, setHeightSel] = useState<string>("");
  const [otherVar, setOtherVar] = useState("t_2m");
  const [roseModel, setRoseModel] = useState("");
  const [exportError, setExportError] = useState("");
  const ok = job.extracts.filter((e) => e.status === "success");

  const series = useQuery({
    queryKey: ["series", job.id, step, method, speedMethod],
    queryFn: () => get<SeriesResponse>(`/forecasts/${job.id}/series?step=${step}&method=${method}&speed_method=${speedMethod}`),
    enabled: ok.length > 0,
  });
  const heights = useMemo(() => {
    const s = new Set<string>();
    for (const m of series.data?.models ?? [])
      for (const k of Object.keys(m.points[0]?.variables ?? {})) if (k.startsWith("ws_") && k.endsWith("m")) s.add(k.slice(3));
    return [...s].sort((a, b) => parseInt(a) - parseInt(b));
  }, [series.data]);
  const height = heightSel && heights.includes(heightSel) ? heightSel : (heights.find((h) => h === "100m") ?? heights[heights.length - 1] ?? "10m");
  const hNum = parseInt(height);
  const rose = useQuery({
    queryKey: ["rose", job.id, hNum],
    queryFn: () => get<{ models: Record<string, WindRose | null> }>(`/forecasts/${job.id}/windrose?height=${hNum}&sectors=16`),
    enabled: ok.length > 0 && !!hNum,
  });
  const cmp = useQuery({
    queryKey: ["cmp", job.id, hNum],
    queryFn: () => get<Comparison>(`/forecasts/${job.id}/comparison?height=${hNum}`),
    enabled: ok.length > 1 && !!hNum,
  });
  const color = (code: string) => models.find((m) => m.code === code)?.color ?? "#333";
  const name = (code: string) => models.find((m) => m.code === code)?.name ?? code;
  const local = tz === "Africa/Casablanca";
  const xs = (m: SeriesModel) => (local ? m.times_local.map((s) => s.slice(0, 19)) : m.times.map((s) => s.slice(0, 19)));
  const axisTitle = local ? t("fc.axisLocal") : t("fc.axisUtc");

  const speedTraces = useMemo(() => {
    const out: any[] = [];
    for (const m of series.data?.models ?? []) {
      const p = m.points[Math.min(pointRank, m.points.length - 1)];
      const v = p?.variables[`ws_${height}`];
      if (!v) continue;
      const x = xs(m);
      const c = color(m.model);
      if (m.members > 1 && v.p10 && v.p90 && v.p50) {
        out.push({ x, y: v.min, type: "scatter", mode: "lines", line: { width: 0.6, color: c, dash: "dot" }, name: `${name(m.model)} min–max`, legendgroup: m.model, hoverinfo: "skip" });
        out.push({ x, y: v.max, type: "scatter", mode: "lines", line: { width: 0.6, color: c, dash: "dot" }, showlegend: false, legendgroup: m.model, hoverinfo: "skip" });
        out.push({ x, y: v.p10, type: "scatter", mode: "lines", line: { width: 0 }, showlegend: false, legendgroup: m.model, hoverinfo: "skip" });
        out.push({ x, y: v.p90, type: "scatter", mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: hexAlpha(c, 0.22), name: `${name(m.model)} P10–P90`, legendgroup: m.model });
        out.push({ x, y: v.p50, type: "scatter", mode: "lines", line: { color: c, width: 2 }, name: `${name(m.model)} P50 (${m.members})`, legendgroup: m.model });
      } else if (v.values) {
        out.push({ x, y: v.values, type: "scatter", mode: "lines", line: { color: c, width: 1.8 }, name: name(m.model), legendgroup: m.model });
      }
      // marqueurs : échéances natives du modèle (les autres points sont interpolés)
      const nat = m.flags.map((f, k) => (f === "interpolated" ? null : k)).filter((k): k is number => k !== null);
      const yv = v.values ?? v.p50 ?? [];
      out.push({
        x: nat.map((k) => x[k]),
        y: nat.map((k) => yv[k]),
        type: "scatter",
        mode: "markers",
        marker: { color: c, size: 4 },
        name: `${name(m.model)} · ${t("fc.nativeMarkers")}`,
        legendgroup: m.model,
        showlegend: false,
        hoverinfo: "skip",
      });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series.data, pointRank, height, local, t]);

  const dirTraces = useMemo(
    () =>
      (series.data?.models ?? []).flatMap((m) => {
        const v = m.points[Math.min(pointRank, m.points.length - 1)]?.variables[`wd_${height}`];
        return v?.values ? [{ x: xs(m), y: v.values, type: "scatter", mode: "markers", marker: { color: color(m.model), size: 4 }, name: name(m.model) }] : [];
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [series.data, pointRank, height, local],
  );
  const otherTraces = useMemo(
    () =>
      (series.data?.models ?? []).flatMap((m) => {
        const v = m.points[Math.min(pointRank, m.points.length - 1)]?.variables[otherVar];
        const y = v?.values ?? v?.p50;
        return y ? [{ x: xs(m), y, type: "scatter", mode: "lines", line: { color: color(m.model) }, name: name(m.model) }] : [];
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [series.data, pointRank, otherVar, local],
  );
  const otherUnit = series.data?.models[0]?.points[0]?.variables[otherVar]?.unit ?? "";
  const roseCode = roseModel && rose.data?.models[roseModel] ? roseModel : ok[0]?.model;
  const roseData = roseCode ? rose.data?.models[roseCode] : null;
  const roseTraces = useMemo(
    () =>
      roseData
        ? roseData.speed_bins.map((b, k) => ({
            type: "barpolar",
            r: roseData.frequency_pct[k],
            theta: roseData.sectors_deg,
            name: `${b} m/s`,
            marker: { color: ROSE_COLORS[k] },
          }))
        : [],
    [roseData],
  );

  const doExport = (fmtName: string) => {
    setExportError("");
    downloadFile(
      `/forecasts/${job.id}/export?fmt=${fmtName}&step=${step}&method=${method}&speed_method=${speedMethod}&tz=${encodeURIComponent(tz)}`,
    ).catch((e) => setExportError(errorText(t, e)));
  };

  const maxPoints = Math.max(1, ...(series.data?.models ?? []).map((m) => m.points.length));

  return (
    <div className="fc-results">
      <div className="extract-list">
        {job.extracts.map((e) => (
          <div key={e.id} className={`extract ${e.status}`}>
            <span className="swatch" style={{ background: color(e.model) }} /> <b>{name(e.model)}</b>{" "}
            {e.status === "success" ? (
              <>
                <span className="badge">{t(`fc.sources.${e.source}`)}</span>
                {e.paid && <span className="badge paid">{t("fc.paid")}</span>}
                <small className="muted">
                  {" "}
                  run {e.run?.slice(0, 13).replace("T", " ")}Z · {t("fc.nTimes", { count: e.n_times })}
                  {e.n_members > 1 && ` · ${t("fc.members", { count: e.n_members })}`}
                  {e.missing_variables.length > 0 && ` · ${t("fc.missing")} : ${e.missing_variables.join(", ")}`}
                </small>
              </>
            ) : (
              <small className="alert-cell">{e.error && "code" in e.error ? codeText(t, e.error.code, e.error.params, e.error.message) : e.status}</small>
            )}
            {e.attempts.length > 0 && (
              <details className="attempts">
                <summary>{t("fc.attempts", { count: e.attempts.length })}</summary>
                <ul>
                  {e.attempts.map((a, k) => (
                    <li key={k}>
                      {t(`fc.sources.${a.source}`)} : {codeText(t, a.code, a.params, a.message)}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        ))}
      </div>

      {ok.length > 0 && (
        <>
          <div className="row controls">
            <label>
              {t("fc.step")}
              <select value={step} onChange={(e) => setStep(e.target.value)}>
                {STEPS.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <label>
              {t("fc.interp")}
              <select value={method} onChange={(e) => setMethod(e.target.value as "linear" | "pchip")}>
                <option value="linear">{t("fc.linear")}</option>
                <option value="pchip">{t("fc.pchip")}</option>
              </select>
            </label>
            <label>
              {t("fc.speedMethod")}
              <select value={speedMethod} onChange={(e) => setSpeedMethod(e.target.value as "scalar" | "vector")}>
                <option value="scalar">{t("fc.scalar")}</option>
                <option value="vector">{t("fc.vector")}</option>
              </select>
            </label>
            <label>
              {t("fc.height")}
              <select value={height} onChange={(e) => setHeightSel(e.target.value)}>
                {heights.map((h) => (
                  <option key={h}>{h}</option>
                ))}
              </select>
            </label>
            <label>
              {t("fc.point")}
              <select value={pointRank} onChange={(e) => setPointRank(Number(e.target.value))}>
                {Array.from({ length: maxPoints }, (_, k) => (
                  <option key={k} value={k}>
                    {t("fc.pointN", { n: k + 1 })}
                  </option>
                ))}
              </select>
            </label>
            <div className="export">
              <span className="muted small">{t("fc.export")}</span>
              {["csv", "xlsx", "nc"].map((f) => (
                <button key={f} onClick={() => doExport(f)}>
                  {f === "nc" ? "NetCDF" : f.toUpperCase()}
                </button>
              ))}
            </div>
          </div>
          {exportError && <div className="alert error">{exportError}</div>}
          {series.isError && <div className="alert error">{errorText(t, series.error)}</div>}
          <p className="muted small">{t("fc.flagHelp", { step })}</p>
          <section className="chart-card">
            <h4>{t("fc.chartSpeed", { height })}</h4>
            <Plot data={speedTraces} layout={{ yaxis: { title: "m/s", rangemode: "tozero" }, xaxis: { title: axisTitle } }} />
          </section>
          <div className="chart-row">
            <section className="chart-card">
              <h4>{t("fc.chartDir", { height })}</h4>
              <Plot data={dirTraces} height={280} layout={{ yaxis: { title: "°", range: [0, 360], dtick: 90 }, xaxis: { title: axisTitle } }} />
            </section>
            <section className="chart-card">
              <h4>
                <select value={otherVar} onChange={(e) => setOtherVar(e.target.value)} aria-label={t("fc.variable")}>
                  {OTHER_VARS.map((v) => (
                    <option key={v} value={v}>
                      {t(`fc.var.${v}`)}
                    </option>
                  ))}
                </select>
              </h4>
              {otherTraces.length ? (
                <Plot data={otherTraces} height={280} layout={{ yaxis: { title: otherUnit }, xaxis: { title: axisTitle } }} />
              ) : (
                <p className="muted">{t("fc.noData")}</p>
              )}
            </section>
          </div>
          <div className="chart-row">
            <section className="chart-card">
              <h4>
                {t("fc.rose", { height })}{" "}
                <select value={roseCode} onChange={(e) => setRoseModel(e.target.value)} aria-label={t("fc.model")}>
                  {ok.map((e) => (
                    <option key={e.model} value={e.model}>
                      {name(e.model)}
                    </option>
                  ))}
                </select>
              </h4>
              {roseData ? (
                <>
                  <Plot
                    data={roseTraces}
                    height={340}
                    layout={{
                      polar: { angularaxis: { direction: "clockwise", rotation: 90, dtick: 45 }, radialaxis: { ticksuffix: " %" } },
                      hovermode: "closest",
                      legend: { orientation: "v", x: 1.05, y: 0.5 },
                    }}
                  />
                  <p className="muted small">{t("fc.roseHelp", { n: roseData.n, mean: fmt(roseData.mean_speed, 1) })}</p>
                </>
              ) : (
                <p className="muted">{t("fc.noData")}</p>
              )}
            </section>
            <section className="chart-card">
              <h4>{t("fc.comparison", { height })}</h4>
              {ok.length < 2 ? (
                <p className="muted">{t("fc.needTwo")}</p>
              ) : cmp.data ? (
                <>
                  <table className="table compact">
                    <thead>
                      <tr>
                        <th>{t("fc.model")}</th>
                        <th>{t("fc.mean")}</th>
                        <th>{t("fc.std")}</th>
                        <th>{t("fc.bias")}</th>
                        <th>RMSD</th>
                        <th>r</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cmp.data.stats.map((s) => (
                        <tr key={s.model}>
                          <td>
                            <span className="swatch" style={{ background: color(s.model) }} /> {name(s.model)}
                          </td>
                          <td>{fmt(s.mean, 2)}</td>
                          <td>{fmt(s.std, 2)}</td>
                          <td>{(s.bias_vs_mmm > 0 ? "+" : "") + fmt(s.bias_vs_mmm, 2)}</td>
                          <td>{fmt(s.rmsd_vs_mmm, 2)}</td>
                          <td>{fmt(s.corr_vs_mmm, 2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="muted small">{t("fc.comparisonHelp", { n: cmp.data.n_common_times })}</p>
                  <Plot
                    height={240}
                    data={[
                      ...cmp.data.models.map((m) => ({
                        x: cmp.data!.times.map((s) => s.slice(0, 19)),
                        y: cmp.data!.values[m],
                        type: "scatter",
                        mode: "lines",
                        line: { color: color(m) },
                        name: name(m),
                      })),
                      {
                        x: cmp.data.times.map((s) => s.slice(0, 19)),
                        y: cmp.data.multi_model_mean,
                        type: "scatter",
                        mode: "lines",
                        line: { color: "#111", dash: "dash" },
                        name: t("fc.mmm"),
                      },
                    ]}
                    layout={{ yaxis: { title: "m/s" }, xaxis: { title: t("fc.axisUtc") } }}
                  />
                </>
              ) : (
                <p className="muted">{t("common.loading")}</p>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
