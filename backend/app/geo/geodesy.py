"""Distances et azimuts géodésiques sur l'ellipsoïde WGS84 (méthode de Karney via pyproj.Geod)."""

import numpy as np
from pyproj import Geod

_GEOD = Geod(ellps="WGS84")


def distance_azimuth(lat1, lon1, lat2, lon2):
    """Distance (m) et azimut (°, 0–360, depuis le Nord, sens horaire) du point 1 vers le point 2.

    Accepte scalaires ou tableaux (diffusion numpy).
    """
    lat1, lon1, lat2, lon2 = np.broadcast_arrays(*(np.asarray(v, dtype=float) for v in (lat1, lon1, lat2, lon2)))
    az, _, dist = _GEOD.inv(lon1, lat1, lon2, lat2)
    az = np.mod(az, 360.0)
    if np.ndim(dist) == 0:
        return float(dist), float(az)
    return np.asarray(dist), np.asarray(az)
