"""ICON global et ICON-EU (DWD opendata, licence libre avec attribution).

Un fichier `.grib2.bz2` par variable, niveau et échéance, champ complet (grille icosaédrique
~2,9 M cellules pour ICON, grille régulière 0,0625° pour ICON-EU). Le DWD ne conserve que ~24 h :
l'archivage automatique (Celery beat) est indispensable pour constituer un historique.

Vent au-dessus de 10 m : interpolation verticale depuis les niveaux modèle les plus bas
(U, V aux niveaux « model-level », hauteurs des demi-niveaux `HHL`), linéaire en ln(z).
Ces hauteurs sont marquées « dérivées » dans les métadonnées.

⚠ Non testé sur les fichiers réels depuis l'environnement de développement (DWD inaccessible) :
à valider sur le serveur (`pytest -m network`).
"""

from __future__ import annotations

import bz2
from datetime import datetime

import httpx
import numpy as np

from app.core.config import get_settings
from app.nwp.grib import decode_grib, extract_regular
from app.nwp.sources.base import (
    Estimate,
    FetchRequest,
    Progress,
    RawBuilder,
    Source,
    SourceError,
    check_size,
    get,
    parallel,
    recent_runs,
)
from app.nwp.variables import PRESSURE_RE, WIND_HEIGHT_RE

_SINGLE = {
    "u_10m": "U_10M",
    "v_10m": "V_10M",
    "gust_10m": "VMAX_10M",
    "t_2m": "T_2M",
    "d_2m": "TD_2M",
    "rh_2m": "RELHUM_2M",
    "sp": "PS",
    "msl": "PMSL",
}
PRESSURE_LEVELS = (1000, 950, 925, 850, 700)


def vertical_interp(z_levels: np.ndarray, values: np.ndarray, heights: list[int]) -> np.ndarray:
    """Interpolation linéaire en ln(z) : z_levels (nlev, npt) m sol, values (nlev, npt) → (nh, npt)."""
    out = np.full((len(heights), values.shape[1]), np.nan)
    order = np.argsort(z_levels, axis=0)
    z = np.take_along_axis(z_levels, order, axis=0)
    v = np.take_along_axis(values, order, axis=0)
    for k, h in enumerate(heights):
        for p in range(values.shape[1]):
            if z[0, p] <= h <= z[-1, p]:
                out[k, p] = np.interp(np.log(h), np.log(z[:, p]), v[:, p])
    return out


class _DwdBase(Source):
    code = "dwd"
    gust_semantics = "period_max"  # VMAX_10M : maximum depuis la sortie précédente
    model_dir = "icon"
    prefix = "icon_global_icosahedral"
    nlev = 120  # niveaux modèle (demi-niveaux HHL : nlev + 1)
    bytes_per_field = 3_500_000
    icosahedral = True

    def steps(self, run: datetime, max_lead_h: int) -> list[int]:
        last = 180 if (self.model == "icon" and run.hour in (0, 12)) else 120
        steps = list(range(0, 79)) + list(range(81, last + 1, 3))
        return [s for s in steps if s <= max_lead_h]

    def url(self, run: datetime, var_dir: str, kind: str, step: int | None, var: str, level: int | None = None) -> str:
        base = get_settings().dwd_base_url
        parts = [f"{self.prefix}_{kind}", f"{run:%Y%m%d%H}"]
        if step is not None:
            parts.append(f"{step:03d}")
        if level is not None:
            parts.append(str(level))
        parts.append(var)
        return f"{base}/{self.model_dir}/grib/{run:%H}/{var_dir}/{'_'.join(parts)}.grib2.bz2"

    def latest_run(self, client: httpx.Client) -> datetime:
        for run in recent_runs((0, 6, 12, 18), days=2, min_age_h=4):
            last = self.steps(run, 999)[-1]
            if get(client, self.url(run, "t_2m", "single-level", last, "T_2M")).status_code == 200:
                return run
        raise SourceError("SOURCE_NO_RECENT_RUN", "No complete recent run found on DWD opendata", model=self.model)

    def _files(self, req: FetchRequest):
        files, missing, derived_heights = [], [], []
        steps = self.steps(req.run, req.max_lead_h)
        for v in req.variables:
            if v in _SINGLE:
                files.append((v, v.lower() if v != "d_2m" else "td_2m", "single-level", None, _SINGLE[v]))
                continue
            m = WIND_HEIGHT_RE.match(v)
            if m and int(m.group(2)) != 10:
                if m.group(1) == "u":
                    derived_heights.append(int(m.group(2)))
                continue
            m = PRESSURE_RE.match(v)
            if m and int(m.group(2)) in PRESSURE_LEVELS:
                comp, p = m.group(1), int(m.group(2))
                files.append((v, comp, "pressure-level", p, comp.upper()))
                continue
            missing.append(v)
        levels = list(range(self.nlev - 5, self.nlev + 1)) if derived_heights else []
        for lev in levels:
            files.append((f"__ml_u_{lev}", "u", "model-level", lev, "U"))
            files.append((f"__ml_v_{lev}", "v", "model-level", lev, "V"))
        return steps, files, missing, sorted(set(derived_heights)), levels

    def estimate(self, client: httpx.Client, req: FetchRequest) -> Estimate:
        steps, files, missing, heights, levels = self._files(req)
        n = len(steps) * len(files) + (len(levels) + 1 if levels else 0)
        avail = [f[0] for f in files if not f[0].startswith("__")] + [f"{c}_{h}m" for h in heights for c in "uv"]
        est = Estimate(
            n,
            n * self.bytes_per_field,
            steps,
            avail,
            missing,
            [0],
            notes=["DWD opendata keeps ~24 h only; sizes estimated"],
        )
        if heights:
            est.notes.append(f"winds at {heights} m derived from model levels {levels[0]}-{levels[-1]} (ln z)")
        return est

    def _values(self, client: httpx.Client, url: str, req: FetchRequest) -> np.ndarray | None:
        r = get(client, url)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise SourceError("SOURCE_HTTP_ERROR", f"HTTP {r.status_code} on {url}", url=url, status=r.status_code)
        msg = decode_grib(bz2.decompress(r.content))[0]
        if self.icosahedral:
            return msg["values"][[p.native_index for p in req.points]]
        return extract_regular(msg, [p.lat for p in req.points], [p.lon for p in req.points])

    def fetch(self, client: httpx.Client, req: FetchRequest, progress: Progress):
        est = self.estimate(client, req)
        check_size(est)
        steps, files, _, heights, levels = self._files(req)
        builder = RawBuilder(req, [0], steps)
        hhl = {}
        if levels:
            for half in range(levels[0], self.nlev + 2):
                vals = self._values(client, self.url(req.run, "hhl", "time-invariant", None, "HHL", half), req)
                if vals is None:
                    raise SourceError("SOURCE_FIELD_MISSING", "HHL not found", field=f"HHL level {half}")
                hhl[half] = vals
        failed = []

        def job(item):
            step, (name, vdir, kind, level, var) = item
            vals = self._values(client, self.url(req.run, vdir, kind, step, var, level), req)
            if vals is None:
                failed.append(f"{step}:{name}")
                return
            builder.put(name, 0, step, vals)

        items = [(s, f) for s in steps for f in files]
        parallel(items, job, progress, "DWD", get_settings().download_workers)
        if levels:
            surface = hhl[self.nlev + 1]
            z = np.stack([(hhl[k] + hhl[k + 1]) / 2 - surface for k in levels])  # (nlev_sel, npt)
            u = np.stack([builder.data[f"__ml_u_{k}"] for k in levels])  # (nlev_sel, 1, npt, nt)
            v = np.stack([builder.data[f"__ml_v_{k}"] for k in levels])
            for t in range(len(steps)):
                ui = vertical_interp(z, u[:, 0, :, t], heights)
                vi = vertical_interp(z, v[:, 0, :, t], heights)
                for k, h in enumerate(heights):
                    builder.put(f"u_{h}m", 0, steps[t], ui[k])
                    builder.put(f"v_{h}m", 0, steps[t], vi[k])
            for k in levels:
                builder.data.pop(f"__ml_u_{k}")
                builder.data.pop(f"__ml_v_{k}")
        ds = builder.build(
            self,
            source_detail=get_settings().dwd_base_url,
            missing_steps=",".join(sorted(failed)) or None,
            derived_heights_m=",".join(map(str, heights)) or None,
            licence="DWD open data (attribution)",
        )
        return ds


class IconDwd(_DwdBase):
    model = "icon"


class IconEuDwd(_DwdBase):
    model = "icon_eu"
    model_dir = "icon-eu"
    prefix = "icon-eu_europe_regular-lat-lon"
    nlev = 74  # à confirmer sur les fichiers réels (HHL : 75 demi-niveaux)
    bytes_per_field = 900_000
    icosahedral = False
