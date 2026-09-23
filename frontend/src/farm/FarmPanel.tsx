import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { del, get, post } from "../api/client";
import type { CrsItem, CurvePoint, Farm, Issue, TurbineType } from "../api/types";
import Plot from "../charts/Plot";
import { codeText, errorText, fmt } from "../utils/format";

function IssuesTable({ issues }: { issues: Issue[] }) {
  const { t } = useTranslation();
  if (!issues.length) return null;
  return (
    <div className="table-scroll">
      <table className="table compact">
        <thead>
          <tr>
            <th>{t("site.row")}</th>
            <th>{t("site.field")}</th>
            <th>{t("site.message")}</th>
          </tr>
        </thead>
        <tbody>
          {issues.map((i, k) => (
            <tr key={k} className={i.severity === "error" ? "row-error" : "row-warning"}>
              <td>{i.row ?? ""}</td>
              <td>{i.field ?? ""}</td>
              <td>{codeText(t, i.code, i.params, i.message)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CurveChart({ curve, height = 260 }: { curve: CurvePoint[]; height?: number }) {
  const { t } = useTranslation();
  return (
    <Plot
      height={height}
      data={[
        { x: curve.map((p) => p.ws), y: curve.map((p) => p.power_kw), type: "scatter", mode: "lines+markers", name: t("farm.power"), line: { color: "#0b6e4f" }, marker: { size: 4 } },
        { x: curve.map((p) => p.ws), y: curve.map((p) => p.ct), type: "scatter", mode: "lines", name: "Ct", yaxis: "y2", line: { color: "#b35c00", dash: "dot" } },
      ]}
      layout={{
        xaxis: { title: "m/s" },
        yaxis: { title: "kW", rangemode: "tozero" },
        yaxis2: { title: "Ct", overlaying: "y", side: "right", range: [0, 1.1] },
        hovermode: "x unified",
      }}
    />
  );
}

function TypeImport({ projectId, onDone }: { projectId: number; onDone: () => void }) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState({ name: "", rotor_d_m: "", hub_heights_m: "", cut_in_ms: "", cut_out_ms: "", restart_ms: "", rho_ref: "" });
  const [res, setRes] = useState<{ ok: boolean; issues: Issue[]; type: any; created: number | null } | null>(null);
  const [error, setError] = useState("");
  const send = async (dry: boolean) => {
    if (!file) return;
    const m: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(meta)) {
      if (!v.trim()) continue;
      m[k] = k === "name" ? v : k === "hub_heights_m" ? v.split(/[;, ]+/).filter(Boolean).map(Number) : Number(v.replace(",", "."));
    }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("meta", JSON.stringify(m));
    setError("");
    try {
      const r = await post<typeof res>(`/projects/${projectId}/turbine-types/import?dry_run=${dry}`, fd);
      setRes(r);
      if (!dry && r?.created) {
        setRes(null);
        setFile(null);
        onDone();
      }
    } catch (e) {
      setError(errorText(t, e));
    }
  };
  const field = (k: keyof typeof meta, label: string) => (
    <label>
      {label}
      <input value={meta[k]} onChange={(e) => setMeta({ ...meta, [k]: e.target.value })} />
    </label>
  );
  return (
    <details className="import">
      <summary>{t("farm.importType")}</summary>
      <p className="muted small">{t("farm.importTypeHelp")}</p>
      <input type="file" accept=".wtg,.csv,.txt" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setRes(null); }} />
      <div className="row">
        {field("name", t("farm.typeName"))}
        {field("rotor_d_m", t("farm.rotor"))}
        {field("hub_heights_m", t("farm.hubs"))}
      </div>
      <div className="row">
        {field("cut_in_ms", t("farm.cutIn"))}
        {field("cut_out_ms", t("farm.cutOut"))}
        {field("restart_ms", t("farm.restart"))}
        {field("rho_ref", t("farm.rho"))}
      </div>
      <div className="row">
        <button disabled={!file} onClick={() => void send(true)}>{t("site.importCheck")}</button>
        {res?.ok && <button className="primary" onClick={() => void send(false)}>{t("farm.saveType")}</button>}
      </div>
      {error && <div className="alert error">{error}</div>}
      {res && (
        <>
          <p className="small">
            <b>{res.type.name}</b> · D {fmt(res.type.rotor_d_m, 0)} m · {fmt(res.type.rated_kw, 0)} kW · {t("farm.cutIn")} {fmt(res.type.cut_in_ms, 1)} ·{" "}
            {t("farm.cutOut")} {fmt(res.type.cut_out_ms, 1)} · ρ {res.type.rho_ref}
            {res.type.density_curves.length > 0 && ` · ${t("farm.densities", { list: res.type.density_curves.join(", ") })}`}
          </p>
          <IssuesTable issues={res.issues} />
          {res.type.power_curve.length > 0 && <CurveChart curve={res.type.power_curve} height={220} />}
        </>
      )}
    </details>
  );
}

function LayoutImport({ farm, crsList, onDone }: { farm: Farm; crsList: CrsItem[]; onDone: () => void }) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [crs, setCrs] = useState("EPSG:32629");
  const [res, setRes] = useState<{ ok: boolean; issues: Issue[]; rows: any[]; created: number } | null>(null);
  const [error, setError] = useState("");
  const send = async (dry: boolean) => {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("default_crs", crs);
    setError("");
    try {
      const r = await post<typeof res>(`/farms/${farm.id}/layout/import?dry_run=${dry}`, fd);
      setRes(r);
      if (!dry && r?.created) {
        setRes(null);
        onDone();
      }
    } catch (e) {
      setError(errorText(t, e));
    }
  };
  return (
    <details className="import">
      <summary>{t("farm.importLayout")}</summary>
      <p className="muted small">{t("farm.importLayoutHelp")}</p>
      <div className="row">
        <input type="file" accept=".csv,.txt,.xlsx,.kml,.zip" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setRes(null); }} />
        <label>
          {t("farm.defaultCrs")}
          <select value={crs} onChange={(e) => setCrs(e.target.value)}>
            {crsList.map((c) => (
              <option key={c.crs} value={c.crs}>{c.name} ({c.crs})</option>
            ))}
          </select>
        </label>
        <button disabled={!file} onClick={() => void send(true)}>{t("site.importCheck")}</button>
        {res?.ok && <button className="primary" onClick={() => void send(false)}>{t("farm.replaceLayout", { count: res.rows.length })}</button>}
      </div>
      {error && <div className="alert error">{error}</div>}
      {res && <IssuesTable issues={res.issues} />}
      {res?.ok && <p className="muted small">{t("farm.layoutOk", { count: res.rows.length })}</p>}
    </details>
  );
}

export default function FarmPanel({ projectId, canEdit, crsList }: { projectId: number; canEdit: boolean; crsList: CrsItem[] }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const types = useQuery({ queryKey: ["types", projectId], queryFn: () => get<TurbineType[]>(`/projects/${projectId}/turbine-types`) });
  const farms = useQuery({ queryKey: ["farms", projectId], queryFn: () => get<Farm[]>(`/projects/${projectId}/farms`) });
  const [shownType, setShownType] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [neighbour, setNeighbour] = useState(false);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["types", projectId] });
    void qc.invalidateQueries({ queryKey: ["farms", projectId] });
  };
  const create = useMutation({ mutationFn: () => post(`/projects/${projectId}/farms`, { name, is_neighbour: neighbour }), onSuccess: () => { setName(""); refresh(); } });
  const remove = useMutation({ mutationFn: (id: number) => del(`/farms/${id}`), onSuccess: refresh });
  const delType = useMutation({ mutationFn: (id: number) => del(`/turbine-types/${id}`), onSuccess: refresh });
  const shown = types.data?.find((x) => x.id === shownType);

  return (
    <div className="panel-grid">
      <section className="card">
        <h3>{t("farm.types")}</h3>
        {types.data?.length === 0 && <p className="muted">{t("farm.noTypes")}</p>}
        <table className="table compact">
          <thead>
            <tr>
              <th>{t("farm.typeName")}</th>
              <th>D (m)</th>
              <th>kW</th>
              <th>{t("farm.hubs")}</th>
              <th>{t("farm.cutInOut")}</th>
              <th>ρ</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {types.data?.map((ty) => (
              <tr key={ty.id} className={ty.id === shownType ? "selected" : ""}>
                <td>
                  <button className="link" onClick={() => setShownType(ty.id === shownType ? null : ty.id)}>{ty.name}</button>
                  <small className="muted"> {ty.source_format}</small>
                </td>
                <td>{fmt(ty.rotor_d_m, 0)}</td>
                <td>{fmt(ty.rated_kw, 0)}</td>
                <td>{ty.hub_heights_m.join(", ")}</td>
                <td>
                  {fmt(ty.cut_in_ms, 1)} / {fmt(ty.cut_out_ms, 1)}
                  {ty.restart_ms != null && ` (↺ ${fmt(ty.restart_ms, 1)})`}
                </td>
                <td>{ty.rho_ref}{ty.density_curves.length > 0 && ` +${ty.density_curves.length}`}</td>
                <td>
                  {canEdit && ty.turbines_using === 0 && (
                    <button className="link small" onClick={() => delType.mutate(ty.id)}>✕</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {shown && <CurveChart curve={shown.power_curve} />}
        {canEdit && <TypeImport projectId={projectId} onDone={refresh} />}
        {delType.isError && <div className="alert error">{errorText(t, delType.error)}</div>}
      </section>

      <section className="card">
        <h3>{t("farm.farms")}</h3>
        {canEdit && (
          <form className="row" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
            <input placeholder={t("farm.farmName")} value={name} onChange={(e) => setName(e.target.value)} required />
            <label className="check">
              <input type="checkbox" checked={neighbour} onChange={(e) => setNeighbour(e.target.checked)} /> {t("farm.neighbour")}
            </label>
            <button>{t("farm.newFarm")}</button>
          </form>
        )}
        {farms.data?.map((f) => (
          <div key={f.id} className="farm">
            <div className="row-between">
              <h4>
                {f.name} {f.is_neighbour && <span className="badge">{t("farm.neighbour")}</span>}
              </h4>
              {canEdit && <button className="link small" onClick={() => remove.mutate(f.id)}>{t("common.delete")}</button>}
            </div>
            <p className="muted small">
              {t("farm.summary", { n: f.n_turbines, mw: fmt(f.capacity_mw, 1) })}
              {f.spacing.min_m != null && ` · ${t("farm.spacing", { m: fmt(f.spacing.min_m, 0), d: fmt(f.spacing.min_d, 1) })}`}
              {f.layout_filename && ` · ${f.layout_filename}`}
            </p>
            {f.turbines.length > 0 && (
              <div className="table-scroll short">
                <table className="table compact">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>{t("farm.type")}</th>
                      <th>{t("farm.hub")}</th>
                      <th>Lat</th>
                      <th>Lon</th>
                      <th>{t("farm.input")}</th>
                      <th>{t("farm.elev")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {f.turbines.map((tu) => (
                      <tr key={tu.id}>
                        <td>{tu.label}</td>
                        <td>{tu.type_name}</td>
                        <td>{fmt(tu.hub_height_m, 0)}</td>
                        <td>{tu.lat.toFixed(5)}</td>
                        <td>{tu.lon.toFixed(5)}</td>
                        <td className="small">
                          {tu.input_x != null ? `${fmt(tu.input_x, 1)} / ${fmt(tu.input_y, 1)} ${tu.input_crs}` : tu.input_crs}
                        </td>
                        <td>{fmt(tu.dem_elevation_m, 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {canEdit && <LayoutImport farm={f} crsList={crsList} onDone={refresh} />}
          </div>
        ))}
      </section>
    </div>
  );
}
