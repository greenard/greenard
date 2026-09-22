import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { get, streamTask } from "../api/client";
import type { ForecastJob, GridPoint, ModelInfo, Task } from "../api/types";
import { formatDateTime } from "../utils/format";
import { usePrefs } from "../utils/prefs";
import ForecastRequest from "./ForecastRequest";
import ForecastResults from "./ForecastResults";

interface Props {
  siteId: number;
  models: ModelInfo[];
  points: GridPoint[];
  canEdit: boolean;
}

export default function ForecastPanel({ siteId, models, points, canEdit }: Props) {
  const { t, i18n } = useTranslation();
  const { tz } = usePrefs();
  const qc = useQueryClient();
  const jobs = useQuery({
    queryKey: ["forecasts", siteId],
    queryFn: () => get<ForecastJob[]>(`/sites/${siteId}/forecasts`),
  });
  const [selected, setSelected] = useState<number | null>(null);
  const [running, setRunning] = useState<{ job: ForecastJob; task?: Task } | null>(null);

  useEffect(() => {
    if (selected === null && jobs.data?.length) setSelected(jobs.data[0].id);
  }, [jobs.data, selected]);

  // progression en direct (SSE)
  useEffect(() => {
    if (!running?.job.task_id) return;
    const ctrl = new AbortController();
    streamTask<Task>(running.job.task_id, (tk) => setRunning((r) => (r ? { ...r, task: tk } : r)), ctrl.signal)
      .catch(() => undefined)
      .finally(() => {
        void qc.invalidateQueries({ queryKey: ["forecasts", siteId] });
        setSelected(running.job.id);
      });
    return () => ctrl.abort();
  }, [running?.job.id, running?.job.task_id, qc, siteId]);

  const job = jobs.data?.find((j) => j.id === selected) ?? null;
  const done = running?.task && ["success", "failure"].includes(running.task.status);

  return (
    <div className="fc-panel">
      <section className="card">
        <h3>{t("fc.requestTitle")}</h3>
        <ForecastRequest siteId={siteId} models={models} points={points} canEdit={canEdit} onStarted={(j) => {
            setRunning({ job: j });
            setSelected(j.id);
            void qc.invalidateQueries({ queryKey: ["forecasts", siteId] });
          }} />
        {running && !done && (
          <div className="progress-box">
            <progress max={1} value={running.task?.progress ?? 0} />
            <small className="muted">{running.task?.message ?? t("fc.queued")}</small>
          </div>
        )}
      </section>
      <section className="card">
        <h3>{t("fc.jobs")}</h3>
        {jobs.data?.length === 0 && <p className="muted">{t("fc.noJobs")}</p>}
        <div className="job-list">
          {jobs.data?.map((j) => (
            <button key={j.id} className={j.id === selected ? "job active" : "job"} onClick={() => setSelected(j.id)}>
              <span className={`status ${j.status}`}>{t(`fc.status.${j.status}`)}</span>
              <span>
                #{j.id} · {j.params.models.join(", ")} · {j.params.max_lead_h} h{j.kind === "archive" && ` · ${t("fc.archiveJob")}`}
              </span>
              <small className="muted">{formatDateTime(j.created_at, tz, i18n.language)}</small>
            </button>
          ))}
        </div>
      </section>
      {job && (
        <section className="card">
          <h3>{t("fc.results", { id: job.id })}</h3>
          <ForecastResults key={job.id} job={job} models={models} />
        </section>
      )}
    </div>
  );
}
