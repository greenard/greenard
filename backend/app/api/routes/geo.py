from fastapi import APIRouter, Depends

from app.api.deps import current_user
from app.api.schemas import ConvertIn
from app.core.errors import AppError
from app.db.models import User
from app.geo import crs as crs_mod

router = APIRouter(prefix="/geo", tags=["geo"])


def resolve_input(lat, lon, x, y, crs: str) -> tuple[float, float, dict]:
    """Coordonnées saisies → (lat, lon) WGS84 + informations de transformation."""
    code = crs_mod.normalize_crs(crs)
    if code == crs_mod.WGS84:
        if lat is None or lon is None:
            raise AppError("COORD_MISSING", "lat and lon are required for WGS84")
        return crs_mod.parse_angle(lat, "lat"), crs_mod.parse_angle(lon, "lon"), {"crs": code, "warnings": []}
    if x is None or y is None:
        raise AppError("COORD_MISSING", "x and y are required for projected CRS", crs=code)
    la, lo, info = crs_mod.to_wgs84(x, y, code)
    return (
        la,
        lo,
        {"crs": code, "transformation": info.description, "accuracy_m": info.accuracy_m, "warnings": info.warnings},
    )


@router.get("/crs")
def list_crs(_: User = Depends(current_user)) -> list[dict]:
    return crs_mod.crs_catalog()


@router.post("/convert")
def convert(body: ConvertIn, _: User = Depends(current_user)) -> dict:
    lat, lon, info = resolve_input(body.lat, body.lon, body.x, body.y, body.crs)
    return {"input": info, **crs_mod.representations(lat, lon)}


@router.get("/utm-zone")
def utm_zone(lat: float, lon: float, _: User = Depends(current_user)) -> dict:
    zone, hemi = crs_mod.utm_zone(lat, lon)
    return {"zone": zone, "hemisphere": hemi, "crs": crs_mod.utm_epsg(zone, hemi)}
