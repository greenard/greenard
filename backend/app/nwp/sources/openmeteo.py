"""Open-Meteo : prototypage rapide et ensembles (extraction au point côté serveur).

Précautions (§4.3.3 de la note d'architecture) :
- `cell_selection=nearest` et interrogation aux coordonnées exactes du nœud : valeur de la maille ;
- `elevation=nan` : pas de correction d'altitude (« downscaling ») appliquée par Open-Meteo ;
- Open-Meteo fournit un pas horaire même pour les modèles 3-/6-horaires (valeurs interpolées par
  Open-Meteo) : **seules les échéances natives du modèle sont conservées**, l'harmonisation
  temporelle est ensuite faite par Greenard avec sa méthode documentée ;
- licence : l'API publique est réservée à un usage non commercial (mode `public`). Modes
  `api_key` (abonnement) et `self_hosted` (instance auto-hébergée) pour un usage commercial.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import pandas as pd

from app.core.config import get_settings
from app.nwp.catalog import native_leads
from app.nwp.sources.base import (
    Estimate,
    FetchRequest,
    Progress,
    RawBuilder,
    Source,
    SourceError,
    get,
)
from app.nwp.variables import PRESSURE_RE, WIND_HEIGHT_RE
from app.nwp.wind import speed_dir_to_uv

# modèle Greenard → (identifiant API forecast/ensemble, identifiant « meta », latence typique h)
MODELS = {
    "gfs": ("gfs_global", "ncep_gfs025", 4),
    "ifs": ("ecmwf_ifs025", "ecmwf_ifs025", 7),
    "ifs_9km": ("ecmwf_ifs", "ecmwf_ifs", 7),
    "icon": ("icon_global", "dwd_icon", 4),
    "icon_eu": ("icon_eu", "dwd_icon_eu", 4),
    "gefs": ("gfs025", "ncep_gefs025", 6),
    "ens": ("ecmwf_ifs025", "ecmwf_ifs025_ensemble", 8),
}
ENSEMBLES = {"gefs", "ens"}
RUN_HOURS = {
    "ifs_9km": (0, 6, 12, 18),
    "gfs": (0, 6, 12, 18),
    "gefs": (0, 6, 12, 18),
    "ifs": (0, 6, 12, 18),
    "ens": (0, 12),
    "icon": (0, 6, 12, 18),
    "icon_eu": (0, 6, 12, 18),
}


def api_bases(ensemble: bool) -> tuple[str, dict]:
    s = get_settings()
    if s.open_meteo_mode == "self_hosted":
        if not s.open_meteo_base_url:
            raise SourceError("OPEN_METEO_NOT_CONFIGURED", "Self-hosted Open-Meteo URL is not configured")
        return s.open_meteo_base_url.rstrip("/"), {}
    if s.open_meteo_mode == "api_key":
        if not s.open_meteo_api_key:
            raise SourceError("OPEN_METEO_NOT_CONFIGURED", "Open-Meteo API key is not configured")
        host = "https://customer-ensemble-api.open-meteo.com" if ensemble else "https://customer-api.open-meteo.com"
        return host, {"apikey": s.open_meteo_api_key}
    return ("https://ensemble-api.open-meteo.com" if ensemble else "https://api.open-meteo.com"), {}


def om_variable(var: str) -> list[str] | None:
    """canonique → variables Open-Meteo nécessaires."""
    m = WIND_HEIGHT_RE.match(var)
    if m:
        h = m.group(2)
        return [f"wind_speed_{h}m", f"wind_direction_{h}m"]
    m = PRESSURE_RE.match(var)
    if m:
        comp, p = m.groups()
        return [f"temperature_{p}hPa"] if comp == "t" else [f"wind_speed_{p}hPa", f"wind_direction_{p}hPa"]
    return {
        "gust_10m": ["wind_gusts_10m"],
        "t_2m": ["temperature_2m"],
        "rh_2m": ["relative_humidity_2m"],
        "d_2m": ["dew_point_2m"],
        "sp": ["surface_pressure"],
        "msl": ["pressure_msl"],
    }.get(var)


class OpenMeteo(Source):
    code = "open_meteo"
    gust_semantics = "period_max"  # rafales Open-Meteo : maximum de l'heure précédente

    def __init__(self, model: str):
        if model not in MODELS:
            raise SourceError("SOURCE_MODEL_UNSUPPORTED", "Model not available on Open-Meteo", model=model)
        self.model = model
        self.ensemble = model in ENSEMBLES

    def latest_run(self, client: httpx.Client) -> datetime:
        """Run courant d'après les métadonnées Open-Meteo, sinon déduit de la latence typique."""
        _, meta_id, latency = MODELS[self.model]
        try:
            r = get(client, f"https://api.open-meteo.com/data/{meta_id}/static/meta.json", retries=1)
            if r.status_code == 200:
                ts = r.json().get("last_run_initialisation_time")
                if ts:
                    return datetime.fromtimestamp(int(ts), UTC)
        except (SourceError, ValueError):
            pass
        now = datetime.now(UTC) - timedelta(hours=latency)
        hours = [h for h in RUN_HOURS[self.model] if h <= now.hour]
        if hours:
            return now.replace(hour=max(hours), minute=0, second=0, microsecond=0)
        prev = now - timedelta(days=1)
        return prev.replace(hour=max(RUN_HOURS[self.model]), minute=0, second=0, microsecond=0)

    def _vars(self, req: FetchRequest):
        wanted, missing = {}, []
        for v in req.variables:
            om = om_variable(v)
            if om is None:
                missing.append(v)
            else:
                wanted[v] = om
        return wanted, missing

    def estimate(self, client: httpx.Client, req: FetchRequest) -> Estimate:
        wanted, missing = self._vars(req)
        leads = native_leads(self.model, req.run.hour, req.max_lead_h)
        return Estimate(
            1,
            50_000 * len(req.points) * (31 if self.ensemble else 1),
            leads,
            list(wanted),
            missing,
            list(range(0, 51)) if self.ensemble else [0],
            notes=[
                "Open-Meteo point extraction; only native lead times kept",
                f"licence mode: {get_settings().open_meteo_mode}",
            ],
        )

    def fetch(self, client: httpx.Client, req: FetchRequest, progress: Progress):
        wanted, missing = self._vars(req)
        base, auth = api_bases(self.ensemble)
        api_model = MODELS[self.model][0]
        om_vars = sorted({x for vs in wanted.values() for x in vs})
        days = min(16, math.ceil((req.max_lead_h + req.run.hour) / 24) + 1)
        params = {
            "latitude": ",".join(f"{p.lat:.5f}" for p in req.points),
            "longitude": ",".join(f"{p.lon:.5f}" for p in req.points),
            "elevation": ",".join("nan" for _ in req.points),
            "hourly": ",".join(om_vars),
            "models": api_model,
            "wind_speed_unit": "ms",
            "timezone": "GMT",
            "timeformat": "unixtime",
            "cell_selection": "nearest",
            "forecast_days": days,
            **auth,
        }
        path = "/v1/ensemble" if self.ensemble else "/v1/forecast"
        progress(0.1, "Open-Meteo")
        try:
            r = client.get(base + path, params=params)
        except httpx.HTTPError as exc:
            raise SourceError("SOURCE_UNREACHABLE", f"Cannot reach Open-Meteo: {exc}", url=base) from exc
        if r.status_code != 200:
            reason = ""
            try:
                reason = r.json().get("reason", "")
            except ValueError:
                pass
            raise SourceError(
                "SOURCE_HTTP_ERROR", f"Open-Meteo HTTP {r.status_code}: {reason}", url=base, status=r.status_code
            )
        payload = r.json()
        locations = payload if isinstance(payload, list) else [payload]
        if len(locations) != len(req.points):
            raise SourceError("SOURCE_BAD_RESPONSE", "Unexpected number of locations", url=base)

        leads = native_leads(self.model, req.run.hour, req.max_lead_h)
        run = pd.Timestamp(req.run).tz_convert(None)
        wanted_times = run + pd.to_timedelta(leads, unit="h")
        members = self._members(locations[0]["hourly"], om_vars)
        if req.members:
            members = [m for m in members if m in req.members]
        builder = RawBuilder(req, members, leads)
        for p_idx, loc in enumerate(locations):
            hourly = loc["hourly"]
            times = pd.to_datetime(np.asarray(hourly["time"], dtype="int64"), unit="s")
            pos = pd.Index(times).get_indexer(wanted_times)
            for member in members:
                series = {name: self._series(hourly, name, member, pos) for name in om_vars}
                for var, names in wanted.items():
                    if len(names) == 2:  # vitesse + direction → U, V
                        ws, wd = series[names[0]], series[names[1]]
                        if ws is None or wd is None:
                            continue
                        u, v = speed_dir_to_uv(ws, wd)
                        values = u if var.startswith("u_") else v
                    else:
                        values = series[names[0]]
                        if values is None:
                            continue
                        if var.startswith("t_"):
                            values = values + 273.15
                        elif var in ("sp", "msl"):
                            values = values * 100.0
                    builder.put_series(var, member, p_idx, values)
        progress(1.0, "done")
        ds = builder.build(
            self,
            source_detail=base,
            open_meteo_model=api_model,
            note="Open-Meteo hourly values kept only at native lead times of the model",
        )
        empty = [v for v in ds.data_vars if np.isnan(ds[v].values).all()]
        ds = ds.drop_vars(empty)
        ds.attrs["missing_variables"] = ",".join(sorted(set(missing) | set(empty)))
        return ds

    @staticmethod
    def _members(hourly: dict, om_vars: list[str]) -> list[int]:
        found = {0}
        for key in hourly:
            if "_member" in key:
                found.add(int(key.rsplit("_member", 1)[1]))
        return sorted(found)

    @staticmethod
    def _series(hourly: dict, name: str, member: int, pos: np.ndarray):
        key = name if member == 0 else f"{name}_member{member:02d}"
        raw = hourly.get(key)
        if raw is None:
            return None
        arr = np.asarray([np.nan if x is None else x for x in raw], dtype=float)
        out = np.full(pos.size, np.nan)
        ok = pos >= 0
        out[ok] = arr[pos[ok]]
        return out
