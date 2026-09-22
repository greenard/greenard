"""Systèmes de coordonnées : WGS84 (décimal / DMS), UTM (zone automatique), Lambert Merchich.

Toutes les coordonnées géographiques internes sont en WGS84 (EPSG:4326), ordre (lat, lon) dans
les signatures publiques, ordre (x=lon, y=lat) pour pyproj (`always_xy=True`).

Transformation Merchich ↔ WGS84 : PROJ propose « Merchich to WGS 84 (1) » (3 paramètres, précision
nominale 7 m) sur une partie du territoire ; ailleurs (zones Sahara), seule une transformation
« ballpark » sans changement de datum est disponible, avec des erreurs pouvant atteindre plusieurs
centaines de mètres. Nous ne choisissons jamais silencieusement une transformation ballpark : le
résultat porte alors l'avertissement `MERCHICH_BALLPARK`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

from pyproj import CRS, Transformer
from pyproj.transformer import TransformerGroup

from app.core.errors import AppError

WGS84 = "EPSG:4326"

# Zones Lambert du Maroc (datum Merchich). EPSG:26193 est déprécié : lecture seule.
MERCHICH_ZONES: dict[str, dict] = {
    "EPSG:26191": {"key": "nord_maroc", "deprecated": False},
    "EPSG:26192": {"key": "sud_maroc", "deprecated": False},
    "EPSG:26194": {"key": "sahara_nord", "deprecated": False},
    "EPSG:26195": {"key": "sahara_sud", "deprecated": False},
    "EPSG:26193": {"key": "sahara", "deprecated": True},
}


class CoordinateError(AppError):
    pass


# --------------------------------------------------------------------------------------------
# Angles : décimal et DMS
# --------------------------------------------------------------------------------------------

_HEMI = {"N": 1, "S": -1, "E": 1, "W": -1, "O": -1}  # "O" = Ouest (saisie française)
_DMS_RE = re.compile(
    r"""^\s*
    (?P<h1>[NSEWO])?\s*
    (?P<sign>[-+])?\s*
    (?P<d>\d+(?:[.,]\d+)?)\s*(?:°|º|d|deg)?\s*
    (?:(?P<m>\d+(?:[.,]\d+)?)\s*(?:'|′|’|m|min)?\s*)?
    (?:(?P<s>\d+(?:[.,]\d+)?)\s*(?:"|″|”|''|s|sec)?\s*)?
    (?P<h2>[NSEWO])?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def parse_angle(text: str | float | int, kind: str) -> float:
    """Convertit une latitude (`kind="lat"`) ou longitude (`kind="lon"`) en degrés décimaux.

    Formats acceptés : `-7.6`, `-7,6`, `33°35'12.3"N`, `33 35 12.3 N`, `N33°35.2'`, `7°36'W`,
    `7d36m0s O`. La lettre d'hémisphère et le signe sont exclusifs.
    """
    if isinstance(text, int | float):
        value = float(text)
    else:
        raw = text.strip()
        if not raw:
            raise CoordinateError("COORD_EMPTY", "Empty coordinate", field=kind)
        m = _DMS_RE.match(raw)
        if not m:
            raise CoordinateError("COORD_UNPARSABLE", f"Cannot parse coordinate: {raw!r}", field=kind, value=raw)
        h1, h2 = m.group("h1"), m.group("h2")
        if h1 and h2:
            raise CoordinateError("COORD_UNPARSABLE", f"Two hemisphere letters: {raw!r}", field=kind, value=raw)
        hemi = (h1 or h2 or "").upper()
        if hemi and m.group("sign"):
            raise CoordinateError(
                "COORD_SIGN_AND_HEMISPHERE", f"Both sign and hemisphere given: {raw!r}", field=kind, value=raw
            )
        if hemi:
            valid = "NS" if kind == "lat" else "EWO"
            if hemi not in valid:
                raise CoordinateError(
                    "COORD_WRONG_HEMISPHERE", f"Hemisphere {hemi} invalid for {kind}", field=kind, value=raw
                )

        def num(g: str) -> float | None:
            v = m.group(g)
            return None if v is None else float(v.replace(",", "."))

        d, mi, s = num("d"), num("m"), num("s")
        if (mi is not None and mi >= 60) or (s is not None and s >= 60):
            raise CoordinateError("COORD_MINUTES_SECONDS_RANGE", "Minutes/seconds must be < 60", field=kind, value=raw)
        if (mi is not None or s is not None) and d is not None and d != int(d):
            raise CoordinateError("COORD_UNPARSABLE", "Fractional degrees with minutes", field=kind, value=raw)
        value = (d or 0.0) + (mi or 0.0) / 60.0 + (s or 0.0) / 3600.0
        if m.group("sign") == "-":
            value = -value
        if hemi:
            value *= _HEMI[hemi]

    limit = 90.0 if kind == "lat" else 180.0
    if not math.isfinite(value) or abs(value) > limit:
        raise CoordinateError("COORD_OUT_OF_RANGE", f"{kind} out of range: {value}", field=kind, value=value)
    return value


def format_dms(value: float, kind: str, decimals: int = 2) -> str:
    """Formate en DMS, ex. `33°35'12.30"N`."""
    hemi = ("N" if value >= 0 else "S") if kind == "lat" else ("E" if value >= 0 else "W")
    total = round(abs(value) * 3600.0, decimals)
    d = int(total // 3600)
    mi = int((total - d * 3600) // 60)
    s = total - d * 3600 - mi * 60
    return f"{d}°{mi:02d}'{s:0{3 + decimals}.{decimals}f}\"{hemi}"


# --------------------------------------------------------------------------------------------
# UTM
# --------------------------------------------------------------------------------------------


def utm_zone(lat: float, lon: float) -> tuple[int, str]:
    """Zone UTM et hémisphère ('N'/'S') pour un point WGS84 (exceptions Norvège/Svalbard incluses)."""
    lon_n = ((lon + 180.0) % 360.0) - 180.0
    zone = int(math.floor((lon_n + 180.0) / 6.0)) + 1
    zone = min(zone, 60)
    if 56.0 <= lat < 64.0 and 3.0 <= lon_n < 12.0:
        zone = 32
    if 72.0 <= lat < 84.0:
        if 0.0 <= lon_n < 9.0:
            zone = 31
        elif 9.0 <= lon_n < 21.0:
            zone = 33
        elif 21.0 <= lon_n < 33.0:
            zone = 35
        elif 33.0 <= lon_n < 42.0:
            zone = 37
    return zone, ("N" if lat >= 0 else "S")


def utm_epsg(zone: int, hemisphere: str) -> str:
    if not 1 <= zone <= 60:
        raise CoordinateError("UTM_ZONE_INVALID", f"Invalid UTM zone {zone}", zone=zone)
    hemisphere = hemisphere.upper()
    if hemisphere not in ("N", "S"):
        raise CoordinateError("UTM_HEMISPHERE_INVALID", f"Invalid hemisphere {hemisphere}", hemisphere=hemisphere)
    return f"EPSG:{(32600 if hemisphere == 'N' else 32700) + zone}"


_UTM_ALIAS = re.compile(r"^\s*UTM\s*(\d{1,2})\s*([NS])\s*$", re.IGNORECASE)
_EPSG_ALIAS = re.compile(r"^\s*(?:EPSG\s*:\s*)?(\d{4,6})\s*$", re.IGNORECASE)
_NAMED = {
    "WGS84": WGS84,
    "WGS 84": WGS84,
    "LAMBERT NORD": "EPSG:26191",
    "LAMBERT SUD": "EPSG:26192",
    "LAMBERT SAHARA NORD": "EPSG:26194",
    "LAMBERT SAHARA SUD": "EPSG:26195",
}


def normalize_crs(text: str) -> str:
    """`UTM29N`, `utm 29 n`, `32629`, `EPSG:26191`, `WGS84`, `Lambert Nord` → `EPSG:xxxx`."""
    t = text.strip()
    if t.upper() in _NAMED:
        return _NAMED[t.upper()]
    m = _UTM_ALIAS.match(t)
    if m:
        return utm_epsg(int(m.group(1)), m.group(2))
    m = _EPSG_ALIAS.match(t)
    if m:
        code = f"EPSG:{m.group(1)}"
        _crs(code)  # validation
        return code
    raise CoordinateError("CRS_UNKNOWN", f"Unknown CRS: {text!r}", value=text)


@lru_cache(maxsize=64)
def _crs(code: str) -> CRS:
    try:
        return CRS.from_user_input(code)
    except Exception as exc:  # pyproj.exceptions.CRSError
        raise CoordinateError("CRS_UNKNOWN", f"Unknown CRS: {code!r}", value=code) from exc


# --------------------------------------------------------------------------------------------
# Transformations
# --------------------------------------------------------------------------------------------


@dataclass
class TransformInfo:
    description: str
    accuracy_m: float | None  # None = inconnue
    ballpark: bool
    warnings: list[str] = field(default_factory=list)


@lru_cache(maxsize=64)
def _group(src: str, dst: str) -> TransformerGroup:
    return TransformerGroup(src, dst, always_xy=True)


def _in_area(t: Transformer, lon: float, lat: float) -> bool:
    area = t.area_of_use
    if area is None:
        return True
    w, s, e, n = area.bounds
    return s <= lat <= n and (w <= lon <= e if w <= e else (lon >= w or lon <= e))


def _pick(src: str, dst: str, lon: float, lat: float) -> tuple[Transformer, TransformInfo]:
    """Choisit la meilleure transformation dont l'emprise contient le point (lon/lat WGS84)."""
    group = _group(src, dst)
    candidates = [t for t in group.transformers if _in_area(t, lon, lat)] or list(group.transformers)
    non_ballpark = [t for t in candidates if "ballpark" not in t.description.lower()]
    t = (non_ballpark or candidates)[0]
    ballpark = "ballpark" in t.description.lower()
    acc = t.accuracy if t.accuracy is not None and t.accuracy >= 0 else None
    warnings = []
    if ballpark and ("Merchich" in _crs(src).name or "Merchich" in _crs(dst).name):
        warnings.append("MERCHICH_BALLPARK")
    return t, TransformInfo(t.description, acc, ballpark, warnings)


def to_wgs84(x: float, y: float, crs: str) -> tuple[float, float, TransformInfo]:
    """(x, y) dans `crs` → (lat, lon) WGS84."""
    crs = normalize_crs(crs)
    if crs == WGS84:
        lat, lon = parse_angle(y, "lat"), parse_angle(x, "lon")
        return lat, lon, TransformInfo("identity", 0.0, False)
    # Première passe pour obtenir une position approchée, puis choix de la transformation adaptée.
    approx = Transformer.from_crs(crs, WGS84, always_xy=True)
    lon0, lat0 = approx.transform(x, y)
    if not (math.isfinite(lon0) and math.isfinite(lat0)):
        raise CoordinateError("COORD_TRANSFORM_FAILED", "Transformation failed", crs=crs, x=x, y=y)
    t, info = _pick(crs, WGS84, lon0, lat0)
    lon, lat = t.transform(x, y)
    if not (math.isfinite(lon) and math.isfinite(lat)) or abs(lat) > 90:
        raise CoordinateError("COORD_TRANSFORM_FAILED", "Transformation failed", crs=crs, x=x, y=y)
    _check_area(crs, lat, lon, info)
    return lat, lon, info


def from_wgs84(lat: float, lon: float, crs: str) -> tuple[float, float, TransformInfo]:
    """(lat, lon) WGS84 → (x, y) dans `crs`."""
    crs = normalize_crs(crs)
    if crs == WGS84:
        return lon, lat, TransformInfo("identity", 0.0, False)
    t, info = _pick(WGS84, crs, lon, lat)
    x, y = t.transform(lon, lat)
    if not (math.isfinite(x) and math.isfinite(y)):
        raise CoordinateError("COORD_TRANSFORM_FAILED", "Transformation failed", crs=crs, lat=lat, lon=lon)
    _check_area(crs, lat, lon, info)
    return x, y, info


def _check_area(crs: str, lat: float, lon: float, info: TransformInfo) -> None:
    area = _crs(crs).area_of_use
    if area is None:
        return
    w, s, e, n = area.bounds
    tol = 0.5  # degrés : tolérance aux bords de zone
    if not (s - tol <= lat <= n + tol and w - tol <= lon <= e + tol):
        info.warnings.append("CRS_OUTSIDE_AREA_OF_USE")


# --------------------------------------------------------------------------------------------
# Représentations simultanées (affichage permanent dans l'interface)
# --------------------------------------------------------------------------------------------


def lambert_zones_for(lat: float, lon: float) -> list[str]:
    out = []
    for code, meta in MERCHICH_ZONES.items():
        if meta["deprecated"]:
            continue
        w, s, e, n = _crs(code).area_of_use.bounds
        if s <= lat <= n and w <= lon <= e:
            out.append(code)
    return out


def representations(lat: float, lon: float) -> dict:
    lat = parse_angle(lat, "lat")
    lon = parse_angle(lon, "lon")
    zone, hemi = utm_zone(lat, lon)
    epsg = utm_epsg(zone, hemi)
    ux, uy, uinfo = from_wgs84(lat, lon, epsg)
    lamberts = []
    for code in lambert_zones_for(lat, lon):
        x, y, info = from_wgs84(lat, lon, code)
        lamberts.append(
            {
                "crs": code,
                "name": _crs(code).name,
                "key": MERCHICH_ZONES[code]["key"],
                "x": x,
                "y": y,
                "transformation": info.description,
                "accuracy_m": info.accuracy_m,
                "warnings": info.warnings,
            }
        )
    return {
        "wgs84": {"lat": lat, "lon": lon},
        "dms": {"lat": format_dms(lat, "lat"), "lon": format_dms(lon, "lon")},
        "utm": {"zone": zone, "hemisphere": hemi, "crs": epsg, "x": ux, "y": uy, "warnings": uinfo.warnings},
        "lambert": lamberts,
    }


def crs_catalog() -> list[dict]:
    """CRS proposés à la saisie."""
    items = [{"crs": WGS84, "name": "WGS 84", "kind": "geographic", "deprecated": False}]
    for zone in (28, 29, 30):
        code = utm_epsg(zone, "N")
        items.append({"crs": code, "name": _crs(code).name, "kind": "utm", "deprecated": False})
    for code, meta in MERCHICH_ZONES.items():
        items.append(
            {"crs": code, "name": _crs(code).name, "kind": "lambert_merchich", "deprecated": meta["deprecated"]}
        )
    return items
