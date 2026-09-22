"""Champs invariants des modèles : géométrie de grille, altitude du modèle, fraction de terre.

Préparés une fois (tâche Celery `prepare_invariants`), mis en cache dans
`<data_dir>/invariants/`, puis relus à chaque recherche de points de grille.

- Grilles régulières : NetCDF recadré sur `settings.invariants_bbox` (Maroc + marges) avec les
  attributs de la grille complète, relus dans les en-têtes GRIB.
- ICON : archive `.npz` (clat/clon, éventuels sommets de triangles, HSURF, FR_LAND).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr

from app.core.config import get_settings
from app.core.errors import AppError
from app.nwp.catalog import INVARIANTS_FROM, get_model
from app.nwp.grids.icosahedral import IcosahedralGrid
from app.nwp.grids.regular import RegularGrid


class InvariantsNotReady(AppError):
    status_code = 409


def invariants_dir() -> Path:
    d = get_settings().data_dir / "invariants"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _source_code(model_code: str) -> str:
    return INVARIANTS_FROM.get(model_code, model_code)


def cache_path(model_code: str) -> Path:
    code = _source_code(model_code)
    ext = "npz" if get_model(code).grid_type == "icosahedral" else "nc"
    return invariants_dir() / f"{code}.{ext}"


def meta_path(model_code: str) -> Path:
    return invariants_dir() / f"{_source_code(model_code)}.json"


def status(model_code: str) -> dict:
    if not get_model(_source_code(model_code)).invariants:
        return {"ready": False, "unavailable": True}
    mp = meta_path(model_code)
    if cache_path(model_code).exists() and mp.exists():
        return {"ready": True, **json.loads(mp.read_text())}
    return {"ready": False}


def write_meta(model_code: str, **meta) -> None:
    meta.setdefault("prepared_at", datetime.now(UTC).isoformat())
    meta_path(model_code).write_text(json.dumps(meta, indent=2))


# --------------------------------------------------------------------------------------------
# Grilles régulières
# --------------------------------------------------------------------------------------------


@dataclass
class RegularInvariants:
    grid: RegularGrid  # grille complète (géométrie relue dans le GRIB)
    i0: int  # décalage du recadrage
    j0: int
    elevation: np.ndarray  # (ni, nj) m
    land_fraction: np.ndarray  # (ni, nj) 0..1

    def lookup(self, i: int, j: int) -> tuple[float | None, float | None]:
        li, lj = i - self.i0, j - self.j0
        if self.grid.global_lon:
            lj = (j - self.j0) % self.grid.nlon
        if 0 <= li < self.elevation.shape[0] and 0 <= lj < self.elevation.shape[1]:
            e, f = self.elevation[li, lj], self.land_fraction[li, lj]
            return (None if np.isnan(e) else float(e)), (None if np.isnan(f) else float(f))
        return None, None

    def save(self, path: Path) -> None:
        g = self.grid
        ds = xr.Dataset(
            {
                "elevation": (("i", "j"), self.elevation.astype("float32"), {"units": "m"}),
                "land_fraction": (("i", "j"), self.land_fraction.astype("float32"), {"units": "1"}),
            },
            attrs={
                "lat_first": g.lat_first,
                "dlat": g.dlat,
                "nlat": g.nlat,
                "lon_first": g.lon_first,
                "dlon": g.dlon,
                "nlon": g.nlon,
                "global_lon": int(g.global_lon),
                "i0": self.i0,
                "j0": self.j0,
            },
        )
        ds.to_netcdf(path)

    @classmethod
    def load(cls, path: Path) -> RegularInvariants:
        with xr.open_dataset(path) as ds:
            a = ds.attrs
            grid = RegularGrid(
                float(a["lat_first"]),
                float(a["dlat"]),
                int(a["nlat"]),
                float(a["lon_first"]),
                float(a["dlon"]),
                int(a["nlon"]),
                bool(a["global_lon"]),
            )
            return cls(grid, int(a["i0"]), int(a["j0"]), ds["elevation"].values, ds["land_fraction"].values)


def crop_regular(
    grid: RegularGrid, elevation: np.ndarray, land: np.ndarray, bbox: tuple[float, float, float, float]
) -> RegularInvariants:
    """Recadre des champs (nlat, nlon) sur bbox (lat_min, lat_max, lon_min, lon_max), avec une maille de marge."""
    lat_min, lat_max, lon_min, lon_max = bbox
    fi = sorted([(lat_min - grid.lat_first) / grid.dlat, (lat_max - grid.lat_first) / grid.dlat])
    i0 = max(int(np.floor(fi[0])) - 1, 0)
    i1 = min(int(np.ceil(fi[1])) + 1, grid.nlat - 1)
    if grid.global_lon:
        j0 = int(np.floor(((lon_min - grid.lon_first) % 360.0) / grid.dlon)) - 1
        width = int(np.ceil((lon_max - lon_min) / grid.dlon)) + 3
        js = np.mod(np.arange(j0, j0 + width), grid.nlon)
        j0 = int(js[0])
    else:
        ja = max(int(np.floor((lon_min - grid.lon_first) / grid.dlon)) - 1, 0)
        jb = min(int(np.ceil((lon_max - grid.lon_first) / grid.dlon)) + 1, grid.nlon - 1)
        js = np.arange(ja, jb + 1)
        j0 = ja
    return RegularInvariants(grid, i0, j0, elevation[i0 : i1 + 1][:, js], land[i0 : i1 + 1][:, js])


# --------------------------------------------------------------------------------------------
# ICON
# --------------------------------------------------------------------------------------------


@dataclass
class IconInvariants:
    grid: IcosahedralGrid
    hsurf: np.ndarray | None
    fr_land: np.ndarray | None

    def lookup(self, idx: int) -> tuple[float | None, float | None]:
        e = None if self.hsurf is None else float(self.hsurf[idx])
        f = None if self.fr_land is None else float(self.fr_land[idx])
        return e, f

    def save(self, path: Path) -> None:
        extra = {}
        if self.grid.vertices_xyz is not None:
            extra["vertices_xyz"] = self.grid.vertices_xyz.astype(np.float32)
        if self.hsurf is not None:
            extra["hsurf"] = self.hsurf.astype(np.float32)
        if self.fr_land is not None:
            extra["fr_land"] = self.fr_land.astype(np.float32)
        with open(path, "wb") as fh:
            np.savez(fh, clat=self.grid.clat, clon=self.grid.clon, **extra)

    @classmethod
    def load(cls, path: Path) -> IconInvariants:
        with np.load(path) as z:
            grid = IcosahedralGrid(z["clat"], z["clon"])
            if "vertices_xyz" in z:
                grid.vertices_xyz = z["vertices_xyz"].astype(float)
            return cls(grid, z["hsurf"] if "hsurf" in z else None, z["fr_land"] if "fr_land" in z else None)


# --------------------------------------------------------------------------------------------
# Chargement avec cache mémoire
# --------------------------------------------------------------------------------------------

_MEM: dict[str, tuple[float, object]] = {}


def load(model_code: str) -> RegularInvariants | IconInvariants:
    path = cache_path(model_code)
    if not path.exists():
        raise InvariantsNotReady(
            "INVARIANTS_NOT_READY",
            f"Invariant fields for {model_code} are not prepared yet",
            model=model_code,
        )
    mtime = path.stat().st_mtime
    key = str(path)
    hit = _MEM.get(key)
    if hit and hit[0] == mtime:
        return hit[1]  # type: ignore[return-value]
    code = _source_code(model_code)
    obj = IconInvariants.load(path) if get_model(code).grid_type == "icosahedral" else RegularInvariants.load(path)
    _MEM[key] = (mtime, obj)
    return obj
