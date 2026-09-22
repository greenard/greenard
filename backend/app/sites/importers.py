"""Import de sites multiples : CSV et KML (§4.1).

CSV : séparateur `,` ou `;` détecté, en-têtes insensibles à la casse. Colonnes :
- `name` (obligatoire) ;
- soit `lat` + `lon` (décimal ou DMS, WGS84), soit `x` + `y` + `crs` (ex. `EPSG:32629`, `UTM29N`,
  `Lambert Nord`). Une colonne `crs` vide vaut WGS84 si `lat`/`lon` sont fournis.

Chaque ligne est validée ; les erreurs sont renvoyées ligne par ligne avec un code stable.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from defusedxml import ElementTree as ET

from app.core.errors import AppError
from app.geo.crs import CoordinateError, parse_angle, to_wgs84

MAX_SITES = 500

_ALIASES = {
    "name": {"name", "nom", "id", "site"},
    "lat": {"lat", "latitude"},
    "lon": {"lon", "lng", "long", "longitude"},
    "x": {"x", "easting", "est"},
    "y": {"y", "northing", "nord"},
    "crs": {"crs", "epsg", "srs", "systeme", "système"},
}


@dataclass
class ParsedSite:
    row: int
    name: str
    lat: float
    lon: float
    input_crs: str
    input_x: float | None
    input_y: float | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class RowError:
    row: int
    field: str
    code: str
    message: str
    severity: str = "error"
    params: dict = field(default_factory=dict)


@dataclass
class ImportResult:
    sites: list[ParsedSite]
    errors: list[RowError]

    @property
    def ok(self) -> bool:
        return not any(e.severity == "error" for e in self.errors)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise AppError("IMPORT_ENCODING", "Unreadable file encoding")


def _num(text: str) -> float:
    return float(text.strip().replace(" ", "").replace(",", "."))


def parse_csv(data: bytes) -> ImportResult:
    text = _decode(data)
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        raise AppError("IMPORT_EMPTY", "Empty file")
    header = [h.strip().lower() for h in rows[0]]
    cols: dict[str, int] = {}
    for key, names in _ALIASES.items():
        for k, h in enumerate(header):
            if h in names:
                cols[key] = k
                break
    errors: list[RowError] = []
    if "name" not in cols:
        errors.append(RowError(1, "name", "IMPORT_MISSING_COLUMN", "Missing column 'name'", params={"column": "name"}))
    has_ll = "lat" in cols and "lon" in cols
    has_xy = "x" in cols and "y" in cols
    if not (has_ll or has_xy):
        errors.append(RowError(1, "lat/lon|x/y", "IMPORT_MISSING_COORD_COLUMNS", "Need lat+lon or x+y+crs columns"))
    if errors:
        return ImportResult([], errors)
    if len(rows) - 1 > MAX_SITES:
        return ImportResult([], [RowError(0, "", "IMPORT_TOO_MANY_ROWS", "Too many rows", params={"max": MAX_SITES})])

    sites: list[ParsedSite] = []
    seen: set[str] = set()
    for r, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue

        def cell(key: str, row=row) -> str:
            k = cols.get(key)
            return row[k].strip() if k is not None and k < len(row) else ""

        name = cell("name")
        if not name:
            errors.append(RowError(r, "name", "IMPORT_EMPTY_NAME", "Empty name"))
            continue
        if name in seen:
            errors.append(RowError(r, "name", "IMPORT_DUPLICATE_NAME", "Duplicate name", params={"name": name}))
            continue
        try:
            crs = cell("crs")
            if cell("lat") and cell("lon") and (not crs or crs.upper() in ("WGS84", "EPSG:4326", "4326")):
                lat, lon = parse_angle(cell("lat"), "lat"), parse_angle(cell("lon"), "lon")
                site = ParsedSite(r, name, lat, lon, "EPSG:4326", None, None)
            elif cell("x") and cell("y"):
                if not crs:
                    raise CoordinateError("IMPORT_MISSING_CRS", "x/y given without crs", field="crs")
                try:
                    x, y = _num(cell("x")), _num(cell("y"))
                except ValueError as exc:
                    raise CoordinateError("COORD_UNPARSABLE", "x/y not numeric", field="x/y") from exc
                lat, lon, info = to_wgs84(x, y, crs)
                site = ParsedSite(r, name, lat, lon, crs, x, y, warnings=list(info.warnings))
            else:
                raise CoordinateError("IMPORT_MISSING_COORD", "No coordinates on this row", field="lat/lon")
        except CoordinateError as exc:
            errors.append(RowError(r, str(exc.params.get("field", "")), exc.code, exc.message, params=exc.params))
            continue
        for w in site.warnings:
            errors.append(RowError(r, "crs", w, w, severity="warning"))
        seen.add(name)
        sites.append(site)
    return ImportResult(sites, errors)


_KML_NS = "{http://www.opengis.net/kml/2.2}"


def parse_kml(data: bytes) -> ImportResult:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise AppError("IMPORT_KML_INVALID", f"Invalid KML: {exc}") from exc
    ns = _KML_NS if root.tag.startswith(_KML_NS) else ""
    sites: list[ParsedSite] = []
    errors: list[RowError] = []
    seen: set[str] = set()
    for k, pm in enumerate(root.iter(f"{ns}Placemark"), start=1):
        name_el = pm.find(f"{ns}name")
        name = (name_el.text or "").strip() if name_el is not None else f"site_{k}"
        coords_el = pm.find(f".//{ns}Point/{ns}coordinates")
        if coords_el is None or not (coords_el.text or "").strip():
            errors.append(
                RowError(
                    k,
                    "Point",
                    "IMPORT_KML_NOT_POINT",
                    "Placemark without Point",
                    severity="warning",
                    params={"name": name},
                )
            )
            continue
        try:
            parts = coords_el.text.strip().split(",")
            lon, lat = parse_angle(float(parts[0]), "lon"), parse_angle(float(parts[1]), "lat")
        except (ValueError, IndexError):
            errors.append(RowError(k, "coordinates", "COORD_UNPARSABLE", "Bad coordinates", params={"name": name}))
            continue
        except CoordinateError as exc:
            errors.append(RowError(k, "coordinates", exc.code, exc.message, params=exc.params))
            continue
        if name in seen:
            errors.append(RowError(k, "name", "IMPORT_DUPLICATE_NAME", "Duplicate name", params={"name": name}))
            continue
        seen.add(name)
        sites.append(ParsedSite(k, name, lat, lon, "EPSG:4326", None, None))
    if not sites and not errors:
        errors.append(RowError(0, "", "IMPORT_EMPTY", "No Placemark found"))
    if len(sites) > MAX_SITES:
        return ImportResult([], [RowError(0, "", "IMPORT_TOO_MANY_ROWS", "Too many rows", params={"max": MAX_SITES})])
    return ImportResult(sites, errors)


def parse_file(filename: str, data: bytes) -> ImportResult:
    lower = filename.lower()
    if lower.endswith(".kml"):
        return parse_kml(data)
    if lower.endswith((".csv", ".txt")):
        return parse_csv(data)
    raise AppError("IMPORT_UNSUPPORTED_FORMAT", "Unsupported file format", filename=filename)
