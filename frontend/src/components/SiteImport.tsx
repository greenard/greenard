import { useState } from "react";
import { useTranslation } from "react-i18next";
import { post } from "../api/client";
import type { ImportReport } from "../api/types";
import { codeText, errorText } from "../utils/format";

export default function SiteImport({ projectId, onDone }: { projectId: number; onDone: () => void }) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");

  const send = async (dry: boolean) => {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    setError("");
    setMsg("");
    try {
      const r = await post<ImportReport>(`/projects/${projectId}/sites/import?dry_run=${dry}`, fd);
      setReport(r);
      if (!dry && r.created.length) {
        setMsg(t("site.importOk", { count: r.created.length }));
        setReport(null);
        setFile(null);
        onDone();
      }
    } catch (e) {
      setError(errorText(t, e));
    }
  };

  return (
    <details className="import">
      <summary>{t("site.import")}</summary>
      <p className="muted small">{t("site.importHelp")}</p>
      <div className="row">
        <input
          type="file"
          accept=".csv,.txt,.kml"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setReport(null);
          }}
        />
        <button disabled={!file} onClick={() => void send(true)}>
          {t("site.importCheck")}
        </button>
        {report?.ok && report.sites.length > 0 && (
          <button className="primary" onClick={() => void send(false)}>
            {t("site.importConfirm", { count: report.sites.length })}
          </button>
        )}
      </div>
      {error && <div className="alert error">{error}</div>}
      {msg && <div className="alert ok">{msg}</div>}
      {report && report.errors.length > 0 && (
        <table className="table compact">
          <thead>
            <tr>
              <th>{t("site.row")}</th>
              <th>{t("site.field")}</th>
              <th>{t("site.message")}</th>
            </tr>
          </thead>
          <tbody>
            {report.errors.map((e, k) => (
              <tr key={k} className={e.severity === "error" ? "row-error" : "row-warning"}>
                <td>{e.row}</td>
                <td>{e.field}</td>
                <td>{codeText(t, e.code, e.params, e.message)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {report && report.sites.length > 0 && (
        <p className="muted small">{report.sites.map((s) => `${s.name} (${s.lat.toFixed(4)}, ${s.lon.toFixed(4)})`).join(" · ")}</p>
      )}
    </details>
  );
}
