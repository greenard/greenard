import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { get, patch } from "../api/client";
import type { DataSources } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { errorText } from "../utils/format";

export default function DataSourcesPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["data-sources"], queryFn: () => get<DataSources>("/data-sources") });
  const toggle = useMutation({
    mutationFn: ({ code, enabled }: { code: string; enabled: boolean }) => patch(`/data-sources/${code}`, { enabled }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["data-sources"] }),
  });
  const d = q.data;
  return (
    <div className="page">
      <h1>{t("ds.title")}</h1>
      {d && (
        <p className="muted">
          {t("ds.deployment", { usage: t(`ds.usage.${d.deployment_usage}`) })} · Open-Meteo : {t(`ds.om.${d.open_meteo_mode}`)} ·{" "}
          {t("ds.limit", { mb: d.max_download_mb })}
        </p>
      )}
      {toggle.isError && <div className="alert error">{errorText(t, toggle.error)}</div>}
      <table className="table">
        <thead>
          <tr>
            <th>{t("ds.source")}</th>
            <th>{t("fc.licence")}</th>
            <th>{t("ds.cost")}</th>
            <th>{t("ds.state")}</th>
            <th>{t("ds.notes")}</th>
          </tr>
        </thead>
        <tbody>
          {d?.sources.map((s) => (
            <tr key={s.code} className={s.enabled && !s.blocked_by_deployment ? "" : "muted"}>
              <td>
                <b>{s.name}</b>
                <br />
                <small>
                  <a href={s.terms_url} target="_blank" rel="noreferrer">
                    {t("ds.terms")}
                  </a>
                </small>
              </td>
              <td>{t(`fc.lic.${s.usage_licence}`)}</td>
              <td>{s.cost === "paid" ? <span className="badge paid">{t("fc.cost.paid")}</span> : t("fc.cost.free")}</td>
              <td>
                {!s.implemented ? (
                  t("ds.notImplemented")
                ) : s.blocked_by_deployment ? (
                  <span className="alert-cell">{t("ds.blocked")}</span>
                ) : user?.is_admin ? (
                  <label className="check">
                    <input type="checkbox" checked={s.enabled} onChange={(e) => toggle.mutate({ code: s.code, enabled: e.target.checked })} />
                    {s.enabled ? t("ds.enabled") : t("ds.disabled")}
                  </label>
                ) : s.enabled ? (
                  t("ds.enabled")
                ) : (
                  t("ds.disabled")
                )}
              </td>
              <td className="small">{s.notes}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">{t("ds.help")}</p>
    </div>
  );
}
