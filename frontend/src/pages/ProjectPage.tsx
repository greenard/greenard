import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { del, get, patch, post } from "../api/client";
import type { CrsItem, GridPoint, ModelInfo, Project, Representations, Site } from "../api/types";
import CoordinateInput, { coordPayload, emptyCoord, type CoordValue } from "../components/CoordinateInput";
import GridPointsPanel, { useGridPoints } from "../components/GridPointsPanel";
import MembersPanel from "../components/MembersPanel";
import SiteImport from "../components/SiteImport";
import MapView from "../map/MapView";
import { errorText, fmt, formatDateTime } from "../utils/format";
import { usePrefs } from "../utils/prefs";

export default function ProjectPage() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const { t, i18n } = useTranslation();
  const { tz } = usePrefs();
  const qc = useQueryClient();

  const project = useQuery({ queryKey: ["project", pid], queryFn: () => get<Project>(`/projects/${pid}`) });
  const sites = useQuery({ queryKey: ["sites", pid], queryFn: () => get<Site[]>(`/projects/${pid}/sites`) });
  const models = useQuery({ queryKey: ["models"], queryFn: () => get<ModelInfo[]>("/models") });
  const crs = useQuery({ queryKey: ["crs"], queryFn: () => get<CrsItem[]>("/geo/crs") });

  const [activeId, setActiveId] = useState<number | null>(null);
  const [coord, setCoord] = useState<CoordValue>(emptyCoord);
  const [resolved, setResolved] = useState<Representations | null>(null);
  const [name, setName] = useState("");
  const [visible, setVisible] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (activeId === null && sites.data?.length) setActiveId(sites.data[0].id);
  }, [sites.data, activeId]);

  const gp = useGridPoints(activeId);
  const active = sites.data?.find((s) => s.id === activeId) ?? null;
  const canEdit = project.data?.role === "owner" || project.data?.role === "engineer";

  const createSite = useMutation({
    mutationFn: () => post<Site>(`/projects/${pid}/sites`, { name, ...coordPayload(coord) }),
    onSuccess: (s) => {
      setName("");
      setCoord({ ...emptyCoord, mode: coord.mode, utmZone: coord.utmZone, lambertCrs: coord.lambertCrs });
      setActiveId(s.id);
      void qc.invalidateQueries({ queryKey: ["sites", pid] });
    },
  });
  const deleteSite = useMutation({
    mutationFn: (id: number) => del(`/sites/${id}`),
    onSuccess: () => {
      setActiveId(null);
      void qc.invalidateQueries({ queryKey: ["sites", pid] });
    },
  });
  const toggle = useMutation({
    mutationFn: (p: GridPoint) => patch(`/sites/${activeId}/grid-points/${p.grid_point_id}`, { selected: !p.selected }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["gridpoints", activeId] }),
  });

  const onMapClick = (lat: number, lon: number) => {
    // saisie par clic : toujours en WGS84 ; la zone UTM est pré-remplie depuis la conversion
    setCoord({ ...coord, mode: "wgs84", lat: lat.toFixed(6), lon: lon.toFixed(6) });
  };
  useEffect(() => {
    if (resolved && coord.mode === "wgs84") setCoord((c) => ({ ...c, utmZone: resolved.utm.zone }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolved?.utm.zone]);

  if (project.isError) return <div className="page alert error">{errorText(t, project.error)}</div>;

  return (
    <div className="project-layout">
      <aside className="sidebar">
        <h2>{project.data?.name}</h2>
        <p className="muted">{project.data?.description}</p>
        <MembersPanel projectId={pid} canManage={project.data?.role === "owner"} />

        <section className="card">
          <h3>{t("site.sites")}</h3>
          {sites.data?.length === 0 && <p className="muted">{t("site.empty")}</p>}
          <ul className="site-list">
            {sites.data?.map((s) => (
              <li key={s.id} className={s.id === activeId ? "active" : ""} onClick={() => setActiveId(s.id)}>
                <b>{s.name}</b>
                <small>
                  {s.lat.toFixed(4)}, {s.lon.toFixed(4)} · {s.input_crs}
                  {s.dem_elevation_m !== null && ` · ${fmt(s.dem_elevation_m, 0)} m`}
                </small>
                {canEdit && (
                  <button
                    className="link small"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (confirm(t("common.confirmDelete"))) deleteSite.mutate(s.id);
                    }}
                  >
                    ✕
                  </button>
                )}
              </li>
            ))}
          </ul>
          {canEdit && <SiteImport projectId={pid} onDone={() => void qc.invalidateQueries({ queryKey: ["sites", pid] })} />}
        </section>

        {canEdit && (
          <section className="card">
            <h3>{t("site.new")}</h3>
            <p className="muted small">{t("site.clickMap")}</p>
            <CoordinateInput value={coord} onChange={setCoord} crsList={crs.data ?? []} onResolved={setResolved} />
            <form
              className="row"
              onSubmit={(e) => {
                e.preventDefault();
                createSite.mutate();
              }}
            >
              <input placeholder={t("site.name")} value={name} onChange={(e) => setName(e.target.value)} required />
              <button className="primary" disabled={!resolved}>
                {t("site.add")}
              </button>
            </form>
            {createSite.isError && <div className="alert error">{errorText(t, createSite.error)}</div>}
          </section>
        )}
      </aside>

      <div className="main-col">
        <MapView
          sites={sites.data ?? []}
          activeSiteId={activeId}
          points={gp.data?.points ?? []}
          models={models.data ?? []}
          visibleModels={visible}
          preview={resolved ? resolved.wgs84 : null}
          domains={(models.data ?? []).filter((m) => m.regional)}
          onMapClick={onMapClick}
          onSiteClick={setActiveId}
          onPointClick={(p) => canEdit && toggle.mutate(p)}
        />
        {active && models.data && (
          <div className="below-map">
            <div className="site-head">
              <h3>{active.name}</h3>
              <span className="muted">
                {active.lat.toFixed(6)}, {active.lon.toFixed(6)} · {t("site.demElevation")} : {fmt(active.dem_elevation_m, 0)} m ·{" "}
                {formatDateTime(active.created_at, tz, i18n.language)}
              </span>
            </div>
            <GridPointsPanel
              key={active.id}
              site={active}
              models={models.data}
              canEdit={canEdit}
              visible={visible}
              setVisible={setVisible}
              points={gp.data?.points ?? []}
            />
          </div>
        )}
      </div>
    </div>
  );
}
