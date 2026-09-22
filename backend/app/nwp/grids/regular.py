"""Grilles latitude/longitude régulières (GFS, IFS, ICON-EU, GEFS, ENS).

Convention d'index : `i` le long des latitudes (ordre du fichier), `j` le long des longitudes,
index natif `i * nlon + j`. Les longitudes du fichier peuvent être en 0–360 (GFS, IFS) ou en
−180–180 (ICON-EU) ; les coordonnées exposées sont toujours ramenées en −180–180.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.geo.geodesy import distance_azimuth


def wrap180(lon):
    return ((np.asarray(lon, dtype=float) + 180.0) % 360.0) - 180.0


@dataclass(frozen=True)
class RegularGrid:
    lat_first: float  # latitude de la première ligne du fichier
    dlat: float  # pas signé (négatif si les latitudes décroissent, ex. GFS 90 → −90)
    nlat: int
    lon_first: float
    dlon: float  # pas positif
    nlon: int
    global_lon: bool = False  # True si la grille fait le tour complet (périodicité en longitude)

    # ---- géométrie -------------------------------------------------------------------------
    @property
    def lat_min(self) -> float:
        return min(self.lat_first, self.lat_first + (self.nlat - 1) * self.dlat)

    @property
    def lat_max(self) -> float:
        return max(self.lat_first, self.lat_first + (self.nlat - 1) * self.dlat)

    @property
    def lon_min(self) -> float:
        return float(wrap180(self.lon_first)) if not self.global_lon else -180.0

    @property
    def lon_max(self) -> float:
        return float(wrap180(self.lon_first)) + (self.nlon - 1) * self.dlon if not self.global_lon else 180.0

    def lat_of(self, i):
        return self.lat_first + np.asarray(i) * self.dlat

    def lon_of(self, j):
        return wrap180(self.lon_first + np.asarray(j) * self.dlon)

    def native_index(self, i, j):
        return np.asarray(i) * self.nlon + np.asarray(j)

    def ij_of(self, native_index):
        return np.divmod(np.asarray(native_index), self.nlon)

    # ---- positions fractionnaires ------------------------------------------------------------
    def _frac_i(self, lat: float) -> float:
        return (lat - self.lat_first) / self.dlat

    def _frac_j(self, lon: float) -> float:
        d = (lon - self.lon_first) % 360.0 if self.global_lon else lon - wrap180(self.lon_first)
        return float(d) / self.dlon

    def contains(self, lat: float, lon: float, margin_cells: int = 0) -> bool:
        fi = self._frac_i(lat)
        if not (margin_cells <= fi <= self.nlat - 1 - margin_cells):
            return False
        if self.global_lon:
            return True
        fj = self._frac_j(lon)
        return margin_cells <= fj <= self.nlon - 1 - margin_cells

    def _wrap_j(self, j: np.ndarray) -> np.ndarray:
        return np.mod(j, self.nlon) if self.global_lon else j

    def _valid(self, i: np.ndarray, j: np.ndarray) -> np.ndarray:
        ok = (i >= 0) & (i < self.nlat)
        if not self.global_lon:
            ok &= (j >= 0) & (j < self.nlon)
        return ok

    # ---- recherche --------------------------------------------------------------------------
    def bracketing(self, lat: float, lon: float) -> list[tuple[int, int]]:
        """Les 4 nœuds encadrant le point (maille contenant le site)."""
        fi, fj = self._frac_i(lat), self._frac_j(lon)
        i0 = min(max(int(math.floor(fi)), 0), self.nlat - 2)
        j0 = int(math.floor(fj))
        if not self.global_lon:
            j0 = min(max(j0, 0), self.nlon - 2)
        out = []
        for di in (0, 1):
            for dj in (0, 1):
                j = (j0 + dj) % self.nlon if self.global_lon else j0 + dj
                out.append((i0 + di, j))
        return out

    def nearest(self, lat: float, lon: float, n: int) -> list[tuple[int, int, float, float]]:
        """Les `n` nœuds les plus proches (distance géodésique) : [(i, j, distance_m, azimut_deg)]."""
        if n < 1:
            raise ValueError("n must be >= 1")
        half = int(math.ceil(math.sqrt(n))) + 1
        fi, fj = self._frac_i(lat), self._frac_j(lon)
        ci, cj = int(round(fi)), int(round(fj))
        ii, jj = np.meshgrid(np.arange(ci - half, ci + half + 1), np.arange(cj - half, cj + half + 1), indexing="ij")
        ii, jj = ii.ravel(), jj.ravel()
        jj = self._wrap_j(jj)
        ok = self._valid(ii, jj)
        ii, jj = ii[ok], jj[ok]
        dist, az = distance_azimuth(lat, lon, self.lat_of(ii), self.lon_of(jj))
        dist, az = np.atleast_1d(dist), np.atleast_1d(az)
        # tri stable : distance puis index natif (déterminisme en cas d'égalité)
        order = np.lexsort((self.native_index(ii, jj), np.round(dist, 3)))[:n]
        return [(int(ii[k]), int(jj[k]), float(dist[k]), float(az[k])) for k in order]
