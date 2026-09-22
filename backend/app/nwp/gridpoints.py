"""Identification des points de grille entourant un site (§4.2)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from geoalchemy2.elements import WKTElement
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import GridPoint, Site, SiteGridPoint
from app.geo.geodesy import distance_azimuth
from app.nwp import invariants
from app.nwp.catalog import get_model
from app.nwp.grids.regular import RegularGrid
from app.terrain import dem

ALLOWED_N = (4, 9, 16)


@dataclass
class Candidate:
    native_index: int
    i: int | None
    j: int | None
    lat: float
    lon: float
    distance_m: float
    azimuth_deg: float
    model_elevation_m: float | None
    land_fraction: float | None
    contains_site: bool = False
    cell_half_deg: tuple[float, float] = (0.125, 0.125)


@dataclass
class ModelResult:
    model: str
    status: str  # "ok" | "out_of_domain" | "invariants_missing"
    method: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    message_code: str = ""
    params: dict = field(default_factory=dict)


def _regular_grid(code: str) -> tuple[RegularGrid, invariants.RegularInvariants | None]:
    try:
        inv = invariants.load(code)
        return inv.grid, inv  # type: ignore[return-value]
    except invariants.InvariantsNotReady:
        spec = get_model(code)
        assert spec.regular_grid is not None
        return spec.regular_grid, None


def domain_check(code: str, lat: float, lon: float) -> ModelResult | None:
    """Pour un modèle régional : None si le site est exploitable, sinon un résultat `out_of_domain`."""
    spec = get_model(code)
    if not spec.regional:
        return None
    grid, _ = _regular_grid(code)
    margin = get_settings().icon_eu_margin_cells
    if grid.contains(lat, lon, margin_cells=margin):
        return None
    inside = grid.contains(lat, lon, margin_cells=0)
    return ModelResult(
        model=code,
        status="out_of_domain",
        message_code="MODEL_SITE_IN_BOUNDARY_MARGIN" if inside else "MODEL_SITE_OUT_OF_DOMAIN",
        params={
            "model": spec.name,
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "lat_min": grid.lat_min,
            "lat_max": grid.lat_max,
            "lon_min": grid.lon_min,
            "lon_max": grid.lon_max,
            "margin_cells": margin,
            "margin_km": round(margin * abs(grid.dlat) * 111.2, 1),
        },
    )


def find_candidates(code: str, lat: float, lon: float, n: int = 4, method: str = "bracket") -> ModelResult:
    """Points de grille candidats pour un modèle (sans accès base de données ni MNT)."""
    spec = get_model(code)
    if n not in ALLOWED_N:
        raise ValueError(f"n must be one of {ALLOWED_N}")
    out_of_domain = domain_check(code, lat, lon)
    if out_of_domain:
        return out_of_domain

    if spec.grid_type == "regular":
        grid, inv = _regular_grid(code)
        if method == "bracket":
            ij = grid.bracketing(lat, lon)
            lats = [float(grid.lat_of(i)) for i, _ in ij]
            lons = [float(grid.lon_of(j)) for _, j in ij]
            dist, az = distance_azimuth(lat, lon, lats, lons)
            rows = [(i, j, float(d), float(a)) for (i, j), d, a in zip(ij, dist, az, strict=True)]
            rows.sort(key=lambda r: (round(r[2], 3), grid.native_index(r[0], r[1])))
        else:
            rows = grid.nearest(lat, lon, n)
        half = (abs(grid.dlat) / 2, grid.dlon / 2)
        cands = []
        for i, j, d, a in rows:
            elev, land = inv.lookup(i, j) if inv is not None else (None, None)
            cands.append(
                Candidate(
                    int(grid.native_index(i, j)),
                    i,
                    j,
                    float(grid.lat_of(i)),
                    float(grid.lon_of(j)),
                    d,
                    a,
                    elev,
                    land,
                    cell_half_deg=half,
                )
            )
        res = ModelResult(code, "ok", method=method, candidates=cands)
        if inv is None:
            res.message_code = "INVARIANTS_NOT_READY"
        return res

    # Grille icosaédrique : pas de notion de maille encadrante, on retient les n plus proches
    # cellules (et la cellule contenant le site si les sommets sont connus).
    try:
        inv = invariants.load(code)
    except invariants.InvariantsNotReady:
        return ModelResult(code, "invariants_missing", message_code="INVARIANTS_NOT_READY", params={"model": spec.name})
    assert isinstance(inv, invariants.IconInvariants)
    cands = []
    half_deg = 0.06  # ~ demi-espacement R3B07 (13 km)
    for nb in inv.grid.neighbours(lat, lon, n):
        elev, land = inv.lookup(nb.index)
        cands.append(
            Candidate(
                nb.index,
                None,
                None,
                nb.lat,
                nb.lon,
                nb.distance_m,
                nb.azimuth_deg,
                elev,
                land,
                contains_site=nb.contains_site,
                cell_half_deg=(half_deg, half_deg),
            )
        )
    return ModelResult(code, "ok", method="nearest", candidates=cands)


# --------------------------------------------------------------------------------------------
# Persistance
# --------------------------------------------------------------------------------------------


def _upsert_grid_point(db: Session, code: str, c: Candidate, with_dem: bool) -> GridPoint:
    gp = db.scalar(select(GridPoint).where(GridPoint.model_code == code, GridPoint.native_index == c.native_index))
    if gp is None:
        gp = GridPoint(model_code=code, native_index=c.native_index)
        db.add(gp)
    if gp.lat != c.lat or gp.lon != c.lon:
        # Géométrie relue dans le GRIB : la position fait foi, les valeurs MNT sont recalculées.
        gp.dem_elevation_m = gp.dem_cell_mean_m = None
    gp.i, gp.j, gp.lat, gp.lon = c.i, c.j, c.lat, c.lon
    gp.geom = WKTElement(f"POINT({c.lon} {c.lat})", srid=4326)
    gp.model_elevation_m = c.model_elevation_m
    gp.land_fraction = c.land_fraction
    if with_dem and gp.dem_elevation_m is None:
        gp.dem_elevation_m = dem.sample(round(c.lat, 6), round(c.lon, 6)).elevation_m
        hl, hn = c.cell_half_deg
        gp.dem_cell_mean_m = dem.box_mean(c.lat - hl, c.lat + hl, c.lon - hn, c.lon + hn)
    db.flush()
    return gp


def compute_for_site(
    db: Session,
    site: Site,
    models: list[str],
    n: int = 4,
    method: str = "bracket",
    with_dem: bool = True,
    progress: Callable[[float, str], None] | None = None,
) -> list[ModelResult]:
    progress = progress or (lambda p, m: None)
    if with_dem and site.dem_elevation_m is None:
        site.dem_elevation_m = dem.sample(round(site.lat, 6), round(site.lon, 6)).elevation_m
    results = []
    for k, code in enumerate(models):
        progress(k / max(len(models), 1), code)
        res = find_candidates(code, site.lat, site.lon, n=n, method=method)
        results.append(res)
        previous = {
            link.grid_point_id: link.selected
            for link in db.scalars(
                select(SiteGridPoint).where(SiteGridPoint.site_id == site.id, SiteGridPoint.model_code == code)
            )
        }
        for link in db.scalars(
            select(SiteGridPoint).where(SiteGridPoint.site_id == site.id, SiteGridPoint.model_code == code)
        ):
            db.delete(link)
        db.flush()
        for rank, c in enumerate(res.candidates, start=1):
            gp = _upsert_grid_point(db, code, c, with_dem)
            diff = None
            if gp.model_elevation_m is not None and site.dem_elevation_m is not None:
                diff = gp.model_elevation_m - site.dem_elevation_m
            db.add(
                SiteGridPoint(
                    site_id=site.id,
                    grid_point_id=gp.id,
                    model_code=code,
                    rank=rank,
                    method=res.method,
                    distance_m=c.distance_m,
                    azimuth_deg=c.azimuth_deg,
                    elevation_diff_m=diff,
                    contains_site=c.contains_site,
                    selected=previous.get(gp.id, False),
                )
            )
        db.flush()
    progress(1.0, "done")
    return results


def link_to_dict(link: SiteGridPoint) -> dict:
    gp = link.grid_point
    alert = get_settings().elevation_alert_m
    land = gp.land_fraction
    return {
        "grid_point_id": gp.id,
        "model": link.model_code,
        "native_index": gp.native_index,
        "i": gp.i,
        "j": gp.j,
        "lat": gp.lat,
        "lon": gp.lon,
        "rank": link.rank,
        "method": link.method,
        "distance_m": link.distance_m,
        "azimuth_deg": link.azimuth_deg,
        "model_elevation_m": gp.model_elevation_m,
        "dem_elevation_m": gp.dem_elevation_m,
        "dem_cell_mean_m": gp.dem_cell_mean_m,
        "elevation_diff_m": link.elevation_diff_m,
        "elevation_alert": link.elevation_diff_m is not None and abs(link.elevation_diff_m) > alert,
        "land_fraction": land,
        "is_land": None if land is None else land >= 0.5,
        # Site à terre (altitude MNT > 0) mais point modèle en mer : représentativité douteuse.
        "land_sea_mismatch": land is not None and land < 0.5 and (link.site.dem_elevation_m or 0.0) > 0.0,
        "contains_site": link.contains_site,
        "selected": link.selected,
    }
