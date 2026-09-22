"""Exports CSV / XLSX / NetCDF avec métadonnées (modèle, run, point, coordonnées, hauteurs, unités, UTC)."""

from __future__ import annotations

import io
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import xarray as xr

from app.forecasts.analysis import FLAG_NAMES, to_long_frame

APP_VERSION = "0.2.0"


def metadata(
    job, site, extracts: list[tuple[str, xr.Dataset]], step: str, method: str, speed_method: str, tz: str | None
) -> dict:
    return {
        "application": f"greenard {APP_VERSION}",
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "job_id": job.id,
        "site": f"{site.name} ({site.lat:.6f}, {site.lon:.6f}) WGS84",
        "time_reference": "UTC (time_utc)" + ("; local time Africa/Casablanca (time_local)" if tz else ""),
        "target_step": step,
        "interpolation": f"{method} on U/V components; speed: {speed_method}; direction from interpolated U/V",
        "aggregation": "period (t - step, t]: scalar mean speed, vector mean direction, max gust",
        "time_flag": (
            "native = model lead time; aggregated = from finer native steps; interpolated = not a model lead time"
        ),
        "wind_direction": "meteorological convention: direction the wind comes FROM, degrees clockwise from north",
        "models": {
            model: {
                "run_utc": ds.attrs.get("run"),
                "source": ds.attrs.get("source"),
                "source_detail": ds.attrs.get("source_detail"),
                "licence": ds.attrs.get("licence", ds.attrs.get("licence_class", "")),
                "members": int(ds.sizes["member"]),
                "gust_semantics": ds.attrs.get("gust_semantics"),
                "missing_variables": ds.attrs.get("missing_variables", ""),
                "derived_heights_m": ds.attrs.get("derived_heights_m", ""),
                "points": [
                    {"grid_point_id": int(g), "lat": float(la), "lon": float(lo)}
                    for g, la, lo in zip(ds["grid_point_id"].values, ds["lat"].values, ds["lon"].values, strict=True)
                ],
            }
            for model, ds in extracts
        },
    }


def build_frame(extracts: list[tuple[str, xr.Dataset]], tz: str | None) -> tuple[pd.DataFrame, dict]:
    frames, units = [], {}
    for model, ds in extracts:
        df, u = to_long_frame(ds, model, tz)
        frames.append(df)
        units.update(u)
    return pd.concat(frames, ignore_index=True, sort=False), units


def to_csv(meta: dict, df: pd.DataFrame, units: dict) -> bytes:
    buf = io.StringIO()
    flat = {k: v for k, v in meta.items() if k != "models"}
    for k, v in flat.items():
        buf.write(f"# {k}: {v}\n")
    for model, m in meta["models"].items():
        buf.write(f"# model {model}: {json.dumps(m, ensure_ascii=False)}\n")
    buf.write(f"# units: {json.dumps(units, ensure_ascii=False)}\n")
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def to_xlsx(meta: dict, df: pd.DataFrame, units: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="data", index=False)
        rows = [(k, str(v)) for k, v in meta.items() if k != "models"]
        rows += [(f"model {m}", json.dumps(v, ensure_ascii=False)) for m, v in meta["models"].items()]
        pd.DataFrame(rows, columns=["key", "value"]).to_excel(xw, sheet_name="metadata", index=False)
        pd.DataFrame(sorted(units.items()), columns=["column", "unit"]).to_excel(xw, sheet_name="units", index=False)
        pts = [{"model": m, **p} for m, v in meta["models"].items() for p in v["points"]]
        pd.DataFrame(pts).to_excel(xw, sheet_name="points", index=False)
    return buf.getvalue()


def to_netcdf(meta: dict, extracts: list[tuple[str, xr.Dataset]]) -> bytes:
    """Un groupe NetCDF par modèle (dimensions member, point, time), conventions CF-1.8."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "export.nc"
        root = xr.Dataset(attrs={"Conventions": "CF-1.8", **{k: str(v) for k, v in meta.items() if k != "models"}})
        root.to_netcdf(path, mode="w")
        for model, ds in extracts:
            out = ds.copy()
            out["time_flag"].attrs = {"flag_values": [0, 1, 2], "flag_meanings": " ".join(FLAG_NAMES.values())}
            out["time"].attrs = {"standard_name": "time", "axis": "T"}
            out["lat"].attrs = {"standard_name": "latitude", "units": "degrees_north"}
            out["lon"].attrs = {"standard_name": "longitude", "units": "degrees_east"}
            for v in out.data_vars:
                if v.startswith("wd_"):
                    out[v].attrs = {"units": "degree", "standard_name": "wind_from_direction"}
                elif v.startswith("ws_"):
                    out[v].attrs = {"units": "m s-1", "standard_name": "wind_speed"}
            out.attrs = {k: str(v) for k, v in out.attrs.items()}
            enc = {v: {"zlib": True, "complevel": 4} for v in out.data_vars}
            enc["time"] = {"units": "hours since 1970-01-01 00:00:00", "calendar": "standard"}
            out.to_netcdf(path, mode="a", group=model, encoding=enc)
        return path.read_bytes()
