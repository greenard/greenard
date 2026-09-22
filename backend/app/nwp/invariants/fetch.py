"""Téléchargement et décodage des champs invariants depuis les sources ouvertes.

- GFS : AWS `noaa-gfs-bdp-pds`, fichier f000 0,25°, requêtes par plages d'octets via `.idx`.
- IFS : AWS `ecmwf-forecasts` (miroir open data), step 0, plages d'octets via `.index` (JSON).
- ICON / ICON-EU : DWD opendata, dossiers des champs « time-invariant » (fichiers .grib2.bz2).
"""

from __future__ import annotations

import bz2
import json
import logging
import re
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import eccodes
import httpx
import numpy as np

from app.core.config import get_settings
from app.core.errors import AppError
from app.nwp.grids.icosahedral import IcosahedralGrid
from app.nwp.grids.regular import RegularGrid
from app.nwp.invariants import (
    IconInvariants,
    _source_code,
    cache_path,
    crop_regular,
    write_meta,
)

log = logging.getLogger(__name__)

GFS_BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
IFS_BUCKET = "https://ecmwf-forecasts.s3.amazonaws.com"
DWD_BASE = "https://opendata.dwd.de/weather/nwp"
G0 = 9.80665

Progress = Callable[[float, str], None]


class SourceUnavailable(AppError):
    status_code = 502


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0), follow_redirects=True, trust_env=True)


def _get(client: httpx.Client, url: str, headers: dict | None = None) -> httpx.Response:
    try:
        r = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise SourceUnavailable("SOURCE_UNREACHABLE", f"Cannot reach {url}: {exc}", url=url) from exc
    return r


# --------------------------------------------------------------------------------------------
# Décodage GRIB
# --------------------------------------------------------------------------------------------


def decode_grib(data: bytes) -> list[dict]:
    """Décode tous les messages GRIB d'un tampon mémoire."""
    out = []
    with tempfile.NamedTemporaryFile(suffix=".grib2") as tmp:
        tmp.write(data)
        tmp.flush()
        with open(tmp.name, "rb") as fh:
            while True:
                gid = eccodes.codes_grib_new_from_file(fh)
                if gid is None:
                    break
                try:
                    msg = {
                        "shortName": eccodes.codes_get(gid, "shortName"),
                        "gridType": eccodes.codes_get(gid, "gridType"),
                        "values": eccodes.codes_get_values(gid).astype(float),
                    }
                    if eccodes.codes_get(gid, "bitmapPresent"):
                        miss = eccodes.codes_get(gid, "missingValue")
                        msg["values"][msg["values"] == miss] = np.nan
                    if msg["gridType"] == "regular_ll":
                        for k in ("Ni", "Nj", "jScansPositively", "iScansNegatively"):
                            msg[k] = eccodes.codes_get(gid, k)
                        for k in (
                            "latitudeOfFirstGridPointInDegrees",
                            "longitudeOfFirstGridPointInDegrees",
                            "iDirectionIncrementInDegrees",
                            "jDirectionIncrementInDegrees",
                        ):
                            msg[k] = eccodes.codes_get_double(gid, k)
                    out.append(msg)
                finally:
                    eccodes.codes_release(gid)
    return out


def regular_from_message(msg: dict) -> tuple[RegularGrid, np.ndarray]:
    """Géométrie et champ 2D (ordre du fichier) d'un message regular_ll."""
    if msg["gridType"] != "regular_ll":
        raise ValueError(f"Unexpected grid type {msg['gridType']}")
    if msg["iScansNegatively"]:
        raise ValueError("iScansNegatively=1 is not supported")
    ni, nj = int(msg["Ni"]), int(msg["Nj"])
    dlon = float(msg["iDirectionIncrementInDegrees"])
    dlat = float(msg["jDirectionIncrementInDegrees"]) * (1 if msg["jScansPositively"] else -1)
    grid = RegularGrid(
        lat_first=float(msg["latitudeOfFirstGridPointInDegrees"]),
        dlat=dlat,
        nlat=nj,
        lon_first=float(msg["longitudeOfFirstGridPointInDegrees"]),
        dlon=dlon,
        nlon=ni,
        global_lon=abs(ni * dlon - 360.0) < 1e-6,
    )
    return grid, np.asarray(msg["values"]).reshape(nj, ni)


# --------------------------------------------------------------------------------------------
# Plages d'octets
# --------------------------------------------------------------------------------------------


def parse_wgrib_idx(text: str) -> list[tuple[int, str, str]]:
    """Index NCEP (.idx) → [(offset, VAR, LEVEL)]."""
    rows = []
    for line in text.strip().splitlines():
        parts = line.split(":")
        if len(parts) >= 5:
            rows.append((int(parts[1]), parts[3], parts[4]))
    return rows


def wgrib_ranges(rows: list[tuple[int, str, str]], wanted: list[tuple[str, str]]) -> list[tuple[int, int | None]]:
    ranges = []
    for var, level in wanted:
        for k, (off, v, lev) in enumerate(rows):
            if v == var and lev == level:
                end = rows[k + 1][0] - 1 if k + 1 < len(rows) else None
                ranges.append((off, end))
                break
        else:
            raise SourceUnavailable("SOURCE_FIELD_MISSING", f"{var}:{level} not in index", field=f"{var}:{level}")
    return ranges


def _fetch_ranges(client: httpx.Client, url: str, ranges: list[tuple[int, int | None]]) -> bytes:
    chunks = []
    for start, end in ranges:
        rng = f"bytes={start}-{'' if end is None else end}"
        r = _get(client, url, headers={"Range": rng})
        if r.status_code not in (200, 206):
            raise SourceUnavailable(
                "SOURCE_HTTP_ERROR", f"HTTP {r.status_code} on {url}", url=url, status=r.status_code
            )
        chunks.append(r.content)
    return b"".join(chunks)


def _recent_cycles(hours: tuple[int, ...], days: int = 3):
    now = datetime.now(UTC)
    for d in range(days):
        day = (now - timedelta(days=d)).date()
        for h in sorted(hours, reverse=True):
            t = datetime(day.year, day.month, day.day, h, tzinfo=UTC)
            if t <= now - timedelta(hours=4):
                yield t


# --------------------------------------------------------------------------------------------
# Fournisseurs
# --------------------------------------------------------------------------------------------


def _save_regular(code: str, grid: RegularGrid, elev: np.ndarray, land: np.ndarray, **meta) -> dict:
    inv = crop_regular(grid, elev, land, get_settings().invariants_bbox)
    inv.save(cache_path(code))
    write_meta(
        code,
        grid={
            "lat_first": grid.lat_first,
            "dlat": grid.dlat,
            "nlat": grid.nlat,
            "lon_first": grid.lon_first,
            "dlon": grid.dlon,
            "nlon": grid.nlon,
        },
        **meta,
    )
    return meta


def fetch_gfs(progress: Progress) -> dict:
    with _client() as client:
        for t in _recent_cycles((0, 6, 12, 18)):
            base = f"{GFS_BUCKET}/gfs.{t:%Y%m%d}/{t:%H}/atmos/gfs.t{t:%H}z.pgrb2.0p25.f000"
            r = _get(client, base + ".idx")
            if r.status_code != 200:
                continue
            progress(0.3, f"GFS {t:%Y-%m-%d %H}Z")
            ranges = wgrib_ranges(parse_wgrib_idx(r.text), [("HGT", "surface"), ("LAND", "surface")])
            msgs = {m["shortName"]: m for m in decode_grib(_fetch_ranges(client, base, ranges))}
            progress(0.8, "decoded")
            grid, elev = regular_from_message(msgs["orog"] if "orog" in msgs else msgs["gh"])
            _, land = regular_from_message(msgs["lsm"] if "lsm" in msgs else msgs["land"])
            return _save_regular(
                "gfs", grid, elev, land, source=base, run=t.isoformat(), fields="HGT:surface, LAND:surface"
            )
    raise SourceUnavailable("SOURCE_NO_RECENT_RUN", "No recent GFS cycle found on AWS", model="gfs")


def fetch_ifs(progress: Progress) -> dict:
    with _client() as client:
        for t in _recent_cycles((0, 12)):
            stem = f"{IFS_BUCKET}/{t:%Y%m%d}/{t:%H}z/ifs/0p25/oper/{t:%Y%m%d%H}0000-0h-oper-fc"
            r = _get(client, stem + ".index")
            if r.status_code != 200:
                continue
            progress(0.3, f"IFS {t:%Y-%m-%d %H}Z")
            entries = [json.loads(line) for line in r.text.splitlines() if line.strip()]
            ranges = []
            for param in ("z", "lsm"):
                e = next((e for e in entries if e.get("param") == param and e.get("levtype") == "sfc"), None)
                if e is None:
                    raise SourceUnavailable("SOURCE_FIELD_MISSING", f"{param} not in IFS index", field=param)
                ranges.append((int(e["_offset"]), int(e["_offset"]) + int(e["_length"]) - 1))
            msgs = {m["shortName"]: m for m in decode_grib(_fetch_ranges(client, stem + ".grib2", ranges))}
            progress(0.8, "decoded")
            grid, z = regular_from_message(msgs["z"])
            _, lsm = regular_from_message(msgs["lsm"])
            return _save_regular("ifs", grid, z / G0, lsm, source=stem, run=t.isoformat(), fields="z (sfc)/g0, lsm")
    raise SourceUnavailable("SOURCE_NO_RECENT_RUN", "No recent IFS run found on AWS", model="ifs")


_DWD_FILE_RE = r'href="({prefix}[^"]*_{var}\.grib2\.bz2)"'


def _dwd_invariant(client: httpx.Client, model_dir: str, prefix: str, var: str) -> tuple[str, bytes]:
    folder = f"{DWD_BASE}/{model_dir}/grib/00/{var.lower()}/"
    r = _get(client, folder)
    if r.status_code != 200:
        raise SourceUnavailable(
            "SOURCE_HTTP_ERROR", f"HTTP {r.status_code} on {folder}", url=folder, status=r.status_code
        )
    names = sorted(set(re.findall(_DWD_FILE_RE.format(prefix=re.escape(prefix), var=var), r.text)))
    if not names:
        raise SourceUnavailable("SOURCE_FIELD_MISSING", f"{var} not found in {folder}", field=var)
    url = folder + names[-1]
    f = _get(client, url)
    if f.status_code != 200:
        raise SourceUnavailable("SOURCE_HTTP_ERROR", f"HTTP {f.status_code} on {url}", url=url, status=f.status_code)
    return url, bz2.decompress(f.content)


def _as_radians(values: np.ndarray) -> np.ndarray:
    """CLAT/CLON : degrés dans les GRIB DWD, radians dans les fichiers de grille NetCDF."""
    return np.radians(values) if np.nanmax(np.abs(values)) > 2 * np.pi + 1e-6 else values


def fetch_icon(progress: Progress) -> dict:
    fields: dict[str, np.ndarray] = {}
    urls = []
    with _client() as client:
        for k, var in enumerate(("CLAT", "CLON", "HSURF", "FR_LAND")):
            progress(0.1 + 0.2 * k, f"ICON {var}")
            url, raw = _dwd_invariant(client, "icon", "icon_global_icosahedral_time-invariant_", var)
            fields[var] = decode_grib(raw)[0]["values"]
            urls.append(url)
    clat, clon = _as_radians(fields["CLAT"]), _as_radians(fields["CLON"])
    if np.nanmax(np.abs(fields["CLAT"])) <= 2 * np.pi + 1e-6 and np.nanmax(np.abs(fields["CLON"])) > 2 * np.pi + 1e-6:
        raise AppError("ICON_GRID_UNITS", "Inconsistent CLAT/CLON units")
    grid = IcosahedralGrid(clat, clon)
    vertices = load_icon_vertices(grid.ncells)
    if vertices is not None:
        grid = IcosahedralGrid(clat, clon, *vertices)
    inv = IconInvariants(grid, fields["HSURF"], np.clip(fields["FR_LAND"], 0.0, 1.0))
    inv.save(cache_path("icon"))
    meta = {
        "source": urls,
        "ncells": grid.ncells,
        "containing_cell": vertices is not None,
        "fields": "CLAT, CLON, HSURF, FR_LAND",
    }
    write_meta("icon", **meta)
    return meta


def load_icon_vertices(ncells: int):
    """Sommets des triangles depuis le fichier de grille DWD complet, s'il est fourni.

    Le fichier `icon_grid_0026_R03B07_G.nc` (https://opendata.dwd.de/weather/lib/cdo/) est
    volumineux ; il est facultatif (`GREENARD_ICON_GRID_FILE`). Sans lui, la cellule
    contenant le site n'est pas déterminée et seuls les plus proches centres sont listés.
    """
    path = getattr(get_settings(), "icon_grid_file", None)
    if not path:
        return None
    import netCDF4

    with netCDF4.Dataset(path) as nc:
        voc = np.asarray(nc["vertex_of_cell"][:])
        vlat, vlon = np.asarray(nc["vlat"][:]), np.asarray(nc["vlon"][:])
        if voc.shape[-1] != ncells and voc.shape[0] != ncells:
            raise AppError("ICON_GRID_MISMATCH", "Grid file does not match the ICON fields", ncells=ncells)
    return voc, vlat, vlon


def fetch_icon_eu(progress: Progress) -> dict:
    with _client() as client:
        progress(0.2, "ICON-EU HSURF")
        u1, raw_h = _dwd_invariant(client, "icon-eu", "icon-eu_europe_regular-lat-lon_time-invariant_", "HSURF")
        progress(0.6, "ICON-EU FR_LAND")
        u2, raw_l = _dwd_invariant(client, "icon-eu", "icon-eu_europe_regular-lat-lon_time-invariant_", "FR_LAND")
    grid, elev = regular_from_message(decode_grib(raw_h)[0])
    _, land = regular_from_message(decode_grib(raw_l)[0])
    return _save_regular("icon_eu", grid, elev, np.clip(land, 0.0, 1.0), source=[u1, u2], fields="HSURF, FR_LAND")


FETCHERS: dict[str, Callable[[Progress], dict]] = {
    "gfs": fetch_gfs,
    "ifs": fetch_ifs,
    "icon": fetch_icon,
    "icon_eu": fetch_icon_eu,
}


def prepare(model_code: str, progress: Progress | None = None) -> dict:
    code = _source_code(model_code)
    progress = progress or (lambda p, m: None)
    progress(0.0, f"prepare {code}")
    meta = FETCHERS[code](progress)
    progress(1.0, "done")
    return meta
