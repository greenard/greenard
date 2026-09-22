"""Grille de Gauss réduite octaédrique (ECMWF « O » N), ex. O1280 de l'IFS 9 km.

- 2N latitudes de Gauss (racines du polynôme de Legendre P_2N), du Nord au Sud ;
- rangée i (1 = la plus proche du pôle, symétrique par rapport à l'équateur) : 4·i + 16 points ;
- longitudes équiréparties à partir de 0° ;
- index natif : rangs cumulés, ordre du fichier GRIB (Nord → Sud, puis longitude croissante).
Pour O1280 : 2 560 rangées, 6 599 680 points.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from app.geo.geodesy import distance_azimuth


@lru_cache(maxsize=4)
def gaussian_latitudes(n: int) -> np.ndarray:
    """2n latitudes de Gauss (degrés), Nord → Sud."""
    x, _ = np.polynomial.legendre.leggauss(2 * n)
    return np.degrees(np.arcsin(x))[::-1].copy()


class ReducedGaussianGrid:
    def __init__(self, n: int = 1280, octahedral: bool = True):
        if not octahedral:
            raise NotImplementedError("only octahedral reduced Gaussian grids are supported")
        self.n = n
        self.lats = gaussian_latitudes(n)
        k = np.arange(1, n + 1)
        half = 4 * k + 16
        self.counts = np.concatenate([half, half[::-1]])
        self.offsets = np.concatenate([[0], np.cumsum(self.counts)[:-1]])
        self.size = int(self.counts.sum())

    # ---- géométrie ----------------------------------------------------------------------------
    def lon_of(self, row: int, j: int) -> float:
        lon = 360.0 * j / int(self.counts[row])
        return ((lon + 180.0) % 360.0) - 180.0

    def native_index(self, row: int, j: int) -> int:
        return int(self.offsets[row] + j % int(self.counts[row]))

    def row_j_of(self, idx: int) -> tuple[int, int]:
        row = int(np.searchsorted(self.offsets, idx, side="right") - 1)
        return row, int(idx - self.offsets[row])

    def lat_lon(self, idx: int) -> tuple[float, float]:
        row, j = self.row_j_of(idx)
        return float(self.lats[row]), self.lon_of(row, j)

    def _row_above(self, lat: float) -> int:
        """Rangée juste au nord (ou sur) la latitude donnée."""
        # self.lats décroissant
        r = int(np.searchsorted(-self.lats, -lat, side="right") - 1)
        return min(max(r, 0), 2 * self.n - 2)

    def _bracket_in_row(self, row: int, lon: float) -> tuple[int, int]:
        nlon = int(self.counts[row])
        f = (lon % 360.0) / (360.0 / nlon)
        j0 = int(math.floor(f)) % nlon
        return j0, (j0 + 1) % nlon

    # ---- recherche ----------------------------------------------------------------------------
    def bracketing(self, lat: float, lon: float) -> list[tuple[int, int]]:
        """4 points encadrants : 2 sur la rangée au nord, 2 sur la rangée au sud (rangées d'effectifs différents)."""
        r0 = self._row_above(lat)
        out = []
        for row in (r0, r0 + 1):
            for j in self._bracket_in_row(row, lon):
                out.append((row, j))
        return out

    def nearest(self, lat: float, lon: float, n: int) -> list[tuple[int, int, float, float]]:
        r0 = self._row_above(lat)
        span = int(math.ceil(math.sqrt(n))) + 1
        rows, js = [], []
        for row in range(max(r0 - span, 0), min(r0 + span + 2, 2 * self.n)):
            j0, _ = self._bracket_in_row(row, lon)
            for dj in range(-span, span + 2):
                rows.append(row)
                js.append((j0 + dj) % int(self.counts[row]))
        rows_a, js_a = np.asarray(rows), np.asarray(js)
        lats = self.lats[rows_a]
        lons = np.array([self.lon_of(r, j) for r, j in zip(rows, js, strict=True)])
        dist, az = distance_azimuth(lat, lon, lats, lons)
        idx = np.array([self.native_index(r, j) for r, j in zip(rows, js, strict=True)])
        order = np.lexsort((idx, np.round(dist, 3)))[:n]
        return [(int(rows_a[k]), int(js_a[k]), float(dist[k]), float(az[k])) for k in order]
