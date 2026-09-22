"""Import de layout de parc : CSV, XLSX, KML, Shapefile (zip).

Champs : identifiant, X/Y + système de coordonnées (ou lat/lon WGS84), type d'éolienne, hauteur
de moyeu. Les erreurs sont renvoyées ligne par ligne ; rien n'est enregistré s'il en reste une.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from defusedxml import ElementTree as ET
from pyproj import CRS, Transformer

from app.core.errors import AppError
from app.geo.crs import CoordinateError, normalize_crs, parse_angle, to_wgs84
from app.geo.geodesy import distance_azimuth

MAX_TURBINES = 500
ALIASES = {
    "label": {"id", "label", "name", "nom", "wtg", "turbine", "eolienne", "éolienne"},
    "x": {"x", "easting", "est"},
    "y": {"y", "northing", "nord"},
    "lat": {"lat", "latitude"},
    "lon": {"lon", "lng", "longitude"},
    "crs": {"crs", "epsg", "srs"},
    "type": {"type", "model", "modele", "modèle", "turbine_type", "wtg_type"},
    "hub": {"hub_height", "hub", "hh", "hauteur_moyeu", "hub_height_m", "hauteur"},
}


@dataclass
class LayoutRow:
    row: int
    label: str
    lat: float
    lon: float
    input_crs: str
    x: float | None
    y: float | None
    type_name: str
    hub_height_m: float | None


@dataclass
class LayoutIssue:
    row: int
    field: str
    code: str
    message: str
    severity: str = "error"
    params: dict = field(default_factory=dict)


@dataclass
class LayoutResult:
    rows: list[LayoutRow]
    issues: list[LayoutIssue]

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)


def _columns(columns) -> dict[str, str]:
    out = {}
    for key, names in ALIASES.items():
        for c in columns:
            if str(c).strip().lower() in names:
                out[key] = c
                break
    return out


def _num(v) -> float | None:
    if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == "":
        return None
    return float(str(v).strip().replace(" ", "").replace(",", "."))


def _from_frame(df: pd.DataFrame, default_crs: str | None) -> LayoutResult:
    cols = _columns(df.columns)
    issues: list[LayoutIssue] = []
    if "label" not in cols:
        issues.append(LayoutIssue(1, "id", "LAYOUT_MISSING_COLUMN", "Missing column id", params={"column": "id"}))
    if not ({"x", "y"} <= cols.keys() or {"lat", "lon"} <= cols.keys()):
        issues.append(LayoutIssue(1, "x/y", "IMPORT_MISSING_COORD_COLUMNS", "Need x+y (+crs) or lat+lon"))
    if "type" not in cols:
        issues.append(LayoutIssue(1, "type", "LAYOUT_MISSING_COLUMN", "Missing column type", params={"column": "type"}))
    if issues:
        return LayoutResult([], issues)
    if len(df) > MAX_TURBINES:
        return LayoutResult(
            [], [LayoutIssue(0, "", "IMPORT_TOO_MANY_ROWS", "Too many rows", params={"max": MAX_TURBINES})]
        )
    rows: list[LayoutRow] = []
    for k, rec in enumerate(df.to_dict("records"), start=2):
        label = str(rec.get(cols["label"], "") or "").strip()
        if label in ("", "nan"):
            issues.append(LayoutIssue(k, "id", "IMPORT_EMPTY_NAME", "Empty id"))
            continue
        try:
            crs_cell = str(rec.get(cols.get("crs"), "") or "").strip() if "crs" in cols else ""
            crs_cell = "" if crs_cell == "nan" else crs_cell
            if "x" in cols and _num(rec.get(cols["x"])) is not None:
                crs = crs_cell or default_crs
                if not crs:
                    raise CoordinateError("IMPORT_MISSING_CRS", "x/y given without crs", field="crs")
                x, y = _num(rec[cols["x"]]), _num(rec[cols["y"]])
                if y is None:
                    raise CoordinateError("IMPORT_MISSING_COORD", "Missing y", field="y")
                lat, lon, info = to_wgs84(x, y, crs)
                crs_code = normalize_crs(crs)
                for w in info.warnings:
                    issues.append(LayoutIssue(k, "crs", w, w, "warning"))
            elif "lat" in cols:
                lat, lon = parse_angle(rec[cols["lat"]], "lat"), parse_angle(rec[cols["lon"]], "lon")
                x = y = None
                crs_code = "EPSG:4326"
            else:
                raise CoordinateError("IMPORT_MISSING_COORD", "No coordinates on this row", field="x/y")
        except (CoordinateError, ValueError) as exc:
            code = exc.code if isinstance(exc, CoordinateError) else "COORD_UNPARSABLE"
            params = exc.params if isinstance(exc, CoordinateError) else {}
            issues.append(LayoutIssue(k, str(params.get("field", "x/y")), code, str(exc), params=params))
            continue
        type_name = str(rec.get(cols["type"], "") or "").strip()
        hub = None
        if "hub" in cols:
            try:
                hub = _num(rec.get(cols["hub"]))
            except ValueError:
                issues.append(LayoutIssue(k, "hub_height", "LAYOUT_HUB_INVALID", "Hub height not numeric"))
                continue
        rows.append(LayoutRow(k, label, lat, lon, crs_code, x, y, type_name, hub))
    return LayoutResult(rows, issues)


def parse_kml(data: bytes) -> LayoutResult:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise AppError("IMPORT_KML_INVALID", f"Invalid KML: {exc}") from exc
    recs = []
    for pm in root.iter():
        if not pm.tag.endswith("Placemark"):
            continue
        rec = {"id": None, "lat": None, "lon": None}
        for el in pm.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "name" and rec["id"] is None:
                rec["id"] = (el.text or "").strip()
            elif tag == "coordinates" and el.text:
                parts = el.text.strip().split(",")
                rec["lon"], rec["lat"] = parts[0], parts[1]
            elif tag in ("Data", "SimpleData"):
                name = el.attrib.get("name", "")
                val = el.text if tag == "SimpleData" else next((c.text for c in el if c.tag.endswith("value")), None)
                rec[name] = val
        if rec["lat"] is not None:
            recs.append(rec)
    return _from_frame(pd.DataFrame(recs), "EPSG:4326")


def parse_shapefile_zip(data: bytes) -> LayoutResult:
    from osgeo import ogr, osr  # GDAL (conda-forge)

    with tempfile.TemporaryDirectory() as d:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    name = Path(info.filename).name  # pas de chemins arbitraires (zip slip)
                    if name and not info.is_dir():
                        (Path(d) / name).write_bytes(z.read(info))
        except zipfile.BadZipFile as exc:
            raise AppError("LAYOUT_SHP_INVALID", "Invalid zip archive") from exc
        shp = next(Path(d).glob("*.shp"), None)
        if shp is None:
            raise AppError("LAYOUT_SHP_INVALID", "No .shp file in the archive")
        ds = ogr.Open(str(shp))
        if ds is None:
            raise AppError("LAYOUT_SHP_INVALID", "Unreadable shapefile")
        layer = ds.GetLayer(0)
        srs = layer.GetSpatialRef()
        if srs is None:
            raise AppError("LAYOUT_SHP_NO_PRJ", "Shapefile without .prj: coordinate system unknown")
        srs.AutoIdentifyEPSG()
        epsg = srs.GetAuthorityCode(None)
        crs = CRS.from_epsg(int(epsg)) if epsg else CRS.from_wkt(srs.ExportToWkt())
        tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        recs = []
        for feat in layer:
            geom = feat.GetGeometryRef()
            if geom is None or geom.GetGeometryName() != "POINT":
                continue
            x, y = geom.GetX(), geom.GetY()
            lon, lat = tr.transform(x, y)
            rec = {k: feat.GetField(k) for k in feat.keys()}
            rec |= {"lat": lat, "lon": lon, "_x": x, "_y": y}
            recs.append(rec)
        del ds
        _ = osr
    result = _from_frame(
        pd.DataFrame(recs).drop(columns=[c for c in ("x", "y") if c in pd.DataFrame(recs)]), "EPSG:4326"
    )
    code = f"EPSG:{epsg}" if epsg else crs.to_string()
    for r, rec in zip(result.rows, recs, strict=False):
        r.input_crs, r.x, r.y = code, rec["_x"], rec["_y"]
    return result


def parse_file(filename: str, data: bytes, default_crs: str | None) -> LayoutResult:
    lower = filename.lower()
    if lower.endswith((".csv", ".txt")):
        text = data.decode("utf-8-sig", errors="replace")
        sep = ";" if text.splitlines()[0].count(";") > text.splitlines()[0].count(",") else ","
        return _from_frame(pd.read_csv(io.StringIO(text), sep=sep, dtype=str), default_crs)
    if lower.endswith((".xlsx", ".xls")):
        return _from_frame(pd.read_excel(io.BytesIO(data), dtype=str), default_crs)
    if lower.endswith(".kml"):
        return parse_kml(data)
    if lower.endswith(".zip"):
        return parse_shapefile_zip(data)
    raise AppError("IMPORT_UNSUPPORTED_FORMAT", "Unsupported format (CSV, XLSX, KML or zipped Shapefile)")


def check_layout(result: LayoutResult, types: dict[str, dict]) -> None:
    """Contrôles métier : identifiants uniques, type connu, hauteur de moyeu, espacement."""
    seen: set[str] = set()
    for r in result.rows:
        if r.label in seen:
            result.issues.append(
                LayoutIssue(r.row, "id", "IMPORT_DUPLICATE_NAME", "Duplicate id", params={"name": r.label})
            )
        seen.add(r.label)
        t = types.get(r.type_name.lower())
        if t is None:
            result.issues.append(
                LayoutIssue(r.row, "type", "LAYOUT_TYPE_UNKNOWN", "Unknown turbine type", params={"type": r.type_name})
            )
            continue
        if r.hub_height_m is None:
            if t["hub_heights_m"]:
                r.hub_height_m = float(t["hub_heights_m"][0])
                result.issues.append(
                    LayoutIssue(
                        r.row,
                        "hub_height",
                        "LAYOUT_HUB_DEFAULTED",
                        "Hub height from type",
                        "warning",
                        params={"value": r.hub_height_m},
                    )
                )
            else:
                result.issues.append(LayoutIssue(r.row, "hub_height", "LAYOUT_HUB_MISSING", "Hub height missing"))
        elif not 20 <= r.hub_height_m <= 250:
            result.issues.append(
                LayoutIssue(
                    r.row,
                    "hub_height",
                    "LAYOUT_HUB_INVALID",
                    "Hub height out of range",
                    params={"value": r.hub_height_m},
                )
            )
    # espacement minimal (en diamètres de rotor)
    rows = [r for r in result.rows if r.type_name.lower() in types]
    if len(rows) >= 2:
        lat = np.array([r.lat for r in rows])
        lon = np.array([r.lon for r in rows])
        for a in range(len(rows)):
            d, _ = distance_azimuth(lat[a], lon[a], np.delete(lat, a), np.delete(lon, a))
            k = int(np.argmin(d))
            other = [r for n, r in enumerate(rows) if n != a][k]
            dmax = max(types[rows[a].type_name.lower()]["rotor_d_m"], types[other.type_name.lower()]["rotor_d_m"])
            ratio = float(d[k]) / dmax
            if ratio < 1.0:
                result.issues.append(
                    LayoutIssue(
                        rows[a].row,
                        "x/y",
                        "LAYOUT_OVERLAP",
                        "Turbines closer than 1 D",
                        params={"other": other.label, "distance_m": round(float(d[k]), 1), "ratio_d": round(ratio, 2)},
                    )
                )
            elif ratio < 2.0:
                result.issues.append(
                    LayoutIssue(
                        rows[a].row,
                        "x/y",
                        "LAYOUT_SPACING_LOW",
                        "Spacing below 2 D",
                        "warning",
                        params={"other": other.label, "distance_m": round(float(d[k]), 1), "ratio_d": round(ratio, 2)},
                    )
                )


def spacing_stats(lats, lons, rotor_d) -> dict:
    lat, lon = np.asarray(lats), np.asarray(lons)
    if lat.size < 2:
        return {}
    mins = []
    for a in range(lat.size):
        d, _ = distance_azimuth(lat[a], lon[a], np.delete(lat, a), np.delete(lon, a))
        mins.append(float(np.min(d)))
    mins = np.array(mins)
    return {
        "min_m": round(float(mins.min()), 1),
        "median_nearest_m": round(float(np.median(mins)), 1),
        "min_d": round(float(mins.min() / rotor_d), 2) if rotor_d else None,
    }
