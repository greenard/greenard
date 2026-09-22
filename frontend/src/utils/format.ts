import type { TFunction } from "i18next";
import { ApiError } from "../api/client";

export type TimeDisplay = "UTC" | "Africa/Casablanca";

/** Horodatages stockés en UTC ; conversion à l'affichage uniquement (tzdata du navigateur). */
export function formatDateTime(iso: string | null | undefined, tz: TimeDisplay, lang: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const s = new Intl.DateTimeFormat(lang === "en" ? "en-GB" : "fr-FR", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
  return `${s} ${tz === "UTC" ? "UTC" : "(Maroc)"}`;
}

export function fmt(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function errorText(t: TFunction, err: unknown): string {
  if (err instanceof ApiError) {
    const key = `errors.${err.body.code}`;
    return t(key, { ...err.body.params, defaultValue: err.body.message || t("errors.unknown") });
  }
  return t("errors.unknown");
}

export function codeText(t: TFunction, code: string, params: Record<string, unknown> = {}, fallback = ""): string {
  return t(`errors.${code}`, { ...params, defaultValue: fallback || code });
}
