"""Grille icosaédrique non structurée d'ICON (DWD).

Aucune hypothèse de régularité : les cellules sont repérées par leur index (base 0) et leur
centre (`clat`, `clon`, en radians dans les fichiers DWD). La recherche des plus proches voisins
se fait par KD-tree sur les vecteurs unitaires 3D ; la distance de corde c correspond à l'angle
au centre θ = 2·arcsin(c/2), mais les distances publiées sont recalculées sur l'ellipsoïde.

Si les sommets des triangles sont disponibles (fichier de grille complet `icon_grid_*.nc` :
`vertex_of_cell`, `vlat`, `vlon`), on détermine aussi la cellule qui contient le site — c'est
elle qui porte la valeur du modèle au site, et ce n'est pas toujours le centre le plus proche.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from app.geo.geodesy import distance_azimuth

EARTH_RADIUS_M = 6_371_000.0


def to_xyz(lat_rad, lon_rad) -> np.ndarray:
    lat_rad, lon_rad = np.asarray(lat_rad, dtype=float), np.asarray(lon_rad, dtype=float)
    c = np.cos(lat_rad)
    return np.stack([c * np.cos(lon_rad), c * np.sin(lon_rad), np.sin(lat_rad)], axis=-1)


def chord_to_angle(chord):
    return 2.0 * np.arcsin(np.clip(np.asarray(chord) / 2.0, 0.0, 1.0))


@dataclass
class Neighbour:
    index: int
    lat: float
    lon: float
    distance_m: float
    azimuth_deg: float
    contains_site: bool


class IcosahedralGrid:
    def __init__(
        self,
        clat_rad: np.ndarray,
        clon_rad: np.ndarray,
        vertex_of_cell: np.ndarray | None = None,
        vlat_rad: np.ndarray | None = None,
        vlon_rad: np.ndarray | None = None,
    ):
        self.clat = np.asarray(clat_rad, dtype=float).ravel()
        self.clon = np.asarray(clon_rad, dtype=float).ravel()
        if self.clat.shape != self.clon.shape:
            raise ValueError("clat and clon must have the same size")
        self.ncells = self.clat.size
        self._tree = cKDTree(to_xyz(self.clat, self.clon))
        self.vertices_xyz: np.ndarray | None = None
        if vertex_of_cell is not None and vlat_rad is not None and vlon_rad is not None:
            voc = np.asarray(vertex_of_cell)
            if voc.shape[0] == 3 and voc.shape[1] == self.ncells:
                voc = voc.T
            if voc.min() >= 1:  # indices Fortran (base 1) dans les fichiers DWD
                voc = voc - 1
            vxyz = to_xyz(np.asarray(vlat_rad).ravel(), np.asarray(vlon_rad).ravel())
            self.vertices_xyz = vxyz[voc]  # (ncells, 3, 3)

    # ---- persistance ------------------------------------------------------------------------
    def save(self, path: Path) -> None:
        extra = {}
        if self.vertices_xyz is not None:
            extra["vertices_xyz"] = self.vertices_xyz.astype(np.float32)
        np.savez(path, clat=self.clat, clon=self.clon, **extra)

    @classmethod
    def load(cls, path: Path) -> IcosahedralGrid:
        with np.load(path) as z:
            grid = cls(z["clat"], z["clon"])
            if "vertices_xyz" in z:
                grid.vertices_xyz = z["vertices_xyz"].astype(float)
        return grid

    # ---- recherche --------------------------------------------------------------------------
    def lat_lon_deg(self, idx) -> tuple[np.ndarray, np.ndarray]:
        idx = np.asarray(idx)
        return np.degrees(self.clat[idx]), ((np.degrees(self.clon[idx]) + 180.0) % 360.0) - 180.0

    def nearest_indices(self, lat_deg: float, lon_deg: float, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Indices des k centres les plus proches et angle au centre (rad), triés."""
        p = to_xyz(np.radians(lat_deg), np.radians(lon_deg))
        chord, idx = self._tree.query(p, k=k)
        return np.atleast_1d(idx), chord_to_angle(np.atleast_1d(chord))

    def containing_cell(self, lat_deg: float, lon_deg: float) -> int | None:
        """Index de la cellule triangulaire contenant le point, si les sommets sont connus."""
        if self.vertices_xyz is None:
            return None
        p = to_xyz(np.radians(lat_deg), np.radians(lon_deg))
        idx, _ = self.nearest_indices(lat_deg, lon_deg, k=min(12, self.ncells))
        for c in idx:
            if point_in_spherical_triangle(p, *self.vertices_xyz[c]):
                return int(c)
        return None

    def neighbours(self, lat_deg: float, lon_deg: float, n: int) -> list[Neighbour]:
        idx, _ = self.nearest_indices(lat_deg, lon_deg, k=n)
        container = self.containing_cell(lat_deg, lon_deg)
        if container is not None and container not in idx:
            idx = np.append(idx, container)
        lats, lons = self.lat_lon_deg(idx)
        dist, az = distance_azimuth(lat_deg, lon_deg, lats, lons)
        dist, az = np.atleast_1d(dist), np.atleast_1d(az)
        order = np.lexsort((idx, np.round(dist, 3)))
        return [
            Neighbour(
                int(idx[k]),
                float(lats[k]),
                float(lons[k]),
                float(dist[k]),
                float(az[k]),
                container is not None and int(idx[k]) == container,
            )
            for k in order
        ]


def point_in_spherical_triangle(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray, eps: float = 1e-12) -> bool:
    """Test d'appartenance à un triangle sphérique (indépendant de l'orientation des sommets)."""
    if np.dot(p, a + b + c) <= 0:  # hémisphère opposé
        return False
    s1 = np.dot(np.cross(a, b), p)
    s2 = np.dot(np.cross(b, c), p)
    s3 = np.dot(np.cross(c, a), p)
    return bool((s1 >= -eps and s2 >= -eps and s3 >= -eps) or (s1 <= eps and s2 <= eps and s3 <= eps))
