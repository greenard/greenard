import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { del, get, put } from "../api/client";
import type { Member, Role } from "../api/types";
import { errorText } from "../utils/format";

export default function MembersPanel({ projectId, canManage }: { projectId: number; canManage: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const members = useQuery({ queryKey: ["members", projectId], queryFn: () => get<Member[]>(`/projects/${projectId}/members`) });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("engineer");
  const refresh = () => void qc.invalidateQueries({ queryKey: ["members", projectId] });
  const upsert = useMutation({ mutationFn: () => put(`/projects/${projectId}/members`, { email, role }), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (uid: number) => del(`/projects/${projectId}/members/${uid}`), onSuccess: refresh });

  return (
    <details className="card">
      <summary>
        {t("projects.members")} ({members.data?.length ?? 0})
      </summary>
      <ul className="members">
        {members.data?.map((m) => (
          <li key={m.user_id}>
            {m.email} — <span className="badge">{t(`projects.roles.${m.role}`)}</span>
            {canManage && (
              <button className="link" onClick={() => remove.mutate(m.user_id)}>
                {t("common.delete")}
              </button>
            )}
          </li>
        ))}
      </ul>
      {canManage && (
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            upsert.mutate();
          }}
        >
          <input type="email" placeholder={t("common.email")} value={email} onChange={(e) => setEmail(e.target.value)} required />
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {(["owner", "engineer", "viewer"] as const).map((r) => (
              <option key={r} value={r}>
                {t(`projects.roles.${r}`)}
              </option>
            ))}
          </select>
          <button>{t("projects.addMember")}</button>
        </form>
      )}
      {(upsert.isError || remove.isError) && <div className="alert error">{errorText(t, upsert.error ?? remove.error)}</div>}
    </details>
  );
}
