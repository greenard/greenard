import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { del, get, post } from "../api/client";
import type { MappingRow, Mast, MastAnalysis, MastPreview, MastQc, MastSeries } from "../api/types";
import Plot from "../charts/Plot";
import { codeText, errorText, fmt } from "../utils/format";

const KINDS = ["", "ws", "wd", "temp", "rh", "pressure"];
const STATS = ["mean", "sd", "max", "min"];
const TIMEZONES = ["UTC", "UTC+01:00", "UTC-01:00", "Africa/Casablanca"];
const ROSE_COLORS = ["#d9f0e3", "#a6dbbf", "#69c095", "#2f9c6c", "#0b6e4f", "#063d2c"];
const FLAG_COLORS: Record<string, string> = {
  missing: "#9aa3ad", range: "#b42318", stuck: "#7a5af8", icing: "#2e90fa", tower_shadow: "#f79009", spike: "#dd2590", shear: "#667085",
};

function ImportWizard({ mast, onDone }: { mast: Mast; onDone: () => void }) {
  const { t } = useTranslation();
  const [preview, setPreview] = useState<MastPreview | null>(null);
  const [mapping, setMapping] = useState<MappingRow[]>([]);
  const [tz, setTz] = useState("UTC");
  const [convention, setConvention] = useState<"start" | "end">("end");
  const [dayfirst, setDayfirst] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<Record<string, unknown> | null>(null);

  const upload = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f);
    setError("");
    setReport(null);
    try {
      const p = await post<MastPreview>(`/masts/${mast.id}/data/preview`, fd);
      setPreview(p);
      setConvention(p.default_convention);
      const byCol = Object.fromEntries(p.suggested_mapping.map((m) => [m.column, m]));
      setMapping(
        p.columns
          .filter((c) => c !== p.time_column)
          .map((c) => byCol[c] ?? { column: c, kind: null, stat: "mean", height_m: null, boom_dir_deg: null }),
      );
    } catch (e) {
      setError(errorText(t, e));
    }
  };
  const set = (k: number, patch: Partial<MappingRow>) => setMapping(mapping.map((m, i) => (i === k ? { ...m, ...patch } : m)));
  const doImport = async () => {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      const r = await post<{ report: Record<string, unknown> }>(`/masts/${mast.id}/data/import`, {
        token: preview.token,
        mapping: mapping.filter((m) => m.kind),
        timezone: tz,
        convention,
        dayfirst,
      });
      setReport(r.report);
      setPreview(null);
      onDone();
    } catch (e) {
      setError(errorText(t, e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
    {report && (
      <div className="alert ok small">
        {t("mast.imported", { n: report.records_in_file as number, gap: report.missing_records_inserted as number, dup: report.duplicates_removed as number })}
      </div>
    )}
    <details className="import">
      <summary>{t("mast.importData")}</summary>
      <p className="muted small">{t("mast.importHelp")}</p>
      <input type="file" accept=".csv,.txt,.dat,.tsv" onChange={(e) => { setReport(null); if (e.target.files?.[0]) void upload(e.target.files[0]); }} />
      {error && <div className="alert error">{error}</div>}
      {preview && (
        <>
          <p className="small">
            {t("mast.detected", { fmt: preview.format, n: preview.n_rows, col: preview.time_column })}
          </p>
          <div className="table-scroll short">
            <table className="table compact">
              <thead>
                <tr>{Object.keys(preview.sample[0] ?? {}).slice(0, 10).map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {preview.sample.slice(0, 4).map((r, k) => (
                  <tr key={k}>{Object.values(r).slice(0, 10).map((v, j) => <td key={j}>{v}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row">
            <label>
              {t("mast.timezone")}
              <select value={tz} onChange={(e) => setTz(e.target.value)}>
                {TIMEZONES.map((z) => <option key={z} value={z}>{t(`mast.tz.${z}`, { defaultValue: z })}</option>)}
              </select>
            </label>
            <label>
              {t("mast.convention")}
              <select value={convention} onChange={(e) => setConvention(e.target.value as "start" | "end")}>
                <option value="start">{t("mast.convStart")}</option>
                <option value="end">{t("mast.convEnd")}</option>
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={dayfirst} onChange={(e) => setDayfirst(e.target.checked)} /> {t("mast.dayfirst")}
            </label>
          </div>
          <p className="muted small">{t("mast.tzHelp")}</p>
          <div className="table-scroll">
            <table className="table compact mapping">
              <thead>
                <tr>
                  <th>{t("mast.column")}</th>
                  <th>{t("mast.kind")}</th>
                  <th>{t("mast.stat")}</th>
                  <th>{t("mast.height")}</th>
                  <th>{t("mast.boom")}</th>
                  <th>{t("mast.unit")}</th>
                </tr>
              </thead>
              <tbody>
                {mapping.map((m, k) => (
                  <tr key={m.column} className={m.kind ? "" : "muted"}>
                    <td className="small">{m.column}</td>
                    <td>
                      <select value={m.kind ?? ""} onChange={(e) => set(k, { kind: e.target.value || null })}>
                        {KINDS.map((x) => <option key={x} value={x}>{x ? t(`mast.kinds.${x}`) : t("mast.ignore")}</option>)}
                      </select>
                    </td>
                    <td>
                      <select value={m.stat} onChange={(e) => set(k, { stat: e.target.value })} disabled={!m.kind}>
                        {STATS.map((x) => <option key={x} value={x}>{t(`mast.stats.${x}`)}</option>)}
                      </select>
                    </td>
                    <td>
                      <input className="num" value={m.height_m ?? ""} disabled={!m.kind}
                        onChange={(e) => set(k, { height_m: e.target.value === "" ? null : Number(e.target.value) })} />
                    </td>
                    <td>
                      <input className="num" value={m.boom_dir_deg ?? ""} disabled={m.kind !== "ws"}
                        onChange={(e) => set(k, { boom_dir_deg: e.target.value === "" ? null : Number(e.target.value) })} />
                    </td>
                    <td>
                      <input className="num" value={m.unit ?? ""} disabled={!m.kind} placeholder={m.kind === "ws" ? "m/s" : m.kind === "temp" ? "C" : m.kind === "pressure" ? "hPa" : ""}
                        onChange={(e) => set(k, { unit: e.target.value })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="primary" disabled={busy} onClick={() => void doImport()}>
            {busy ? t("common.loading") : t("mast.runImport")}
          </button>
        </>
      )}
    </details>
    </>
  );
}

function DatasetView({ datasetId }: { datasetId: number }) {
  const { t } = useTranslation();
  const qc = useQuery({ queryKey: ["mast-qc", datasetId], queryFn: () => get<MastQc>(`/mast-datasets/${datasetId}/qc`) });
  const an = useQuery({ queryKey: ["mast-an", datasetId], queryFn: () => get<MastAnalysis>(`/mast-datasets/${datasetId}/analysis`) });
  const [window, setWindow] = useState<{ start: string; end: string } | null>(null);
  const series = useQuery({
    queryKey: ["mast-series", datasetId, window?.start, window?.end],
    queryFn: () => get<MastSeries>(`/mast-datasets/${datasetId}/series${window ? `?start=${window.start}&end=${window.end}` : ""}`),
  });
  const [focus, setFocus] = useState("");
  useEffect(() => {
    if (!focus && qc.data) setFocus(qc.data.sensors.find((s) => s.kind === "ws")?.code ?? "");
  }, [qc.data, focus]);

  const heat = useMemo(() => {
    if (!qc.data) return null;
    const s = qc.data.sensors;
    return {
      z: s.map((x) => qc.data!.months.map((m) => x.monthly_valid_pct[m] ?? null)),
      x: qc.data.months,
      y: s.map((x) => x.code),
    };
  }, [qc.data]);
  const s = series.data;
  const seriesTraces = useMemo(() => {
    if (!s) return [];
    const x = s.times.map((v) => v.slice(0, 19));
    const out: any[] = Object.entries(s.composites)
      .filter(([k]) => k.startsWith("ws@"))
      .map(([k, y]) => ({ x, y, type: "scatter", mode: "lines", name: `${k} m`, line: { width: 1.2 } }));
    const f = s.sensors[focus];
    if (f?.values && f.flags) {
      const byFlag: Record<string, number[]> = {};
      f.flags.forEach((fl, k) => {
        if (!fl) return;
        for (const [bit, name] of Object.entries(qc.data?.flag_names ?? {})) if (fl & Number(bit)) (byFlag[name] ??= []).push(k);
      });
      for (const [name, idx] of Object.entries(byFlag)) {
        if (name === "missing") continue;
        out.push({ x: idx.map((k) => x[k]), y: idx.map((k) => f.values![k]), type: "scatter", mode: "markers", name: `${focus} · ${t(`mast.flag.${name}`)}`, marker: { color: FLAG_COLORS[name], size: 6, symbol: "x" } });
      }
    }
    return out;
  }, [s, focus, qc.data, t]);
  const a = an.data;

  return (
    <div className="dataset-view">
      {qc.data && (
        <div className="table-scroll">
          <table className="table compact">
            <caption>{t("mast.qcTitle")}</caption>
            <thead>
              <tr>
                <th>{t("mast.sensor")}</th>
                <th>{t("mast.height")}</th>
                <th>{t("mast.valid")}</th>
                {Object.values(qc.data.flag_names).map((n) => <th key={n}>{t(`mast.flag.${n}`)}</th>)}
              </tr>
            </thead>
            <tbody>
              {qc.data.sensors.map((x) => (
                <tr key={x.code} className={x.code === focus ? "selected" : ""} onClick={() => setFocus(x.code)} style={{ cursor: "pointer" }}>
                  <td>{x.code}{x.boom_dir_deg != null && <small className="muted"> ({x.boom_dir_deg}°)</small>}</td>
                  <td>{x.height_m}</td>
                  <td className={x.valid_pct < 80 ? "alert-cell" : ""}>{fmt(x.valid_pct, 1)} %</td>
                  {Object.values(qc.data!.flag_names).map((n) => <td key={n}>{x.flags[n] || ""}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">{t("mast.qcHelp")}</p>
        </div>
      )}
      <div className="chart-row">
        {heat && (
          <section className="chart-card">
            <h4>{t("mast.completeness")}</h4>
            <Plot height={Math.max(220, 26 * heat.y.length + 90)} data={[{ type: "heatmap", ...heat, zmin: 0, zmax: 100, colorscale: [[0, "#b42318"], [0.8, "#fdb022"], [0.95, "#a6dbbf"], [1, "#0b6e4f"]], colorbar: { ticksuffix: " %" }, hovertemplate: "%{y} %{x}: %{z:.1f} %<extra></extra>" }]}
              layout={{ margin: { l: 90, r: 20, t: 20, b: 50 }, hovermode: "closest", xaxis: { type: "category", tickangle: -45 } }} />
          </section>
        )}
        {a?.wind_rose && (
          <section className="chart-card">
            <h4>{t("mast.rose", { h: a.wind_rose.height_m })}</h4>
            <Plot height={300} data={a.wind_rose.speed_bins.map((b, k) => ({ type: "barpolar", r: a.wind_rose!.frequency_pct[k], theta: a.wind_rose!.sectors_deg, name: `${b} m/s`, marker: { color: ROSE_COLORS[k] } }))}
              layout={{ polar: { angularaxis: { direction: "clockwise", rotation: 90 }, radialaxis: { ticksuffix: " %", angle: 45, tickangle: 45, tickfont: { size: 9 } } }, hovermode: "closest", legend: { orientation: "v", x: 1.05, y: 0.5 } }} />
          </section>
        )}
      </div>
      <section className="chart-card">
        <h4>
          {t("mast.series")} <small className="muted">({s?.resample})</small>{" "}
          {s && s.resample !== "10min" && a && (
            <button className="link small" onClick={() => {
              // 10 jours autour du premier défaut du capteur affiché, sinon début de la période
              const ev = qc.data?.sensors.find((x) => x.code === focus)?.first_event_utc;
              const d0 = new Date(ev ?? a.overview.start_utc.slice(0, 10));
              if (ev) d0.setUTCDate(d0.getUTCDate() - 3);
              const d1 = new Date(d0);
              d1.setUTCDate(d1.getUTCDate() + 10);
              setWindow({ start: d0.toISOString().slice(0, 10), end: d1.toISOString().slice(0, 10) });
            }}>{t("mast.zoom10d")}</button>
          )}
          {window && <button className="link small" onClick={() => setWindow(null)}>{t("mast.fullPeriod")}</button>}
        </h4>
        <Plot data={seriesTraces} height={320} layout={{ yaxis: { title: "m/s", rangemode: "tozero" }, xaxis: { title: t("fc.axisUtc") } }} />
        <p className="muted small">{t("mast.seriesHelp")}</p>
      </section>
      {a && (
        <div className="chart-row">
          <section className="chart-card">
            <h4>{a.shear ? t("mast.shear", { z1: a.shear.z_low_m, z2: a.shear.z_high_m, a: fmt(a.shear.alpha_all, 3) }) : t("mast.noShear")}</h4>
            {a.shear?.by_sector && (
              <Plot height={260}
                data={[
                  { type: "bar", x: a.shear.by_sector.map((r) => `${r.sector_deg}°`), y: a.shear.by_sector.map((r) => r.alpha), name: t("mast.bySector"), marker: { color: "#0b6e4f" } },
                ]}
                layout={{ yaxis: { title: "α" }, hovermode: "closest" }} />
            )}
            {a.shear && (
              <Plot height={220}
                data={[{ type: "scatter", mode: "lines+markers", x: a.shear.by_hour_utc.map((r) => Number(r.key)), y: a.shear.by_hour_utc.map((r) => r.alpha), name: t("mast.byHour"), line: { color: "#b35c00" } }]}
                layout={{ xaxis: { title: t("mast.hourUtc"), dtick: 3 }, yaxis: { title: "α" } }} />
            )}
            {a.shear && <p className="muted small">{t("mast.seasons")} : {a.shear.by_season.map((r) => `${r.key} ${fmt(r.alpha, 3)}`).join(" · ")}</p>}
          </section>
          <section className="chart-card">
            <h4>{a.turbulence ? t("mast.ti", { h: a.turbulence.height_m, ti: fmt(a.turbulence.ti_mean * 100, 1) }) : t("mast.noTi")}</h4>
            {a.turbulence && (
              <Plot height={260}
                data={[
                  { type: "scatter", mode: "lines+markers", x: a.turbulence.by_speed.map((r) => r.ws_bin), y: a.turbulence.by_speed.map((r) => r.ti_mean), name: t("mast.tiMean"), line: { color: "#0b6e4f" } },
                  { type: "scatter", mode: "lines", x: a.turbulence.by_speed.map((r) => r.ws_bin), y: a.turbulence.by_speed.map((r) => r.ti_rep), name: t("mast.tiRep"), line: { color: "#b42318", dash: "dash" } },
                ]}
                layout={{ xaxis: { title: "m/s" }, yaxis: { title: "TI", tickformat: ".0%" } }} />
            )}
            <table className="table compact">
              <tbody>
                <tr><th>{t("mast.density")}</th><td>{a.air_density ? `${fmt(a.air_density.mean, 3)} kg/m³ (${fmt(a.air_density.min, 3)} – ${fmt(a.air_density.max, 3)})` : "—"}</td></tr>
                {Object.entries(a.overview.ws_heights).map(([h, v]) => (
                  <tr key={h}><th>{t("mast.meanAt", { h })}</th><td>{fmt(v.mean, 2)} m/s · {fmt(v.valid_pct, 1)} %</td></tr>
                ))}
                {!a.z0_by_sector && <tr><th>{t("mast.z0")}</th><td className="small">{t("mast.noZ0")}</td></tr>}
              </tbody>
            </table>
            {a.z0_by_sector && (
              <>
                <h4>{t("mast.z0")}</h4>
                <Plot height={220}
                  data={[{ type: "bar", x: a.z0_by_sector.map((r) => `${r.sector_deg}°`), y: a.z0_by_sector.map((r) => r.z0_m), marker: { color: "#8a6d3b" }, hovertemplate: "%{x} : z0 = %{y:.4f} m<extra></extra>" }]}
                  layout={{ yaxis: { title: "z0 (m)", type: "log" }, xaxis: { type: "category" }, margin: { t: 10 } }} />
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

export default function MastPanel({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const masts = useQuery({ queryKey: ["masts", projectId], queryFn: () => get<Mast[]>(`/projects/${projectId}/masts`) });
  const [form, setForm] = useState({ name: "", lat: "", lon: "" });
  const [selected, setSelected] = useState<number | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["masts", projectId] });
  const create = useMutation({ mutationFn: () => post(`/projects/${projectId}/masts`, form), onSuccess: () => { setForm({ name: "", lat: "", lon: "" }); refresh(); } });
  const remove = useMutation({ mutationFn: (id: number) => del(`/masts/${id}`), onSuccess: refresh });
  const delDs = useMutation({ mutationFn: (id: number) => del(`/mast-datasets/${id}`), onSuccess: refresh });

  return (
    <div>
      {canEdit && (
        <form className="card row" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <input placeholder={t("mast.name")} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          <input placeholder={t("coord.lat")} value={form.lat} onChange={(e) => setForm({ ...form, lat: e.target.value })} required />
          <input placeholder={t("coord.lon")} value={form.lon} onChange={(e) => setForm({ ...form, lon: e.target.value })} required />
          <button>{t("mast.create")}</button>
          {create.isError && <span className="alert error">{errorText(t, create.error)}</span>}
        </form>
      )}
      {masts.data?.length === 0 && <p className="muted">{t("mast.none")}</p>}
      {masts.data?.map((m) => (
        <section key={m.id} className="card">
          <div className="row-between">
            <h3>{m.name}</h3>
            {canEdit && <button className="link small" onClick={() => remove.mutate(m.id)}>{t("common.delete")}</button>}
          </div>
          <p className="muted small">
            {m.lat.toFixed(5)}, {m.lon.toFixed(5)} · {m.input_crs} · {t("mast.elev", { z: fmt(m.elevation_m, 0) })} ·{" "}
            {t("mast.sensors", { list: m.sensors.map((s) => s.code).join(", ") || "—" })}
          </p>
          {m.datasets.map((d) => (
            <div key={d.id} className={`dataset ${selected === d.id ? "active" : ""}`}>
              <button className="link" onClick={() => setSelected(selected === d.id ? null : d.id)}>
                {d.original_filename} · {d.format.toUpperCase()} · {d.t_start?.slice(0, 10)} → {d.t_end?.slice(0, 10)} · {t("mast.records", { n: d.n_records })}
              </button>
              {canEdit && <button className="link small" onClick={() => delDs.mutate(d.id)}>✕</button>}
              {selected === d.id && <DatasetView datasetId={d.id} />}
            </div>
          ))}
          {canEdit && <ImportWizard mast={m} onDone={refresh} />}
        </section>
      ))}
      {(remove.isError || delDs.isError) && <div className="alert error">{codeText(t, "unknown")}</div>}
    </div>
  );
}
