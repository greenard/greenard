"""Tables harmonisées, séries pour les graphiques, rose des vents et comparaison inter-modèles."""

from __future__ import annotations

from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import xarray as xr

from app.nwp.timeseries import harmonise
from app.nwp.wind import uv_to_speed_dir

FLAG_NAMES = {0: "native", 1: "aggregated", 2: "interpolated"}
LOCAL_TZ = "Africa/Casablanca"


def export_columns(ds: xr.Dataset) -> list[tuple[str, str, str, callable]]:
    """(colonne, variable source, unité, conversion) dans l'ordre d'export."""
    cols = []
    ws = sorted([v for v in ds.data_vars if v.startswith("ws_")], key=_level_key)
    for v in ws:
        suffix = v[3:]
        cols.append((f"ws_{suffix}", v, "m/s", lambda a: a))
        cols.append((f"wd_{suffix}", f"wd_{suffix}", "deg (from, clockwise from N)", lambda a: a))
    table = [
        ("gust_10m", "gust_10m", "m/s", lambda a: a),
        ("t_2m", "t_2m", "degC", lambda a: a - 273.15),
        ("rh_2m", "rh_2m", "%", lambda a: a),
        ("sp", "sp", "hPa", lambda a: a / 100.0),
        ("msl", "msl", "hPa", lambda a: a / 100.0),
    ]
    cols += [c for c in table if c[1] in ds]
    cols += [
        (v, v, "degC", lambda a: a - 273.15) for v in sorted(ds.data_vars) if v.startswith("t_") and v.endswith("hPa")
    ]
    return cols


def _level_key(name: str):
    s = name.split("_", 1)[1]
    return (s.endswith("hPa"), int("".join(c for c in s if c.isdigit()) or 0))


def to_long_frame(ds: xr.Dataset, model: str, tz: str | None) -> tuple[pd.DataFrame, dict]:
    """Table longue (une ligne par temps × membre × point) aux unités d'export."""
    cols = export_columns(ds)
    times = pd.DatetimeIndex(ds["time"].values)
    nm, npnt, nt = ds.sizes["member"], ds.sizes["point"], ds.sizes["time"]
    frame = {
        "time_utc": np.tile(times.strftime("%Y-%m-%dT%H:%M:%SZ"), nm * npnt),
        "model": model,
        "run_utc": ds.attrs.get("run", ""),
        "source": ds.attrs.get("source", ""),
        "member": np.repeat(ds["member"].values, npnt * nt),
        "grid_point_id": np.tile(np.repeat(ds["grid_point_id"].values, nt), nm),
        "lat": np.tile(np.repeat(ds["lat"].values, nt), nm),
        "lon": np.tile(np.repeat(ds["lon"].values, nt), nm),
        "lead_h": np.tile(
            ((times - pd.Timestamp(ds.attrs["run"].rstrip("Z"))).total_seconds() / 3600).round(3), nm * npnt
        ),
        "time_flag": np.tile(np.vectorize(FLAG_NAMES.get)(ds["time_flag"].values), nm * npnt),
    }
    if tz == LOCAL_TZ:
        local = times.tz_localize("UTC").tz_convert(ZoneInfo(LOCAL_TZ))
        frame["time_local"] = np.tile(local.strftime("%Y-%m-%dT%H:%M:%S%z"), nm * npnt)
    units = {}
    for col, var, unit, conv in cols:
        frame[col] = np.round(conv(ds[var].values.astype(float)).reshape(-1), 3)
        units[col] = unit
    df = pd.DataFrame(frame)
    order = (
        ["time_utc"]
        + (["time_local"] if "time_local" in frame else [])
        + [c for c in df.columns if c not in ("time_utc", "time_local")]
    )
    return df[order], units


def ensemble_stats(arr: np.ndarray) -> dict:
    """arr (member, time) → quantiles par temps."""
    q = np.nanpercentile(arr, [10, 25, 50, 75, 90], axis=0)
    return {
        "p10": q[0],
        "p25": q[1],
        "p50": q[2],
        "p75": q[3],
        "p90": q[4],
        "min": np.nanmin(arr, axis=0),
        "max": np.nanmax(arr, axis=0),
        "mean": np.nanmean(arr, axis=0),
    }


def _clean(a) -> list:
    return [None if not np.isfinite(x) else round(float(x), 3) for x in np.asarray(a, dtype=float)]


def local_times(times: pd.DatetimeIndex) -> list[str]:
    """Heures locales Maroc calculées côté serveur (tzdata IANA de l'image, tenue à jour) plutôt que par
    le navigateur, dont la base de fuseaux peut être ancienne."""
    return times.tz_localize("UTC").tz_convert(ZoneInfo(LOCAL_TZ)).strftime("%Y-%m-%dT%H:%M:%S%z").tolist()


def series_payload(ds: xr.Dataset, model: str) -> dict:
    """Séries pour les graphiques : par point, valeurs (déterministe) ou quantiles (ensemble)."""
    cols = export_columns(ds)
    times = pd.DatetimeIndex(ds["time"].values).strftime("%Y-%m-%dT%H:%M:%SZ").tolist()
    ens = ds.sizes["member"] > 1
    points = []
    for p in range(ds.sizes["point"]):
        item = {
            "grid_point_id": int(ds["grid_point_id"].values[p]),
            "lat": float(ds["lat"].values[p]),
            "lon": float(ds["lon"].values[p]),
            "variables": {},
        }
        for col, var, unit, conv in cols:
            if col.startswith("wd_") and ens:
                # direction de la moyenne vectorielle d'ensemble
                suffix = col[3:]
                u = ds[f"u_{suffix}"].values[:, p, :].mean(axis=0)
                v = ds[f"v_{suffix}"].values[:, p, :].mean(axis=0)
                item["variables"][col] = {"unit": unit, "values": _clean(uv_to_speed_dir(u, v)[1])}
                continue
            arr = conv(ds[var].values[:, p, :].astype(float))
            if ens:
                item["variables"][col] = {"unit": unit, **{k: _clean(v) for k, v in ensemble_stats(arr).items()}}
            else:
                item["variables"][col] = {"unit": unit, "values": _clean(arr[0])}
        points.append(item)
    return {
        "model": model,
        "run": ds.attrs.get("run"),
        "source": ds.attrs.get("source"),
        "members": int(ds.sizes["member"]),
        "times": times,
        "times_local": local_times(pd.DatetimeIndex(ds["time"].values)),
        "flags": [FLAG_NAMES[int(f)] for f in ds["time_flag"].values],
        "points": points,
        "derived_heights_m": ds.attrs.get("derived_heights_m", ""),
        "missing_variables": ds.attrs.get("missing_variables", ""),
    }


SPEED_BINS = [0, 3, 6, 9, 12, 15, np.inf]


def wind_rose(raw: xr.Dataset, height: int, sectors: int = 12) -> dict | None:
    """Fréquences (%) par secteur et classe de vitesse, sur les échéances natives, tous points et membres."""
    un, vn = f"u_{height}m", f"v_{height}m"
    if un not in raw or vn not in raw:
        return None
    ws, wd = uv_to_speed_dir(raw[un].values.ravel(), raw[vn].values.ravel())
    ok = np.isfinite(ws) & np.isfinite(wd)
    ws, wd = ws[ok], wd[ok]
    if ws.size == 0:
        return None
    width = 360.0 / sectors
    sec = np.floor(((wd + width / 2) % 360.0) / width).astype(int)
    bins = np.digitize(ws, SPEED_BINS[1:-1])
    freq = np.zeros((len(SPEED_BINS) - 1, sectors))
    np.add.at(freq, (bins, sec), 1)
    freq = 100.0 * freq / ws.size
    labels = [
        f"{SPEED_BINS[k]:g}–{SPEED_BINS[k + 1]:g}" if np.isfinite(SPEED_BINS[k + 1]) else f"≥ {SPEED_BINS[k]:g}"
        for k in range(len(SPEED_BINS) - 1)
    ]
    return {
        "sectors_deg": [round(k * width, 1) for k in range(sectors)],
        "speed_bins": labels,
        "frequency_pct": [[round(x, 2) for x in row] for row in freq],
        "n": int(ws.size),
        "mean_speed": round(float(ws.mean()), 2),
    }


def comparison(extracts: dict[str, xr.Dataset], height: int, step: str = "3h", method: str = "linear") -> dict:
    """Vitesse à `height` : moyenne des points sélectionnés (et moyenne d'ensemble), sur les temps communs.

    Référence : moyenne multi-modèle. Indicateurs par modèle : moyenne, écart-type, biais et écart
    quadratique (RMSD) par rapport à la moyenne multi-modèle, corrélation.
    """
    series = {}
    for model, raw in extracts.items():
        if f"u_{height}m" not in raw:
            continue
        h = harmonise(raw[[f"u_{height}m", f"v_{height}m"]].assign_attrs(raw.attrs), step, method)
        ws = h[f"ws_{height}m"].mean(dim=("member", "point")).to_series()
        series[model] = ws
    if len(series) < 1:
        return {"height_m": height, "models": [], "times": [], "stats": []}
    df = pd.DataFrame(series).dropna()
    mmm = df.mean(axis=1)
    stats = []
    for m in df.columns:
        d = df[m] - mmm
        stats.append(
            {
                "model": m,
                "mean": round(float(df[m].mean()), 3),
                "std": round(float(df[m].std()), 3),
                "bias_vs_mmm": round(float(d.mean()), 3),
                "rmsd_vs_mmm": round(float(np.sqrt((d**2).mean())), 3),
                "corr_vs_mmm": round(float(df[m].corr(mmm)), 3) if len(df) > 2 and len(df.columns) > 1 else None,
            }
        )
    return {
        "height_m": height,
        "step": step,
        "times": pd.DatetimeIndex(df.index).strftime("%Y-%m-%dT%H:%M:%SZ").tolist(),
        "models": list(df.columns),
        "values": {m: _clean(df[m].values) for m in df.columns},
        "multi_model_mean": _clean(mmm.values),
        "stats": stats,
        "n_common_times": int(len(df)),
    }
