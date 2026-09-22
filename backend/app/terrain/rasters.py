"""Couches de terrain du projet : MNT, occupation du sol, rugosité (z0), carte WAsP `.map`.

- MNT : mosaïque Copernicus GLO-30 (tuiles 1°) découpée sur l'emprise du projet, ou GeoTIFF importé.
- Occupation du sol : ESA WorldCover 10 m v200 (tuiles 3°, AWS), convertie en z0 par une table
  éditable (valeurs par défaut de la note d'architecture §5.1).
- Carte `.map` WAsP : lignes de changement de rugosité / courbes de niveau, dans un système de
  coordonnées à préciser à l'import (le format ne le contient pas).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.io import MemoryFile
from rasterio.merge import merge
from rasterio.windows import from_bounds

from app.core.config import get_settings
from app.core.errors import AppError
from app.terrain.dem import _GDAL_ENV, tile_path

WORLDCOVER_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map"
# Classes ESA WorldCover → z0 (m), table éditable par projet
DEFAULT_Z0 = {
    "10": {"label": "Tree cover", "z0": 0.8},
    "20": {"label": "Shrubland", "z0": 0.1},
    "30": {"label": "Grassland", "z0": 0.03},
    "40": {"label": "Cropland", "z0": 0.05},
    "50": {"label": "Built-up", "z0": 1.0},
    "60": {"label": "Bare / sparse vegetation", "z0": 0.005},
    "70": {"label": "Snow and ice", "z0": 0.001},
    "80": {"label": "Permanent water bodies", "z0": 0.0002},
    "90": {"label": "Herbaceous wetland", "z0": 0.03},
    "95": {"label": "Mangroves", "z0": 0.5},
    "100": {"label": "Moss and lichen", "z0": 0.01},
}


def terrain_dir(project_id: int) -> Path:
    d = get_settings().data_dir / "terrain" / f"p{project_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def extent(points: list[tuple[float, float]], margin_km: float) -> tuple[float, float, float, float]:
    if not points:
        raise AppError("TERRAIN_NO_EXTENT", "Add turbines, a mast or a site first")
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    dlat = margin_km / 111.2
    dlon = margin_km / (111.2 * max(math.cos(math.radians(sum(lats) / len(lats))), 0.2))
    return min(lons) - dlon, min(lats) - dlat, max(lons) + dlon, max(lats) + dlat


def _mosaic(paths: list[str], bbox, out: Path, nodata=None, resampling=Resampling.nearest) -> dict:
    srcs = []
    with rasterio.Env(**_GDAL_ENV):
        for p in paths:
            try:
                srcs.append(rasterio.open(p))
            except RasterioIOError:
                continue  # tuile absente (mer)
        if not srcs:
            raise AppError("TERRAIN_NO_TILE", "No raster tile covers this area")
        arr, transform = merge(srcs, bounds=bbox, nodata=nodata, resampling=resampling)
        profile = srcs[0].profile.copy()
        for s in srcs:
            s.close()
    profile.update(
        driver="GTiff",
        height=arr.shape[1],
        width=arr.shape[2],
        transform=transform,
        compress="deflate",
        tiled=True,
        count=1,
        nodata=nodata,
    )
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(arr[0], 1)
    return {
        "width": int(arr.shape[2]),
        "height": int(arr.shape[1]),
        "crs": str(profile["crs"]),
        "res_deg": abs(transform.a),
    }


def download_dem(bbox, out: Path) -> dict:
    lon0, lat0, lon1, lat1 = bbox
    paths = [
        tile_path(la, lo)
        for la in range(math.floor(lat0), math.floor(lat1) + 1)
        for lo in range(math.floor(lon0), math.floor(lon1) + 1)
    ]
    info = _mosaic(paths, bbox, out, nodata=-32767.0)
    with rasterio.open(out) as ds:
        a = ds.read(1, masked=True)
        info |= {"min_m": float(a.min()), "max_m": float(a.max()), "mean_m": round(float(a.mean()), 1)}
    return info


def worldcover_tile(lat_floor3: int, lon_floor3: int) -> str:
    ns = "N" if lat_floor3 >= 0 else "S"
    ew = "E" if lon_floor3 >= 0 else "W"
    name = f"ESA_WorldCover_10m_2021_v200_{ns}{abs(lat_floor3):02d}{ew}{abs(lon_floor3):03d}_Map.tif"
    return f"/vsicurl/{WORLDCOVER_URL}/{name}"


def download_landcover(bbox, out: Path) -> dict:
    lon0, lat0, lon1, lat1 = bbox
    paths = [
        worldcover_tile(la, lo)
        for la in range(3 * math.floor(lat0 / 3), 3 * math.floor(lat1 / 3) + 1, 3)
        for lo in range(3 * math.floor(lon0 / 3), 3 * math.floor(lon1 / 3) + 1, 3)
    ]
    info = _mosaic(paths, bbox, out, nodata=0)
    with rasterio.open(out) as ds:
        a = ds.read(1)
    classes, counts = np.unique(a[a > 0], return_counts=True)
    info["classes_pct"] = {str(int(c)): round(float(n) * 100 / a.size, 2) for c, n in zip(classes, counts, strict=True)}
    return info


def z0_from_landcover(landcover: Path, table: dict, out: Path) -> dict:
    with rasterio.open(landcover) as src:
        lc = src.read(1)
        profile = src.profile.copy()
    z0 = np.full(lc.shape, np.nan, dtype="float32")
    for cls, row in table.items():
        z0[lc == int(cls)] = float(row["z0"])
    unmapped = sorted({int(c) for c in np.unique(lc) if c != 0 and str(int(c)) not in table})
    profile.update(dtype="float32", nodata=np.nan, compress="deflate")
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(z0, 1)
    valid = z0[np.isfinite(z0)]
    return {
        "unmapped_classes": unmapped,
        "z0_log_mean_m": round(float(np.exp(np.log(valid).mean())), 4) if valid.size else None,
    }


def z0_by_sector(z0_path: Path, lat: float, lon: float, sectors: int = 12, radius_km: float = 3.0) -> list[dict]:
    """Rugosité amont par secteur : moyenne géométrique de z0 dans un secteur de rayon 3 km, pondérée
    par 1/distance (proche de l'éolienne ou du mât = plus influent). Approche simple, documentée ;
    le modèle d'écoulement (WAsP Engineering importé) reste la référence."""
    with rasterio.open(z0_path) as ds:
        dlat = radius_km / 111.2
        dlon = radius_km / (111.2 * math.cos(math.radians(lat)))
        win = from_bounds(lon - dlon, lat - dlat, lon + dlon, lat + dlat, ds.transform)
        z0 = ds.read(1, window=win, boundless=True, fill_value=np.nan)
        t = ds.window_transform(win)
    rows, cols = np.indices(z0.shape)
    xs = t.c + (cols + 0.5) * t.a
    ys = t.f + (rows + 0.5) * t.e
    dx = (xs - lon) * 111.2 * math.cos(math.radians(lat))
    dy = (ys - lat) * 111.2
    dist = np.hypot(dx, dy)
    az = (np.degrees(np.arctan2(dx, dy)) + 360.0) % 360.0
    width = 360.0 / sectors
    sec = np.floor(((az + width / 2) % 360.0) / width).astype(int)
    ok = np.isfinite(z0) & (dist > 0.05) & (dist <= radius_km) & (z0 > 0)
    out = []
    for k in range(sectors):
        m = ok & (sec == k)
        if not m.any():
            out.append({"sector_deg": k * width, "z0_m": None})
            continue
        w = 1.0 / dist[m]
        out.append({"sector_deg": k * width, "z0_m": round(float(np.exp((np.log(z0[m]) * w).sum() / w.sum())), 4)})
    return out


# --------------------------------------------------------------------------------------------
# Aperçus PNG (superposition sur la carte)
# --------------------------------------------------------------------------------------------


def _read_preview(path: Path, max_px: int = 1024):
    with rasterio.open(path) as ds:
        scale = max(ds.width, ds.height) / max_px
        h, w = (int(ds.height / scale), int(ds.width / scale)) if scale > 1 else (ds.height, ds.width)
        a = ds.read(
            1,
            out_shape=(h, w),
            resampling=Resampling.average if ds.dtypes[0] != "uint8" else Resampling.mode,
            masked=True,
        )
        b = ds.bounds
        res = (abs(ds.transform.a) * ds.width / w, abs(ds.transform.e) * ds.height / h)
    return a, [b.left, b.bottom, b.right, b.top], res


def _png(rgba: np.ndarray) -> bytes:
    h, w, _ = rgba.shape
    with MemoryFile() as mem:
        with mem.open(driver="PNG", width=w, height=h, count=4, dtype="uint8") as dst:
            for k in range(4):
                dst.write(rgba[:, :, k], k + 1)
        return mem.read()


def hillshade_png(path: Path) -> tuple[bytes, list[float]]:
    a, bounds, res = _read_preview(path)
    z = a.filled(np.nan).astype(float)
    lat = (bounds[1] + bounds[3]) / 2
    dx = res[0] * 111_320 * math.cos(math.radians(lat))
    dy = res[1] * 111_320
    gy, gx = np.gradient(np.nan_to_num(z, nan=np.nanmean(z)), dy, dx)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = math.radians(315), math.radians(45)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    v = (np.clip(shade, 0, 1) * 255).astype("uint8")
    rgba = np.dstack([v, v, v, np.where(np.isfinite(z), 150, 0).astype("uint8")])
    return _png(rgba), bounds


def z0_png(path: Path) -> tuple[bytes, list[float]]:
    a, bounds, _ = _read_preview(path)
    z = a.filled(np.nan).astype(float)
    t = np.clip((np.log10(np.clip(z, 1e-4, 2.0)) + 4) / (math.log10(2.0) + 4), 0, 1)  # 1e-4 … 2 m
    # palette séquentielle : eau (bleu clair) → lisse (sable) → rugueux (vert foncé)
    stops = np.array([[198, 219, 239], [240, 228, 180], [161, 205, 125], [49, 130, 64], [20, 70, 40]], float)
    pos = t * (len(stops) - 1)
    k = np.clip(np.floor(pos).astype(int), 0, len(stops) - 2)
    f = (pos - k)[..., None]
    rgb = (stops[k] * (1 - f) + stops[k + 1] * f).astype("uint8")
    alpha = np.where(np.isfinite(z), 170, 0).astype("uint8")
    return _png(np.dstack([rgb, alpha])), bounds


# --------------------------------------------------------------------------------------------
# Carte WAsP .map
# --------------------------------------------------------------------------------------------


def parse_wasp_map(data: bytes) -> dict:
    """Lignes de rugosité (`z0_gauche z0_droite n`) et courbes de niveau (`h n`) d'un fichier `.map`.

    En-tête : 4 lignes (titre ; points fixes ; échelles/décalages x/y ; échelle/décalage des hauteurs).
    Les lignes combinées (`z0_g z0_d h n`) sont également acceptées.
    """
    text = data.decode("latin-1")
    tokens_lines = [ln.split() for ln in text.splitlines()]
    if len(tokens_lines) < 5:
        raise AppError("TERRAIN_MAP_INVALID", "File too short for a WAsP .map")
    nums: list[float] = []
    for ln in tokens_lines[4:]:
        for tok in ln:
            try:
                nums.append(float(tok.replace(",", ".")))
            except ValueError as exc:
                raise AppError("TERRAIN_MAP_INVALID", "Non numeric value in .map body", value=tok) from exc
    try:
        sx, ox, sy, oy = (float(v) for v in tokens_lines[2][:4])
    except (ValueError, IndexError):
        sx, ox, sy, oy = 1.0, 0.0, 1.0, 0.0
    # Lecture séquentielle des blocs : on reconnaît le type par la ligne d'en-tête de bloc.
    body = [ln for ln in tokens_lines[4:] if ln]
    rough, contours = [], []
    k = 0
    while k < len(body):
        head = [float(v.replace(",", ".")) for v in body[k]]
        k += 1
        if len(head) == 3:
            z0l, z0r, n = head
            h = None
        elif len(head) == 2:
            h, n = head
            z0l = z0r = None
        elif len(head) == 4:
            z0l, z0r, h, n = head
        else:
            raise AppError("TERRAIN_MAP_INVALID", "Unexpected block header", line=" ".join(body[k - 1]))
        n = int(n)
        coords = []
        while len(coords) < 2 * n and k < len(body):
            coords += [float(v.replace(",", ".")) for v in body[k]]
            k += 1
        pts = [[coords[2 * i] * sx + ox, coords[2 * i + 1] * sy + oy] for i in range(n)]
        if z0l is not None:
            rough.append({"z0_left": z0l, "z0_right": z0r, "coords": pts})
        if h is not None:
            contours.append({"height": h, "coords": pts})
    return {"title": " ".join(tokens_lines[0]), "roughness_lines": rough, "contour_lines": contours}


def map_to_geojson(parsed: dict, crs: str) -> dict:
    from pyproj import Transformer

    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    feats = []
    for kind, items in (("roughness", parsed["roughness_lines"]), ("contour", parsed["contour_lines"])):
        for it in items:
            xs, ys = zip(*it["coords"], strict=True) if it["coords"] else ((), ())
            lon, lat = tr.transform(list(xs), list(ys))
            props = {k: v for k, v in it.items() if k != "coords"} | {"kind": kind}
            feats.append(
                {
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "LineString", "coordinates": [[a, b] for a, b in zip(lon, lat, strict=True)]},
                }
            )
    return {"type": "FeatureCollection", "features": feats}


def upload_geotiff(data: bytes, out: Path) -> dict:
    """GeoTIFF importé (MNT) : reprojeté en WGS84 géographique pour un traitement homogène."""
    from rasterio.warp import calculate_default_transform, reproject, transform_bounds

    try:
        with MemoryFile(data) as mem, mem.open() as src:
            if src.crs is None:
                raise AppError("TERRAIN_TIFF_NO_CRS", "GeoTIFF without coordinate system")
            info = {"source_crs": str(src.crs), "width": src.width, "height": src.height}
            dst_crs = "EPSG:4326"
            transform, w, h = calculate_default_transform(src.crs, dst_crs, src.width, src.height, *src.bounds)
            profile = src.profile.copy()
            profile.update(
                driver="GTiff",
                crs=dst_crs,
                transform=transform,
                width=w,
                height=h,
                compress="deflate",
                dtype="float32",
                nodata=-32767.0,
                count=1,
            )
            with rasterio.open(out, "w", **profile) as dst:
                reproject(
                    rasterio.band(src, 1),
                    rasterio.band(dst, 1),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=transform,
                    dst_crs=dst_crs,
                    dst_nodata=-32767.0,
                    resampling=Resampling.bilinear,
                )
            info["bbox"] = list(transform_bounds(src.crs, dst_crs, *src.bounds))
    except RasterioIOError as exc:
        raise AppError("TERRAIN_TIFF_INVALID", "Invalid GeoTIFF") from exc
    with rasterio.open(out) as ds:
        a = ds.read(1, masked=True)
        info |= {
            "crs": "EPSG:4326",
            "res_deg": abs(ds.transform.a),
            "min_m": float(a.min()),
            "max_m": float(a.max()),
            "mean_m": round(float(a.mean()), 1),
        }
    return info
