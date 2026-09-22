"""Conversions vent : composantes U/V ↔ vitesse/direction (convention météorologique « vient de »).

U positif vers l'Est, V positif vers le Nord. Direction : d'où vient le vent, en degrés depuis le
Nord géographique, sens horaire (0 = Nord, 90 = Est). Vitesse nulle → direction indéfinie (NaN).
"""

import numpy as np


def uv_to_speed_dir(u, v):
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    ws = np.hypot(u, v)
    with np.errstate(invalid="ignore"):
        wd = np.mod(270.0 - np.degrees(np.arctan2(v, u)), 360.0)
    wd = np.where(ws > 0, wd, np.nan)
    return ws, wd


def speed_dir_to_uv(ws, wd):
    ws = np.asarray(ws, dtype=float)
    rad = np.radians(np.asarray(wd, dtype=float))
    return -ws * np.sin(rad), -ws * np.cos(rad)


def vector_mean_direction(u, v, axis=None):
    """Direction de la moyenne vectorielle (pour l'agrégation vers un pas plus long)."""
    return uv_to_speed_dir(np.nanmean(u, axis=axis), np.nanmean(v, axis=axis))[1]


def relative_humidity(t_k, td_k):
    """Humidité relative (%) depuis température et point de rosée (Magnus, eau liquide)."""
    t = np.asarray(t_k, dtype=float) - 273.15
    td = np.asarray(td_k, dtype=float) - 273.15
    a, b = 17.625, 243.04
    rh = 100.0 * np.exp(a * td / (b + td) - a * t / (b + t))
    return np.clip(rh, 0.0, 100.0)
