"""Registre des variables canoniques des extraits de prévision.

Stockage brut en unités SI (m/s, K, Pa, %) ; conversion à l'export (°C, hPa).
Noms : `u_100m`, `v_100m`, `u_850hPa`, `gust_10m`, `t_2m`, `d_2m`, `rh_2m`, `t_850hPa`, `sp`, `msl`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

WIND_HEIGHT_RE = re.compile(r"^([uv])_(\d+)m$")
PRESSURE_RE = re.compile(r"^([uvt])_(\d+)hPa$")


@dataclass(frozen=True)
class VarInfo:
    name: str
    units: str
    long_name: str
    standard_name: str = ""


def info(name: str) -> VarInfo:
    m = WIND_HEIGHT_RE.match(name)
    if m:
        comp, h = m.groups()
        return VarInfo(
            name,
            "m s-1",
            f"{'eastward' if comp == 'u' else 'northward'} wind at {h} m",
            f"{'eastward' if comp == 'u' else 'northward'}_wind",
        )
    m = PRESSURE_RE.match(name)
    if m:
        comp, p = m.groups()
        if comp == "t":
            return VarInfo(name, "K", f"air temperature at {p} hPa", "air_temperature")
        return VarInfo(
            name,
            "m s-1",
            f"{'eastward' if comp == 'u' else 'northward'} wind at {p} hPa",
            f"{'eastward' if comp == 'u' else 'northward'}_wind",
        )
    table = {
        "gust_10m": VarInfo("gust_10m", "m s-1", "wind speed of gust at 10 m", "wind_speed_of_gust"),
        "t_2m": VarInfo("t_2m", "K", "air temperature at 2 m", "air_temperature"),
        "d_2m": VarInfo("d_2m", "K", "dew point temperature at 2 m", "dew_point_temperature"),
        "rh_2m": VarInfo("rh_2m", "%", "relative humidity at 2 m", "relative_humidity"),
        "sp": VarInfo("sp", "Pa", "surface air pressure", "surface_air_pressure"),
        "msl": VarInfo("msl", "Pa", "air pressure at mean sea level", "air_pressure_at_mean_sea_level"),
    }
    return table[name]


def wind_heights(names) -> list[int]:
    return sorted({int(m.group(2)) for n in names if (m := WIND_HEIGHT_RE.match(n))})


def wind_levels_hpa(names) -> list[int]:
    return sorted({int(m.group(2)) for n in names if (m := PRESSURE_RE.match(n)) and m.group(1) in "uv"})


@dataclass(frozen=True)
class VariableRequest:
    """Ce que l'utilisateur demande : hauteurs de vent, niveaux de pression, variables de surface."""

    wind_heights_m: tuple[int, ...] = (10, 100)
    pressure_levels_hpa: tuple[int, ...] = ()
    surface: tuple[str, ...] = ("gust_10m", "t_2m", "rh_2m", "sp", "msl")

    def canonical(self) -> list[str]:
        out = []
        for h in self.wind_heights_m:
            out += [f"u_{h}m", f"v_{h}m"]
        for p in self.pressure_levels_hpa:
            out += [f"u_{p}hPa", f"v_{p}hPa", f"t_{p}hPa"]
        return out + list(self.surface)
