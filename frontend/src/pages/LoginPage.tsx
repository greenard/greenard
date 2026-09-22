import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth/AuthContext";
import { setLanguage } from "../i18n";
import { errorText } from "../utils/format";

export default function LoginPage() {
  const { t, i18n } = useTranslation();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
    } catch (err) {
      setError(errorText(t, err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <form className="card login-card" onSubmit={submit}>
        <h1>
          <span className="brand-mark">◭</span> {t("app.title")}
        </h1>
        <p className="muted">{t("app.subtitle")}</p>
        <h2>{t("login.title")}</h2>
        <label>
          {t("login.email")}
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          {t("login.password")}
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error && <div className="alert error">{error}</div>}
        <button className="primary" disabled={busy}>
          {t("login.submit")}
        </button>
        <div className="lang-switch">
          {(["fr", "en"] as const).map((l) => (
            <button type="button" key={l} className={i18n.language === l ? "link active" : "link"} onClick={() => setLanguage(l)}>
              {l.toUpperCase()}
            </button>
          ))}
        </div>
      </form>
    </div>
  );
}
