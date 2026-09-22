import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { get, patch, post } from "../api/client";
import type { Availability, GridPoint, GridPointsResponse, ModelInfo, Site, Task } from "../api/types";
import { codeText, errorText, fmt } from "../utils/format";

interface Props {
  site: Site;
  models: ModelInfo[];
  canEdit: boolean;
  visible: Record<string, boolean>;
  setVisible: (v: Record<string, boolean>) => void;
  points: GridPoint[];
}

export function useGridPoints(siteId: number | null) {
  return useQuery({
    queryKey: ["gridpoints", siteId],
    queryFn: () => get<GridPointsResponse>(`/sites/${siteId}/grid-points`),
    enabled: siteId !== null,
  });
}

export default function GridPointsPanel({ site, models, canEdit, visible, setVisible, points }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const availability = useQuery({
    queryKey: ["availability", site.id],
    queryFn: () => get<Availability[]>(`/sites/${site.id}/model-availability`),
  });
  const avail = useMemo(() => Object.fromEntries((availability.data ?? []).map((a) => [a.model, a])), [availability.data]);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [method, setMethod] = useState<"bracket" | "nearest">("bracket");
  const [n, setN] = useState(4);
  const [taskId, setTaskId] = useState<number | null>(null);

  useEffect(() => {
    // par défaut : modèles déterministes disponibles pour ce site
    setChosen(Object.fromEntries(models.map((m) => [m.code, m.milestone === 1 && avail[m.code]?.available !== false])));
  }, [models, avail]);

  const task = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => get<Task>(`/tasks/${taskId}`),
    enabled: taskId !== null,
    refetchInterval: (q) => (q.state.data && ["success", "failure"].includes(q.state.data.status) ? false : 1000),
  });
  useEffect(() => {
    if (task.data && ["success", "failure"].includes(task.data.status)) {
      void qc.invalidateQueries({ queryKey: ["gridpoints", site.id] });
      void qc.invalidateQueries({ queryKey: ["sites"] });
      void qc.invalidateQueries({ queryKey: ["models"] });
    }
  }, [task.data, qc, site.id]);

  const run = useMutation({
    mutationFn: () =>
      post<Task>(`/sites/${site.id}/grid-points`, {
        models: Object.keys(chosen).filter((k) => chosen[k]),
        method,
        n,
      }),
    onSuccess: (tk) => setTaskId(tk.id),
  });
  const toggle = useMutation({
    mutationFn: (p: GridPoint) => patch<GridPoint>(`/sites/${site.id}/grid-points/${p.grid_point_id}`, { selected: !p.selected }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["gridpoints", site.id] }),
  });

  const running = task.data && !["success", "failure"].includes(task.data.status);
  const byModel = useMemo(() => {
    const m: Record<string, GridPoint[]> = {};
    for (const p of points) (m[p.model] ??= []).push(p);
    return m;
  }, [points]);
  const selectedCount = points.filter((p) => p.selected).length;
  const alertM = 100;

  return (
    <section className="card">
      <h3>{t("grid.title")}</h3>
      <div className="models">
        {models.map((m) => {
          const a = avail[m.code];
          const off = a && !a.available;
          return (
            <div key={m.code} className={off ? "model off" : "model"}>
              <label className="check">
                <input
                  type="checkbox"
                  disabled={off || !canEdit}
                  checked={!!chosen[m.code] && !off}
                  onChange={(e) => setChosen({ ...chosen, [m.code]: e.target.checked })}
                />
                <span className="swatch" style={{ background: m.color }} />
                <b>{m.name}</b> <small className="muted">{m.resolution}</small>
              </label>
              <label className="check small" title={t("grid.layers")}>
                <input
                  type="checkbox"
                  checked={visible[m.code] !== false}
                  onChange={(e) => setVisible({ ...visible, [m.code]: e.target.checked })}
                />
                👁
              </label>
              <small className="muted">
                {m.milestone > 1
                  ? t("grid.milestone2")
                  : m.invariants_status.ready
                    ? t("grid.invariantsReady")
                    : m.invariants_status.unavailable
                      ? t("grid.invariantsUnavailable")
                      : t("grid.invariantsMissing")}
              </small>
              {off && <div className="alert warning small">{codeText(t, a.message_code, a.params)}</div>}
            </div>
          );
        })}
      </div>
      {canEdit && (
        <div className="row">
          <label>
            {t("grid.method")}
            <select value={method} onChange={(e) => setMethod(e.target.value as "bracket" | "nearest")}>
              <option value="bracket">{t("grid.bracket")}</option>
              <option value="nearest">{t("grid.nearest")}</option>
            </select>
          </label>
          {method === "nearest" && (
            <label className="narrow">
              {t("grid.n")}
              <select value={n} onChange={(e) => setN(Number(e.target.value))}>
                {[4, 9, 16].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
          )}
          <button className="primary" disabled={!!running || run.isPending} onClick={() => run.mutate()}>
            {running ? t("grid.computing") : t("grid.compute")}
          </button>
        </div>
      )}
      {running && (
        <progress max={1} value={task.data?.progress ?? 0}>
          {task.data?.message}
        </progress>
      )}
      {run.isError && <div className="alert error">{errorText(t, run.error)}</div>}
      {task.data?.status === "failure" && (
        <div className="alert error">
          {t("grid.taskFailed")} :{" "}
          {codeText(t, task.data.result?.error?.code ?? "INTERNAL_ERROR", task.data.result?.error?.params, task.data.message)}
        </div>
      )}
      {(task.data?.result?.warnings ?? []).map((w: any, k: number) => (
        <div key={k} className="alert warning">
          {t("grid.warning")} ({w.model}) : {codeText(t, w.code, w.params, w.message)}
        </div>
      ))}

      {points.length === 0 ? (
        <p className="muted">{t("grid.noPoints")}</p>
      ) : (
        <p className="muted">{t("grid.selectedCount", { count: selectedCount })}</p>
      )}
      {Object.entries(byModel).map(([code, pts]) => {
        const m = models.find((x) => x.code === code);
        return (
          <div key={code} className="gp-model">
            <h4>
              <span className="swatch" style={{ background: m?.color }} /> {m?.name ?? code}
            </h4>
            <div className="table-scroll">
              <table className="table compact">
                <thead>
                  <tr>
                    <th>{t("grid.select")}</th>
                    <th>{t("grid.index")}</th>
                    <th>{t("grid.lat")}</th>
                    <th>{t("grid.lon")}</th>
                    <th>{t("grid.dist")}</th>
                    <th>{t("grid.az")}</th>
                    <th>{t("grid.zModel")}</th>
                    <th>{t("grid.zDem")}</th>
                    <th>{t("grid.dz")}</th>
                    <th>{t("grid.land")}</th>
                  </tr>
                </thead>
                <tbody>
                  {pts.map((p) => (
                    <tr key={p.grid_point_id} className={p.selected ? "selected" : ""}>
                      <td>
                        <input type="checkbox" checked={p.selected} disabled={!canEdit} onChange={() => toggle.mutate(p)} />
                      </td>
                      <td>
                        {p.i !== null ? `${p.i},${p.j}` : p.native_index}
                        {p.contains_site && <span className="badge">{t("grid.contains")}</span>}
                      </td>
                      <td>{p.lat.toFixed(4)}</td>
                      <td>{p.lon.toFixed(4)}</td>
                      <td>{fmt(p.distance_m / 1000, 2)}</td>
                      <td>{fmt(p.azimuth_deg, 0)}°</td>
                      <td>{fmt(p.model_elevation_m, 0)}</td>
                      <td>
                        {fmt(p.dem_elevation_m, 0)} / {fmt(p.dem_cell_mean_m, 0)}
                      </td>
                      <td className={p.elevation_alert ? "alert-cell" : ""} title={p.elevation_alert ? t("grid.elevAlert", { value: alertM }) : ""}>
                        {p.elevation_diff_m !== null && p.elevation_diff_m > 0 ? "+" : ""}
                        {fmt(p.elevation_diff_m, 0)} {p.elevation_alert && "⚠"}
                      </td>
                      <td className={p.land_sea_mismatch ? "alert-cell" : ""} title={p.land_sea_mismatch ? t("grid.landSeaAlert") : ""}>
                        {p.is_land === null ? "—" : p.is_land ? t("grid.landYes") : t("grid.landNo")}
                        {p.land_fraction !== null && ` (${fmt(p.land_fraction * 100, 0)} %)`} {p.land_sea_mismatch && "⚠"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </section>
  );
}
