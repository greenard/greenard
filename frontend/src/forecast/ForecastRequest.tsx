import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { get, post } from "../api/client";
import type { DataSources, EstimateResponse, ForecastJob, ForecastParams, GridPoint, ModelInfo } from "../api/types";
import { codeText, errorText, fmt } from "../utils/format";

const HEIGHTS = [10, 20, 30, 40, 50, 80, 100, 120, 150, 180];
const LEVELS = [925, 850];
const SURFACE = ["gust_10m", "t_2m", "rh_2m", "sp", "msl"];
const LEADS = [24, 48, 72, 120, 168, 240, 360, 384];
const SOURCES = ["auto", "nomads", "aws", "ecmwf", "dwd", "open_meteo"];

interface Props {
  siteId: number;
  models: ModelInfo[];
  points: GridPoint[];
  canEdit: boolean;
  onStarted: (job: ForecastJob) => void;
}

export default function ForecastRequest({ siteId, models, points, canEdit, onStarted }: Props) {
  const { t } = useTranslation();
  const selectedByModel = useMemo(() => {
    const m: Record<string, number> = {};
    for (const p of points) if (p.selected) m[p.model] = (m[p.model] ?? 0) + 1;
    return m;
  }, [points]);
  const usable = models.filter((m) => selectedByModel[m.code]);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [runMode, setRunMode] = useState<"latest" | "past">("latest");
  const [runTime, setRunTime] = useState("");
  const [params, setParams] = useState<Omit<ForecastParams, "models" | "run">>({
    max_lead_h: 240,
    wind_heights_m: [10, 100],
    pressure_levels_hpa: [],
    surface: [...SURFACE],
    source: "auto",
    members: null,
    accept_paid: false,
  });
  const sources = useQuery({ queryKey: ["data-sources"], queryFn: () => get<DataSources>("/data-sources") });
  const paidAvailable = sources.data?.sources.some((s) => s.cost === "paid" && s.enabled) ?? false;

  const body = (): ForecastParams => ({
    ...params,
    models: usable.filter((m) => chosen[m.code] ?? true).map((m) => m.code),
    run: runMode === "latest" || !runTime ? "latest" : `${runTime}:00Z`,
  });
  const estimate = useMutation({ mutationFn: () => post<EstimateResponse>(`/sites/${siteId}/forecasts/estimate`, body()) });
  const start = useMutation({
    mutationFn: () => post<ForecastJob>(`/sites/${siteId}/forecasts`, body()),
    onSuccess: onStarted,
  });
  const toggle = <K extends "wind_heights_m" | "pressure_levels_hpa" | "surface">(key: K, v: ForecastParams[K][number]) => {
    const list = params[key] as (typeof v)[];
    setParams({ ...params, [key]: list.includes(v) ? list.filter((x) => x !== v) : [...list, v].sort() });
  };

  if (usable.length === 0) return <p className="muted">{t("fc.noSelection")}</p>;

  return (
    <div className="fc-request">
      <div className="fc-grid">
        <fieldset>
          <legend>{t("grid.models")}</legend>
          {usable.map((m) => (
            <label key={m.code} className="check">
              <input
                type="checkbox"
                checked={chosen[m.code] ?? true}
                onChange={(e) => setChosen({ ...chosen, [m.code]: e.target.checked })}
              />
              <span className="swatch" style={{ background: m.color }} /> {m.name}
              <small className="muted">
                {t("fc.points", { count: selectedByModel[m.code] })}
                {m.members > 1 && ` · ${t("fc.members", { count: m.members })}`}
              </small>
            </label>
          ))}
        </fieldset>
        <fieldset>
          <legend>{t("fc.run")}</legend>
          <label className="check">
            <input type="radio" checked={runMode === "latest"} onChange={() => setRunMode("latest")} /> {t("fc.latest")}
          </label>
          <label className="check">
            <input type="radio" checked={runMode === "past"} onChange={() => setRunMode("past")} /> {t("fc.archive")}
          </label>
          {runMode === "past" && (
            <label>
              {t("fc.runTime")}
              <input type="datetime-local" step={3600} value={runTime} onChange={(e) => setRunTime(e.target.value)} />
              <small className="muted">{t("fc.archiveHelp")}</small>
            </label>
          )}
          <label>
            {t("fc.horizon")}
            <select value={params.max_lead_h} onChange={(e) => setParams({ ...params, max_lead_h: Number(e.target.value) })}>
              {LEADS.map((h) => (
                <option key={h} value={h}>
                  {h} h ({fmt(h / 24, 1)} {t("fc.days")})
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("fc.source")}
            <select value={params.source} onChange={(e) => setParams({ ...params, source: e.target.value })}>
              {SOURCES.map((s) => (
                <option key={s} value={s}>
                  {t(`fc.sources.${s}`)}
                </option>
              ))}
            </select>
          </label>
        </fieldset>
        <fieldset>
          <legend>{t("fc.heights")}</legend>
          <div className="chips">
            {HEIGHTS.map((h) => (
              <label key={h} className="chip">
                <input type="checkbox" checked={params.wind_heights_m.includes(h)} onChange={() => toggle("wind_heights_m", h)} />
                {h} m
              </label>
            ))}
          </div>
          <legend className="sub">{t("fc.levels")}</legend>
          <div className="chips">
            {LEVELS.map((p) => (
              <label key={p} className="chip">
                <input type="checkbox" checked={params.pressure_levels_hpa.includes(p)} onChange={() => toggle("pressure_levels_hpa", p)} />
                {p} hPa
              </label>
            ))}
          </div>
          <legend className="sub">{t("fc.surface")}</legend>
          <div className="chips">
            {SURFACE.map((v) => (
              <label key={v} className="chip">
                <input type="checkbox" checked={params.surface.includes(v)} onChange={() => toggle("surface", v)} />
                {t(`fc.var.${v}`)}
              </label>
            ))}
          </div>
          {paidAvailable && (
            <label className="check paid">
              <input type="checkbox" checked={params.accept_paid} onChange={(e) => setParams({ ...params, accept_paid: e.target.checked })} />
              {t("fc.acceptPaid")}
            </label>
          )}
        </fieldset>
      </div>
      <div className="row">
        <button onClick={() => estimate.mutate()} disabled={estimate.isPending}>
          {estimate.isPending ? t("common.loading") : t("fc.estimate")}
        </button>
        {canEdit && (
          <button className="primary" onClick={() => start.mutate()} disabled={start.isPending}>
            {t("fc.download")}
          </button>
        )}
      </div>
      {(estimate.isError || start.isError) && <div className="alert error">{errorText(t, estimate.error ?? start.error)}</div>}
      {estimate.data && <EstimateTable data={estimate.data} />}
    </div>
  );
}

function EstimateTable({ data }: { data: EstimateResponse }) {
  const { t } = useTranslation();
  return (
    <div className="table-scroll">
      <table className="table compact">
        <caption>{t("fc.estimateTitle", { limit: data.limit_mb })}</caption>
        <thead>
          <tr>
            <th>{t("fc.model")}</th>
            <th>{t("fc.source")}</th>
            <th>{t("fc.run")}</th>
            <th>{t("fc.steps")}</th>
            <th>{t("fc.volume")}</th>
            <th>{t("fc.licence")}</th>
            <th>{t("fc.missing")}</th>
          </tr>
        </thead>
        <tbody>
          {data.models.flatMap((m) =>
            m.error
              ? [
                  <tr key={m.model}>
                    <td>{m.model}</td>
                    <td colSpan={6} className="alert-cell">
                      {codeText(t, m.error.code, m.error.params)}
                    </td>
                  </tr>,
                ]
              : m.candidates.map((c, k) => (
                  <tr key={`${m.model}-${c.source}`} className={c.error ? "muted" : c.over_limit ? "row-warning" : ""}>
                    <td>{k === 0 ? m.model : ""}</td>
                    <td>{t(`fc.sources.${c.source}`)}</td>
                    <td>{c.run ? c.run.slice(0, 13).replace("T", " ") + "Z" : "—"}</td>
                    <td>{c.estimate ? `${c.estimate.n_steps} (${c.estimate.first_lead_h}–${c.estimate.last_lead_h} h)` : "—"}</td>
                    <td>
                      {c.estimate?.megabytes == null ? "—" : c.estimate.megabytes < 1 ? "< 1 Mo" : `${fmt(c.estimate.megabytes, 0)} Mo`}
                      {c.over_limit && ` ⚠ ${t("fc.overLimit")}`}
                    </td>
                    <td>{c.licence ? `${t(`fc.lic.${c.licence}`)} · ${t(`fc.cost.${c.cost}`)}` : "—"}</td>
                    <td>
                      {c.error ? codeText(t, c.error.code, c.error.params, c.error.message) : (c.estimate?.missing_variables ?? []).join(", ")}
                    </td>
                  </tr>
                )),
          )}
        </tbody>
      </table>
    </div>
  );
}
