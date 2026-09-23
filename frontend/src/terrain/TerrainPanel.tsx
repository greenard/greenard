import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { del, get, post, put, streamTask } from "../api/client";
import type { CrsItem, Task, TerrainLayer } from "../api/types";
import { errorText, fmt } from "../utils/format";

export interface TerrainList {
  layers: TerrainLayer[];
  default_z0_table: Record<string, { label: string; z0: number }>;
}

type Z0Table = Record<string, { label: string; z0: number }>;

function Z0Editor({ layer, onSaved }: { layer: TerrainLayer; onSaved: () => void }) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<Record<string, { label: string; z0: string }>>({});
  useEffect(() => {
    setRows(Object.fromEntries(Object.entries(layer.z0_table as Z0Table).map(([k, v]) => [k, { label: v.label, z0: String(v.z0) }])));
  }, [layer.id, layer.z0_table]);
  const save = useMutation({
    mutationFn: () =>
      put(`/terrain/${layer.id}/z0-table`, {
        table: Object.fromEntries(Object.entries(rows).map(([k, v]) => [k, { label: v.label, z0: Number(v.z0.replace(",", ".")) }])),
      }),
    onSuccess: onSaved,
  });
  const shares: Record<string, number> = layer.stats?.classes_pct ?? {};
  return (
    <div className="z0-editor">
      <table className="table compact">
        <thead>
          <tr>
            <th>{t("terrain.class")}</th>
            <th>{t("terrain.label")}</th>
            <th className="num">{t("terrain.share")}</th>
            <th>z0 (m)</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(rows).map(([k, v]) => (
            <tr key={k}>
              <td>{k}</td>
              <td>{v.label}</td>
              <td className="num">{shares[k] !== undefined ? `${fmt(shares[k], 1)} %` : "—"}</td>
              <td>
                <input className="short" value={v.z0} onChange={(e) => setRows({ ...rows, [k]: { ...v, z0: e.target.value } })} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <button className="primary" onClick={() => save.mutate()} disabled={save.isPending}>
          {t("terrain.applyZ0")}
        </button>
        {save.isSuccess && <span className="alert ok small">{t("terrain.z0Updated")}</span>}
      </div>
      {save.isError && <div className="alert error">{errorText(t, save.error)}</div>}
    </div>
  );
}

interface Props {
  projectId: number;
  canEdit: boolean;
  crsList: CrsItem[];
  shown: Record<number, boolean>;
  setShown: (v: Record<number, boolean>) => void;
}

export default function TerrainPanel({ projectId, canEdit, crsList, shown, setShown }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const layers = useQuery({ queryKey: ["terrain", projectId], queryFn: () => get<TerrainList>(`/projects/${projectId}/terrain`) });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["terrain", projectId] });
  const [margin, setMargin] = useState("5");
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState("");
  const [mapCrs, setMapCrs] = useState("EPSG:32629");
  const [z0Layer, setZ0Layer] = useState<number | null>(null);

  const download = async (kind: "dem" | "landcover") => {
    setError("");
    try {
      const tk = await post<Task>(`/projects/${projectId}/terrain/download`, { kind, margin_km: Number(margin) });
      setTask(tk);
      await streamTask<Task>(tk.id, setTask);
    } catch (e) {
      setError(errorText(t, e));
    } finally {
      refresh();
    }
  };
  const upload = async (path: string, file: File, extra: Record<string, string> = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    for (const [k, v] of Object.entries(extra)) fd.append(k, v);
    setError("");
    try {
      await post(path, fd);
      refresh();
    } catch (e) {
      setError(errorText(t, e));
    }
  };
  const remove = useMutation({ mutationFn: (id: number) => del(`/terrain/${id}`), onSuccess: refresh });
  const running = task && !["success", "failure"].includes(task.status);
  const lc = layers.data?.layers.find((l) => l.id === z0Layer);

  return (
    <div className="panel-grid">
      {canEdit && (
        <section className="card">
          <h4>{t("terrain.sources")}</h4>
          <p className="muted small">{t("terrain.sourcesHelp")}</p>
          <div className="row">
            <label className="narrow">
              {t("terrain.margin")}
              <input value={margin} onChange={(e) => setMargin(e.target.value)} />
            </label>
            <button onClick={() => void download("dem")} disabled={!!running}>
              {t("terrain.downloadDem")}
            </button>
            <button onClick={() => void download("landcover")} disabled={!!running}>
              {t("terrain.downloadLc")}
            </button>
          </div>
          {task && (
            <div className="progress-box">
              <progress value={task.progress} max={1} />
              <span className={`status ${task.status}`}>{task.message}</span>
            </div>
          )}
          <div className="row">
            <label>
              {t("terrain.uploadDem")}
              <input type="file" accept=".tif,.tiff" onChange={(e) => e.target.files?.[0] && void upload(`/projects/${projectId}/terrain/upload-dem`, e.target.files[0])} />
            </label>
          </div>
          <div className="row">
            <label className="narrow wide">
              {t("terrain.mapCrs")}
              <select value={mapCrs} onChange={(e) => setMapCrs(e.target.value)}>
                {crsList.map((c) => (
                  <option key={c.crs} value={c.crs}>
                    {c.crs} · {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("terrain.uploadMap")}
              <input
                type="file"
                accept=".map"
                onChange={(e) => e.target.files?.[0] && void upload(`/projects/${projectId}/terrain/wasp-map`, e.target.files[0], { crs: mapCrs })}
              />
            </label>
          </div>
          <p className="muted small">{t("terrain.mapHelp")}</p>
          {error && <div className="alert error">{error}</div>}
        </section>
      )}
      <section className="card">
        <h4>{t("terrain.layers")}</h4>
        {layers.data?.layers.length === 0 && <p className="muted">{t("terrain.none")}</p>}
        <table className="table compact">
          <thead>
            <tr>
              <th>{t("terrain.show")}</th>
              <th>{t("terrain.name")}</th>
              <th>{t("terrain.kind")}</th>
              <th>{t("terrain.resolution")}</th>
              <th>{t("terrain.summary")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {layers.data?.layers.map((l) => (
              <tr key={l.id}>
                <td>
                  {l.kind !== "landcover" && (
                    <input type="checkbox" checked={!!shown[l.id]} onChange={(e) => setShown({ ...shown, [l.id]: e.target.checked })} />
                  )}
                </td>
                <td>
                  {l.name}
                  <br />
                  <small className="muted">{l.source}</small>
                </td>
                <td>{t(`terrain.kinds.${l.kind}`)}</td>
                <td>{l.resolution_m ? `${fmt(l.resolution_m, 0)} m` : "—"}</td>
                <td className="small">
                  {l.kind === "dem" && l.stats?.min_m !== undefined && `${fmt(l.stats.min_m, 0)} – ${fmt(l.stats.max_m, 0)} m`}
                  {l.kind === "roughness" && l.stats?.z0_log_mean_m != null && `${t("terrain.z0Mean")} ${fmt(l.stats.z0_log_mean_m, 3)} m`}
                  {l.kind === "roughness_map" && `${l.stats?.roughness_lines ?? 0} ${t("terrain.rLines")} · ${l.stats?.contour_lines ?? 0} ${t("terrain.cLines")}`}
                  {l.kind === "landcover" && canEdit && (
                    <button className="link" onClick={() => setZ0Layer(z0Layer === l.id ? null : l.id)}>
                      {t("terrain.editZ0")}
                    </button>
                  )}
                </td>
                <td>
                  {canEdit && (
                    <button className="link small" onClick={() => confirm(t("common.confirmDelete")) && remove.mutate(l.id)}>
                      ✕
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {lc && <Z0Editor layer={lc} onSaved={refresh} />}
      </section>
    </div>
  );
}
