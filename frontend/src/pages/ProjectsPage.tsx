import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { get, post } from "../api/client";
import type { Project } from "../api/types";
import { errorText, formatDateTime } from "../utils/format";
import { usePrefs } from "../utils/prefs";

export default function ProjectsPage() {
  const { t, i18n } = useTranslation();
  const { tz } = usePrefs();
  const qc = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: () => get<Project[]>("/projects") });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useMutation({
    mutationFn: () => post<Project>("/projects", { name, description }),
    onSuccess: () => {
      setName("");
      setDescription("");
      void qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  return (
    <div className="page">
      <h1>{t("projects.title")}</h1>
      <form
        className="card inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <input placeholder={t("common.name")} value={name} onChange={(e) => setName(e.target.value)} required />
        <input placeholder={t("projects.description")} value={description} onChange={(e) => setDescription(e.target.value)} />
        <button className="primary">{t("projects.new")}</button>
        {create.isError && <span className="alert error">{errorText(t, create.error)}</span>}
      </form>
      {projects.data?.length === 0 && <p className="muted">{t("projects.empty")}</p>}
      <div className="grid-cards">
        {projects.data?.map((p) => (
          <Link key={p.id} to={`/projects/${p.id}`} className="card project-card">
            <h3>{p.name}</h3>
            <p className="muted">{p.description}</p>
            <div className="row-between">
              <span className="badge">{t(`projects.roles.${p.role}`)}</span>
              <span>{t("projects.sites", { count: p.site_count })}</span>
            </div>
            <small className="muted">{formatDateTime(p.created_at, tz, i18n.language)}</small>
          </Link>
        ))}
      </div>
    </div>
  );
}
