"""Contrôle qualité des séries du mât : drapeaux par valeur, jamais de suppression.

Drapeaux (masque de bits, colonne `<capteur>__flag`) :

| bit | code | règle |
|---|---|---|
| 1 | missing | enregistrement absent ou valeur non numérique |
| 2 | range | hors plage physique (vent 0–50 m/s, écart-type 0–10, T −30…+55 °C, HR 0–100 %, P 750–1100 hPa) |
| 4 | stuck | valeur figée sur 1 h (6 enregistrements) : anémomètre > 0,3 m/s, girouette avec vent > 2 m/s |
| 8 | icing | T < +2 °C, HR > 85 % (si mesurée) et anémomètre/girouette figés ou écart-type nul |
| 16 | tower_shadow | vent venant de l'arrière du bras (± 30° autour de l'orientation + 180°) |
| 32 | spike | écart à la médiane glissante (7) > max(5 × MAD, 3 m/s) — vent ; > 5 °C — température |
| 64 | shear | rapport de vitesses entre hauteurs hors [0,75 ; 1,8] (vent bas > 3 m/s) |

Série composite par hauteur (`ws@100`, `wd@97`…) : moyenne des anémomètres redondants valides
(hors ombrage), sinon le seul valide. Les valeurs drapeautées sont exclues des analyses.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.mast.ingest import SensorDef

MISSING, RANGE, STUCK, ICING, SHADOW, SPIKE, SHEAR = 1, 2, 4, 8, 16, 32, 64
FLAG_NAMES = {
    MISSING: "missing",
    RANGE: "range",
    STUCK: "stuck",
    ICING: "icing",
    SHADOW: "tower_shadow",
    SPIKE: "spike",
    SHEAR: "shear",
}
RANGES = {"ws": (0.0, 50.0), "wd": (0.0, 360.0), "temp": (-30.0, 55.0), "rh": (0.0, 100.0), "pressure": (750.0, 1100.0)}
SHADOW_HALF_WIDTH = 30.0


def _stuck(x: pd.Series, n: int = 6) -> pd.Series:
    r = x.rolling(n, min_periods=n)
    flat = (r.max() - r.min()) == 0
    # une fenêtre figée drapeaute tous ses enregistrements
    return flat[::-1].rolling(n, min_periods=1).max()[::-1].fillna(0).astype(bool)


def _nearest(sensors: list[SensorDef], kind: str, height: float) -> SensorDef | None:
    cands = [s for s in sensors if s.kind == kind]
    return min(cands, key=lambda s: abs(s.height_m - height)) if cands else None


def angular_diff(a, b):
    return np.abs((np.asarray(a) - np.asarray(b) + 180.0) % 360.0 - 180.0)


def run_qc(frame: pd.DataFrame, sensors: list[SensorDef]) -> pd.DataFrame:
    df = frame.copy()
    flags: dict[str, np.ndarray] = {}
    for s in sensors:
        x = df[f"{s.code}__mean"]
        f = np.zeros(len(df), dtype=np.uint16)
        f |= np.where(x.isna(), MISSING, 0).astype(np.uint16)
        lo, hi = RANGES[s.kind]
        f |= np.where((x < lo) | (x > hi), RANGE, 0).astype(np.uint16)
        if s.kind == "ws" and f"{s.code}__sd" in df:
            sd = df[f"{s.code}__sd"]
            f |= np.where((sd < 0) | (sd > 10), RANGE, 0).astype(np.uint16)
        if s.kind in ("ws", "temp"):
            med = x.rolling(7, center=True, min_periods=4).median()
            if s.kind == "ws":
                mad = (x - med).abs().rolling(7, center=True, min_periods=4).median()
                thr = np.maximum(5 * mad, 3.0)
            else:
                thr = 5.0
            f |= np.where((x - med).abs() > thr, SPIKE, 0).astype(np.uint16)
        flags[s.code] = f

    # figé
    for s in sensors:
        x = df[f"{s.code}__mean"]
        stuck = _stuck(x)
        if s.kind == "ws":
            stuck &= x > 0.3
        elif s.kind == "wd":
            ref = _nearest(sensors, "ws", s.height_m)
            if ref is not None:
                stuck &= df[f"{ref.code}__mean"] > 2.0
        else:
            stuck[:] = False  # T, HR, P peuvent légitimement rester stables
        flags[s.code] |= np.where(stuck, STUCK, 0).astype(np.uint16)

    # givrage
    temp = _nearest(sensors, "temp", 1e9)
    rh = _nearest(sensors, "rh", 1e9)
    if temp is not None:
        cold = df[f"{temp.code}__mean"] < 2.0
        if rh is not None:
            cold &= df[f"{rh.code}__mean"] > 85.0
        for s in sensors:
            if s.kind not in ("ws", "wd"):
                continue
            frozen = (flags[s.code] & STUCK) > 0
            if s.kind == "ws" and f"{s.code}__sd" in df:
                frozen |= (df[f"{s.code}__sd"] == 0) & (df[f"{s.code}__mean"] > 0)
            flags[s.code] |= np.where(cold & frozen, ICING, 0).astype(np.uint16)

    # ombrage du mât
    for s in sensors:
        if s.kind != "ws" or s.boom_dir_deg is None:
            continue
        vane = _nearest(sensors, "wd", s.height_m)
        if vane is None:
            continue
        wd = df[f"{vane.code}__mean"]
        behind = angular_diff(wd, (s.boom_dir_deg + 180.0) % 360.0) <= SHADOW_HALF_WIDTH
        flags[s.code] |= np.where(behind, SHADOW, 0).astype(np.uint16)

    # cohérence du cisaillement entre hauteurs voisines (capteurs non drapeautés)
    ws_sensors = sorted([s for s in sensors if s.kind == "ws"], key=lambda s: s.height_m)
    for lo_s, hi_s in zip(ws_sensors, ws_sensors[1:], strict=False):
        if hi_s.height_m <= lo_s.height_m:
            continue
        lo_v, hi_v = df[f"{lo_s.code}__mean"], df[f"{hi_s.code}__mean"]
        ok = (flags[lo_s.code] == 0) & (flags[hi_s.code] == 0) & (lo_v > 3.0)
        ratio = hi_v / lo_v
        bad = ok & ((ratio < 0.75) | (ratio > 1.8))
        flags[lo_s.code] |= np.where(bad, SHEAR, 0).astype(np.uint16)
        flags[hi_s.code] |= np.where(bad, SHEAR, 0).astype(np.uint16)

    for code, f in flags.items():
        df[f"{code}__flag"] = f
    return add_composites(df, sensors)


def add_composites(df: pd.DataFrame, sensors: list[SensorDef]) -> pd.DataFrame:
    """Une série valide par grandeur et hauteur (redondance : anémomètre non ombragé)."""
    by_kh: dict[tuple, list[SensorDef]] = {}
    for s in sensors:
        by_kh.setdefault((s.kind, s.height_m), []).append(s)
    for (kind, h), group in by_kh.items():
        vals = []
        for s in group:
            v = df[f"{s.code}__mean"].where(df[f"{s.code}__flag"] == 0)
            vals.append(v)
        if kind == "wd":
            rad = np.radians(pd.concat(vals, axis=1))
            comp = (
                np.degrees(np.arctan2(np.sin(rad).mean(axis=1, skipna=True), np.cos(rad).mean(axis=1, skipna=True)))
                % 360
            )
            comp[pd.concat(vals, axis=1).isna().all(axis=1)] = np.nan
        else:
            comp = pd.concat(vals, axis=1).mean(axis=1, skipna=True)
        df[f"{kind}@{h:g}"] = comp.astype("float32")
        if kind == "ws":
            sds = [df[f"{s.code}__sd"].where(df[f"{s.code}__flag"] == 0) for s in group if f"{s.code}__sd" in df]
            if sds:
                df[f"sd@{h:g}"] = pd.concat(sds, axis=1).mean(axis=1, skipna=True).astype("float32")
    return df


def summary(df: pd.DataFrame, sensors: list[SensorDef]) -> dict:
    """Taux de disponibilité (global et mensuel) et comptes de drapeaux par capteur."""
    months = df.index.to_period("M")
    out = {"sensors": [], "months": sorted({str(m) for m in months})}
    for s in sensors:
        f = df[f"{s.code}__flag"].to_numpy()
        counts = {name: int(((f & bit) > 0).sum()) for bit, name in FLAG_NAMES.items()}
        valid = f == 0
        monthly = pd.Series(valid, index=months).groupby(level=0).mean() * 100
        events = np.flatnonzero(f & np.uint16(0xFFFF ^ MISSING))
        out["sensors"].append(
            {
                "code": s.code,
                "kind": s.kind,
                "height_m": s.height_m,
                "boom_dir_deg": s.boom_dir_deg,
                "valid_pct": round(float(valid.mean() * 100), 2),
                "flags": counts,
                "monthly_valid_pct": {str(k): round(float(v), 1) for k, v in monthly.items()},
                # premier enregistrement signalé (hors manquant) : point d'entrée du zoom dans l'interface
                "first_event_utc": df.index[events[0]].isoformat() if events.size else None,
            }
        )
    return out
