import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { post } from "../api/client";
import type { CrsItem, Representations } from "../api/types";
import { errorText, fmt } from "../utils/format";

export type Mode = "wgs84" | "utm" | "lambert";

export interface CoordValue {
  mode: Mode;
  lat: string;
  lon: string;
  x: string;
  y: string;
  utmZone: number;
  utmHemi: "N" | "S";
  lambertCrs: string;
}

export const emptyCoord: CoordValue = {
  mode: "wgs84",
  lat: "",
  lon: "",
  x: "",
  y: "",
  utmZone: 29,
  utmHemi: "N",
  lambertCrs: "EPSG:26191",
};

/** Corps de requête /geo/convert et /sites correspondant à la saisie. */
export function coordPayload(v: CoordValue): Record<string, unknown> | null {
  if (v.mode === "wgs84") return v.lat.trim() && v.lon.trim() ? { lat: v.lat, lon: v.lon, crs: "EPSG:4326" } : null;
  const x = Number(v.x.replace(",", "."));
  const y = Number(v.y.replace(",", "."));
  if (!v.x.trim() || !v.y.trim() || Number.isNaN(x) || Number.isNaN(y)) return null;
  const crs = v.mode === "utm" ? `UTM${v.utmZone}${v.utmHemi}` : v.lambertCrs;
  return { x, y, crs };
}

interface Props {
  value: CoordValue;
  onChange: (v: CoordValue) => void;
  crsList: CrsItem[];
  onResolved: (r: Representations | null) => void;
}

export default function CoordinateInput({ value, onChange, crsList, onResolved }: Props) {
  const { t } = useTranslation();
  const [rep, setRep] = useState<Representations | null>(null);
  const [error, setError] = useState("");
  const payload = useMemo(() => coordPayload(value), [value]);
  const payloadKey = JSON.stringify(payload);

  // conversion en direct (debounce)
  useEffect(() => {
    if (!payload) {
      setRep(null);
      setError("");
      onResolved(null);
      return;
    }
    const h = setTimeout(() => {
      post<Representations>("/geo/convert", payload)
        .then((r) => {
          setRep(r);
          setError("");
          onResolved(r);
        })
        .catch((e) => {
          setRep(null);
          setError(errorText(t, e));
          onResolved(null);
        });
    }, 300);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payloadKey, t]);

  const set = (patch: Partial<CoordValue>) => onChange({ ...value, ...patch });
  const lamberts = crsList.filter((c) => c.kind === "lambert_merchich");
  const warnings = [...(rep?.input.warnings ?? []), ...(rep?.utm.warnings ?? [])];

  return (
    <div className="coord">
      <div className="tabs" role="tablist" aria-label={t("coord.mode")}>
        {(["wgs84", "utm", "lambert"] as const).map((m) => (
          <button key={m} type="button" className={value.mode === m ? "tab active" : "tab"} onClick={() => set({ mode: m })}>
            {t(`coord.${m}`)}
          </button>
        ))}
      </div>
      {value.mode === "wgs84" && (
        <div className="row">
          <label>
            {t("coord.lat")}
            <input value={value.lat} placeholder="31.5125 / 31°30'45&quot;N" onChange={(e) => set({ lat: e.target.value })} />
          </label>
          <label>
            {t("coord.lon")}
            <input value={value.lon} placeholder="-9.77 / 9°46'12&quot;W" onChange={(e) => set({ lon: e.target.value })} />
          </label>
        </div>
      )}
      {value.mode === "utm" && (
        <div className="row">
          <label className="narrow">
            {t("coord.zone")}
            <input type="number" min={1} max={60} value={value.utmZone} onChange={(e) => set({ utmZone: Number(e.target.value) })} />
          </label>
          <label className="narrow">
            {t("coord.hemisphere")}
            <select value={value.utmHemi} onChange={(e) => set({ utmHemi: e.target.value as "N" | "S" })}>
              <option value="N">N</option>
              <option value="S">S</option>
            </select>
          </label>
          <label>
            {t("coord.x")}
            <input value={value.x} onChange={(e) => set({ x: e.target.value })} />
          </label>
          <label>
            {t("coord.y")}
            <input value={value.y} onChange={(e) => set({ y: e.target.value })} />
          </label>
        </div>
      )}
      {value.mode === "lambert" && (
        <div className="row">
          <label>
            {t("coord.lambertZone")}
            <select value={value.lambertCrs} onChange={(e) => set({ lambertCrs: e.target.value })}>
              {lamberts.map((c) => (
                <option key={c.crs} value={c.crs}>
                  {c.name} ({c.crs}) {c.deprecated ? t("coord.deprecated") : ""}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("coord.x")}
            <input value={value.x} onChange={(e) => set({ x: e.target.value })} />
          </label>
          <label>
            {t("coord.y")}
            <input value={value.y} onChange={(e) => set({ y: e.target.value })} />
          </label>
        </div>
      )}
      {error && <div className="alert error">{error}</div>}
      {warnings.map((w) => (
        <div key={w} className="alert warning">
          {t(`errors.${w}`)}
        </div>
      ))}
      {rep && <Conversions rep={rep} />}
    </div>
  );
}

export function Conversions({ rep }: { rep: Representations }) {
  const { t } = useTranslation();
  return (
    <table className="table compact conversions">
      <caption>{t("coord.conversions")}</caption>
      <tbody>
        <tr>
          <th>WGS84 — {t("coord.decimal")}</th>
          <td>
            {rep.wgs84.lat.toFixed(6)}, {rep.wgs84.lon.toFixed(6)}
          </td>
        </tr>
        <tr>
          <th>WGS84 — {t("coord.dms")}</th>
          <td>
            {rep.dms.lat} {rep.dms.lon}
          </td>
        </tr>
        <tr>
          <th>
            UTM {rep.utm.zone}
            {rep.utm.hemisphere} <small className="muted">({t("coord.autoZone")})</small>
          </th>
          <td>
            X {fmt(rep.utm.x, 1)} · Y {fmt(rep.utm.y, 1)} <small className="muted">{rep.utm.crs}</small>
          </td>
        </tr>
        {rep.lambert.length === 0 && (
          <tr>
            <th>{t("coord.lambert")}</th>
            <td className="muted">{t("coord.noLambert")}</td>
          </tr>
        )}
        {rep.lambert.map((l) => (
          <tr key={l.crs}>
            <th>{l.name}</th>
            <td>
              X {fmt(l.x, 1)} · Y {fmt(l.y, 1)} <small className="muted">{l.crs}</small>
              <div className={l.warnings.length ? "alert warning small" : "muted small"}>
                {l.warnings.length
                  ? t("errors.MERCHICH_BALLPARK")
                  : `${t("coord.transformation")} : ${
                      l.accuracy_m != null ? t("coord.accuracy", { value: l.accuracy_m }) : t("coord.unknownAccuracy")
                    }`}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
