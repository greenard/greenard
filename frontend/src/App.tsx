import { useTranslation } from "react-i18next";
import { Link, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import { setLanguage } from "./i18n";
import AdminUsersPage from "./pages/AdminUsersPage";
import LoginPage from "./pages/LoginPage";
import ProjectPage from "./pages/ProjectPage";
import ProjectsPage from "./pages/ProjectsPage";
import { usePrefs } from "./utils/prefs";

function Header() {
  const { t, i18n } = useTranslation();
  const { user, logout } = useAuth();
  const { tz, setTz } = usePrefs();
  return (
    <header className="topbar">
      <Link to="/" className="brand">
        <span className="brand-mark">◭</span> {t("app.title")}
        <small>{t("app.subtitle")}</small>
      </Link>
      <nav>
        <Link to="/">{t("header.projects")}</Link>
        {user?.is_admin && <Link to="/admin/users">{t("header.users")}</Link>}
      </nav>
      <div className="topbar-right">
        <label title={t("header.timeDisplay")}>
          🕒
          <select value={tz} onChange={(e) => setTz(e.target.value as typeof tz)}>
            <option value="UTC">{t("header.utc")}</option>
            <option value="Africa/Casablanca">{t("header.local")}</option>
          </select>
        </label>
        <select
          aria-label={t("header.language")}
          value={i18n.language}
          onChange={(e) => setLanguage(e.target.value as "fr" | "en")}
        >
          <option value="fr">FR</option>
          <option value="en">EN</option>
        </select>
        {user && (
          <>
            <span className="muted">{user.email}</span>
            <button className="link" onClick={() => void logout()}>
              {t("header.logout")}
            </button>
          </>
        )}
      </div>
    </header>
  );
}

export default function App() {
  const { user, ready } = useAuth();
  const { t } = useTranslation();
  if (!ready) return <div className="center">{t("common.loading")}</div>;
  if (!user) return <LoginPage />;
  return (
    <div className="app">
      <Header />
      <main>
        <Routes>
          <Route path="/" element={<ProjectsPage />} />
          <Route path="/projects/:projectId" element={<ProjectPage />} />
          <Route path="/admin/users" element={user.is_admin ? <AdminUsersPage /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
