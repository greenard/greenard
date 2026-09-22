import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { patch } from "../api/client";
import type { ModelInfo, Project } from "../api/types";
import { errorText } from "../utils/format";

export default function ArchivePanel({ project, models }: { project: Project; models: ModelInfo[] }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [enabled, setEnabled] = useState(project.archive_enabled);
  const [chosen, setChosen] = useState<string[]>(project.archive_models);
  const [lead, setLead] = useState(project.archive_max_lead_h);
  useEffect(() => {
    setEnabled(project.archive_enabled);
    setChosen(project.archive_models);
    setLead(project.archive_max_lead_h);
  }, [project]);
  const save = useMutation({
    mutationFn: () => patch(`/projects/${project.id}`, { archive_enabled: enabled, archive_models: chosen, archive_max_lead_h: lead }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["project", project.id] }),
  });
  return (
    <details className="card">
      <summary>
        {t("arch.title")} {project.archive_enabled ? <span className="badge">{t("arch.on")}</span> : <span className="muted small">{t("arch.off")}</span>}
      </summary>
      <p className="muted small">{t("arch.help")}</p>
      <label className="check">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> {t("arch.enable")}
      </label>
      <div className="chips">
        {models.map((m) => (
          <label key={m.code} className="chip">
            <input
              type="checkbox"
              checked={chosen.includes(m.code)}
              onChange={(e) => setChosen(e.target.checked ? [...chosen, m.code] : chosen.filter((c) => c !== m.code))}
            />
            {m.name}
          </label>
        ))}
      </div>
      <label>
        {t("arch.lead")}
        <input type="number" min={6} max={384} value={lead} onChange={(e) => setLead(Number(e.target.value))} />
      </label>
      <button onClick={() => save.mutate()}>{t("common.save")}</button>
      {save.isError && <div className="alert error">{errorText(t, save.error)}</div>}
      {save.isSuccess && <span className="muted small"> ✓</span>}
    </details>
  );
}
