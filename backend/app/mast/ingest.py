"""Mise en forme des séries du mât : UTC, convention d'horodatage, grille régulière, unités.

Convention interne : horodatage UTC de **fin** d'intervalle (période (t − Δ, t]), cohérente avec
l'agrégation des prévisions (`app.nwp.timeseries`). Aucune donnée n'est comblée : les
enregistrements absents sont insérés vides (drapeau `missing` au contrôle qualité).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.core.errors import AppError
from app.mast.formats import KINDS, STATS, RawTable

_BOOM_LABELS = {0: "n", 45: "ne", 90: "e", 135: "se", 180: "s", 225: "sw", 270: "w", 315: "nw"}
_OFFSET = re.compile(r"^(?:UTC)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.I)

WS_UNITS = {"m/s": 1.0, "km/h": 1 / 3.6, "kt": 0.514444, "knots": 0.514444, "mph": 0.44704}
P_UNITS = {"hpa": 1.0, "mbar": 1.0, "kpa": 10.0, "pa": 0.01, "mmhg": 1.33322}


@dataclass
class SensorDef:
    code: str
    kind: str
    height_m: float
    boom_dir_deg: float | None
    stats: dict[str, str] = field(default_factory=dict)  # stat → colonne d'origine
    unit: str = ""


@dataclass
class Ingested:
    frame: pd.DataFrame  # index : fin d'intervalle UTC ; colonnes « code__stat »
    sensors: list[SensorDef]
    interval_min: int
    report: dict


def sensor_code(kind: str, height: float, boom: float | None, duplicate: bool) -> str:
    base = f"{kind}_{height:g}".replace(".", "p")
    if duplicate and boom is not None:
        label = _BOOM_LABELS.get(int(round(boom / 45.0) * 45) % 360, f"{int(boom)}")
        return f"{base}_{label}"
    return base


def build_sensors(mapping: list[dict]) -> list[SensorDef]:
    """Regroupe les colonnes (moyenne, écart-type, max, min) par capteur physique."""
    errors = []
    groups: dict[tuple, dict] = {}
    for m in mapping:
        if not m.get("kind"):
            continue  # colonne ignorée
        if m["kind"] not in KINDS or m.get("stat", "mean") not in STATS:
            errors.append(m.get("column"))
            continue
        if m.get("height_m") is None:
            raise AppError("MAST_MAPPING_HEIGHT_MISSING", "Sensor height missing", column=m.get("column"))
        key = (m["kind"], float(m["height_m"]), m.get("boom_dir_deg"))
        g = groups.setdefault(key, {"stats": {}, "unit": m.get("unit", "")})
        stat = m.get("stat", "mean")
        if stat in g["stats"]:
            raise AppError(
                "MAST_MAPPING_DUPLICATE",
                "Two columns mapped to the same sensor statistic",
                column=m.get("column"),
                other=g["stats"][stat],
            )
        g["stats"][stat] = m["column"]
        g["unit"] = g["unit"] or m.get("unit", "")
    if errors:
        raise AppError("MAST_MAPPING_INVALID", "Invalid sensor kind or statistic", columns=errors)
    per_kh: dict[tuple, int] = {}
    for kind, h, _ in groups:
        per_kh[(kind, h)] = per_kh.get((kind, h), 0) + 1
    sensors = []
    for (kind, h, boom), g in groups.items():
        if "mean" not in g["stats"]:
            raise AppError("MAST_MAPPING_NO_MEAN", "Each sensor needs a mean column", sensor=f"{kind} {h} m")
        sensors.append(
            SensorDef(sensor_code(kind, h, boom, per_kh[(kind, h)] > 1), kind, h, boom, g["stats"], g["unit"])
        )
    if len({s.code for s in sensors}) != len(sensors):
        raise AppError("MAST_MAPPING_AMBIGUOUS", "Redundant sensors at the same height need a boom orientation")
    return sorted(sensors, key=lambda s: (s.kind, s.height_m, s.code))


def _to_utc(times: pd.Series, tz: str) -> tuple[pd.Series, int]:
    tz = (tz or "UTC").strip()
    if tz.upper() in ("UTC", "GMT", "Z", "+00:00"):
        return times, 0
    m = _OFFSET.match(tz)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        delta = pd.Timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return times - sign * delta, 0
    try:
        loc = times.dt.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    except Exception as exc:  # zoneinfo inconnue
        raise AppError("MAST_TIMEZONE_INVALID", "Unknown time zone", value=tz) from exc
    bad = int(loc.isna().sum() - times.isna().sum())
    return loc.dt.tz_convert("UTC").dt.tz_localize(None), bad


def _convert(values: pd.Series, kind: str, unit: str) -> pd.Series:
    u = (unit or "").strip().lower().replace("°", "").replace(" ", "")
    if kind == "ws" and u in WS_UNITS:
        return values * WS_UNITS[u]
    if kind == "temp":
        if u in ("k", "kelvin"):
            return values - 273.15
        if u in ("f", "degf"):
            return (values - 32.0) * 5.0 / 9.0
    if kind == "pressure" and u in P_UNITS:
        return values * P_UNITS[u]
    return values


def ingest(raw: RawTable, mapping: list[dict], time_ref: dict) -> Ingested:
    sensors = build_sensors(mapping)
    if not sensors:
        raise AppError("MAST_MAPPING_EMPTY", "No column mapped to a sensor")
    df = raw.frame
    fmt = time_ref.get("format") or None
    try:
        times = pd.to_datetime(
            df[raw.time_column].astype(str).str.strip(),
            format=fmt or "mixed",
            dayfirst=bool(time_ref.get("dayfirst", False)),
            errors="coerce",
        )
    except ValueError as exc:
        raise AppError("MAST_TIME_PARSE", "Cannot parse timestamps", detail=str(exc)) from exc
    unparsed = int(times.isna().sum())
    times, ambiguous = _to_utc(times, time_ref.get("timezone", "UTC"))
    valid = times.notna()
    if valid.sum() < 2:
        raise AppError("MAST_TIME_PARSE", "Too few valid timestamps")
    t = times[valid]
    diffs = t.sort_values().diff().dropna()
    step = diffs[diffs > pd.Timedelta(0)].median()
    interval = int(round(step.total_seconds() / 60))
    if interval < 1 or interval > 60:
        raise AppError("MAST_INTERVAL_INVALID", "Unexpected record interval", minutes=interval)
    convention = time_ref.get("convention") or raw.default_convention
    if convention not in ("start", "end"):
        raise AppError("MAST_CONVENTION_INVALID", "Convention must be start or end")
    if convention == "start":
        t = t + pd.Timedelta(minutes=interval)
    data = {}
    bad_values = 0
    for s in sensors:
        for stat, col in s.stats.items():
            if col not in df.columns:
                raise AppError("MAST_MAPPING_COLUMN_UNKNOWN", "Unknown column", column=col)
            v = pd.to_numeric(df.loc[valid, col].astype(str).str.replace(",", ".").str.strip(), errors="coerce")
            v = v.replace([-9999, -999, 9999, -99.99], np.nan)  # sentinelles courantes des enregistreurs
            bad_values += int(v.isna().sum())
            data[f"{s.code}__{stat}"] = _convert(v, s.kind, s.unit).to_numpy(dtype="float32")
    frame = pd.DataFrame(data, index=pd.DatetimeIndex(t.to_numpy(), name="time_utc_end"))
    misaligned = int((frame.index.floor(f"{interval}min") != frame.index).sum())
    frame.index = frame.index.round(f"{interval}min")
    dup = int(frame.index.duplicated().sum())
    frame = frame[~frame.index.duplicated(keep="first")].sort_index()
    full = pd.date_range(frame.index[0], frame.index[-1], freq=f"{interval}min", name="time_utc_end")
    inserted = int(len(full) - len(frame))
    frame = frame.reindex(full)
    report = {
        "format": raw.format,
        "records_in_file": int(len(df)),
        "unparsed_timestamps": unparsed,
        "ambiguous_or_nonexistent_local_times": ambiguous,
        "misaligned_timestamps_rounded": misaligned,
        "duplicates_removed": dup,
        "missing_records_inserted": inserted,
        "non_numeric_or_sentinel_values": bad_values,
        "interval_min": interval,
        "convention_in_file": convention,
        "stored_convention": "end of interval, UTC",
        "timezone_in_file": time_ref.get("timezone", "UTC"),
        "start_utc": str(full[0]),
        "end_utc": str(full[-1]),
    }
    return Ingested(frame, sensors, interval, report)
