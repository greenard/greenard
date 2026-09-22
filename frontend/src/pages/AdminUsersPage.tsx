import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { get, patch, post } from "../api/client";
import type { User } from "../api/types";
import { errorText, formatDateTime } from "../utils/format";
import { usePrefs } from "../utils/prefs";

export default function AdminUsersPage() {
  const { t, i18n } = useTranslation();
  const { tz } = usePrefs();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => get<User[]>("/users") });
  const [form, setForm] = useState({ email: "", full_name: "", password: "", is_admin: false, locale: "fr" });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["users"] });
  const create = useMutation({
    mutationFn: () => post<User>("/users", form),
    onSuccess: () => {
      setForm({ email: "", full_name: "", password: "", is_admin: false, locale: "fr" });
      refresh();
    },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Partial<User> }) => patch<User>(`/users/${id}`, body),
    onSuccess: refresh,
  });

  return (
    <div className="page">
      <h1>{t("users.title")}</h1>
      <form
        className="card inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <input type="email" placeholder={t("common.email")} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
        <input placeholder={t("users.fullName")} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
        <input type="password" placeholder={t("users.password")} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
        <select value={form.locale} onChange={(e) => setForm({ ...form, locale: e.target.value })}>
          <option value="fr">FR</option>
          <option value="en">EN</option>
        </select>
        <label className="check">
          <input type="checkbox" checked={form.is_admin} onChange={(e) => setForm({ ...form, is_admin: e.target.checked })} />
          {t("users.admin")}
        </label>
        <button className="primary">{t("users.new")}</button>
      </form>
      {(create.isError || update.isError) && <div className="alert error">{errorText(t, create.error ?? update.error)}</div>}
      <table className="table">
        <thead>
          <tr>
            <th>{t("common.email")}</th>
            <th>{t("users.fullName")}</th>
            <th>{t("users.admin")}</th>
            <th>{t("users.active")}</th>
            <th>{t("users.lastLogin")}</th>
            <th>{t("common.actions")}</th>
          </tr>
        </thead>
        <tbody>
          {users.data?.map((u) => (
            <tr key={u.id} className={u.is_active ? "" : "muted"}>
              <td>{u.email}</td>
              <td>{u.full_name}</td>
              <td>{u.is_admin ? t("common.yes") : t("common.no")}</td>
              <td>{u.is_active ? t("common.yes") : t("common.no")}</td>
              <td>{formatDateTime(u.last_login_at, tz, i18n.language)}</td>
              <td>
                <button className="link" onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}>
                  {u.is_active ? t("users.deactivate") : t("users.activate")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
