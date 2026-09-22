"""Altitude réelle : Copernicus DEM GLO-30 (tuiles COG 1°×1°, bucket AWS public, sans compte).

Les tuiles absentes correspondent à la mer : l'altitude vaut alors 0 m (drapeau `sea_tile`).
Un répertoire local de tuiles (mêmes noms) peut remplacer l'accès réseau (`GREENARD_DEM_LOCAL_DIR`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.windows import from_bounds

from app.core.config import get_settings

_GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
}


def tile_name(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_floor):02d}_00_{ew}{abs(lon_floor):03d}_00_DEM"


def tile_path(lat_floor: int, lon_floor: int) -> str:
    s = get_settings()
    name = tile_name(lat_floor, lon_floor)
    if s.dem_local_dir:
        return str(s.dem_local_dir / f"{name}.tif")
    return f"/vsicurl/{s.dem_base_url}/{name}/{name}.tif"


@dataclass
class DemSample:
    elevation_m: float
    sea_tile: bool


@lru_cache(maxsize=4096)
def sample(lat: float, lon: float) -> DemSample:
    """Altitude au point (plus proche pixel)."""
    lat_f, lon_f = math.floor(lat), math.floor(lon)
    try:
        with rasterio.Env(**_GDAL_ENV), rasterio.open(tile_path(lat_f, lon_f)) as ds:
            v = float(next(ds.sample([(lon, lat)]))[0])
    except RasterioIOError:
        return DemSample(0.0, True)
    return DemSample(v, False)


def box_mean(lat_min: float, lat_max: float, lon_min: float, lon_max: float, max_px: int = 120) -> float | None:
    """Altitude moyenne sur une boîte lat/lon (mosaïque des tuiles, lecture décimée via les aperçus COG).

    Les tuiles de mer comptent pour 0 m, pondérées par leur surface dans la boîte.
    """
    total, weight = 0.0, 0.0
    for lat_f in range(math.floor(lat_min), math.floor(lat_max) + 1):
        for lon_f in range(math.floor(lon_min), math.floor(lon_max) + 1):
            b = (max(lon_min, lon_f), max(lat_min, lat_f), min(lon_max, lon_f + 1), min(lat_max, lat_f + 1))
            if b[0] >= b[2] or b[1] >= b[3]:
                continue
            w = (b[2] - b[0]) * (b[3] - b[1]) * math.cos(math.radians((b[1] + b[3]) / 2))
            try:
                with rasterio.Env(**_GDAL_ENV), rasterio.open(tile_path(lat_f, lon_f)) as ds:
                    win = from_bounds(*b, transform=ds.transform)
                    nrow = max(1, min(max_px, int(round(win.height))))
                    ncol = max(1, min(max_px, int(round(win.width))))
                    arr = ds.read(1, window=win, out_shape=(nrow, ncol), boundless=True, fill_value=np.nan)
                    m = float(np.nanmean(arr)) if np.isfinite(arr).any() else 0.0
            except RasterioIOError:
                m = 0.0
            total += m * w
            weight += w
    return total / weight if weight > 0 else None
