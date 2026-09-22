"""Décodage GRIB (ecCodes) commun aux champs invariants et aux prévisions."""

from __future__ import annotations

import tempfile

import eccodes
import numpy as np

from app.nwp.grids.regular import RegularGrid

# Clés d'identification (GRIB2 : discipline / catégorie / numéro de paramètre, type de niveau).
_EXTRA_KEYS = (
    "discipline",
    "parameterCategory",
    "parameterNumber",
    "typeOfLevel",
    "level",
    "number",
    "stepRange",
    "dataDate",
    "dataTime",
)


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
                    for k in _EXTRA_KEYS:
                        try:
                            msg[k] = eccodes.codes_get(gid, k)
                        except eccodes.KeyValueNotFoundError:
                            msg[k] = None
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


def extract_regular(msg: dict, lats, lons) -> np.ndarray:
    """Valeurs aux nœuds (lat, lon) d'un message regular_ll (grille globale ou sous-domaine).

    Les nœuds demandés doivent être des nœuds de la grille (tolérance 1e-4°) : on n'interpole pas.
    """
    grid, arr = regular_from_message(msg)
    lats, lons = np.asarray(lats, float), np.asarray(lons, float)
    fi = (lats - grid.lat_first) / grid.dlat
    if grid.global_lon:
        fj = ((lons - grid.lon_first) % 360.0) / grid.dlon
    else:
        fj = (((lons - grid.lon_first) + 180.0) % 360.0 - 180.0) / grid.dlon
    i, j = np.rint(fi).astype(int), np.rint(fj).astype(int)
    if grid.global_lon:
        j = np.mod(j, grid.nlon)
    if (np.abs(fi - np.rint(fi)) > 1e-3).any() or (np.abs(fj - np.rint(fj)) > 1e-3).any():
        raise ValueError("requested points are not nodes of this grid")
    if (i < 0).any() or (i >= grid.nlat).any() or (j < 0).any() or (j >= grid.nlon).any():
        raise ValueError("requested points outside the GRIB domain")
    return arr[i, j]
