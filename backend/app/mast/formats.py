"""Lecture des séries du mât : CSV générique, Campbell TOA5, exports texte NRG et Windographer.

Chaque lecteur renvoie un `RawTable` (colonnes d'origine, horodatages naïfs tels qu'écrits dans
le fichier) ; la conversion en UTC et la convention d'horodatage sont appliquées ensuite
(`app.mast.ingest`) selon le choix de l'utilisateur. Formats binaires propriétaires (NRG .rld,
Windographer .windographer) : non pris en charge, utiliser leurs exports texte.

Conventions d'horodatage par défaut :
- Campbell TOA5 : fin d'intervalle ;
- NRG SymphoniePRO (export texte) : début d'intervalle ;
- Windographer (export texte) : début d'intervalle ;
- CSV générique : à préciser (début par défaut).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

import pandas as pd

from app.core.errors import AppError

KINDS = ("ws", "wd", "temp", "rh", "pressure")
STATS = ("mean", "sd", "max", "min")


@dataclass
class RawTable:
    format: str
    frame: pd.DataFrame  # colonnes d'origine (texte), + colonne de temps
    time_column: str
    default_convention: str  # "start" | "end"
    header: dict = field(default_factory=dict)
    units: dict = field(default_factory=dict)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise AppError("IMPORT_ENCODING", "Unreadable file encoding")


def detect_format(text: str) -> str:
    head = text[:4000]
    first = head.splitlines()[0] if head else ""
    if first.startswith('"TOA5"') or first.startswith("TOA5"):
        return "toa5"
    if re.search(r"^Timestamp\t", head, re.M) and (
        "NRG" in head or "SymphoniePRO" in head or "Export Parameters" in head
    ):
        return "nrg"
    if "Windographer" in head or re.search(r"^Date/Time\t", head, re.M):
        return "windographer"
    return "csv"


def read_toa5(text: str) -> RawTable:
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 5:
        raise AppError("MAST_FILE_TOO_SHORT", "TOA5 file without data")
    names, units, procs = rows[1], rows[2], rows[3]
    df = pd.DataFrame(rows[4:], columns=names)
    tcol = "TIMESTAMP" if "TIMESTAMP" in names else names[0]
    header = {"station": rows[0][1] if len(rows[0]) > 1 else "", "logger": rows[0][2] if len(rows[0]) > 2 else ""}
    return RawTable(
        "toa5", df, tcol, "end", header, {n: f"{u} ({p})".strip() for n, u, p in zip(names, units, procs, strict=False)}
    )


def _read_after(text: str, marker: str, fmt: str, convention: str) -> RawTable:
    lines = text.splitlines()
    idx = next((k for k, line in enumerate(lines) if line.startswith(marker)), None)
    if idx is None:
        raise AppError("MAST_HEADER_NOT_FOUND", f"Column header starting with {marker!r} not found")
    header = {}
    for line in lines[:idx]:
        if "=" in line or ":" in line or "\t" in line:
            parts = re.split(r"\t|=|:", line, maxsplit=1)
            if len(parts) == 2 and parts[0].strip():
                header[parts[0].strip()[:80]] = parts[1].strip()[:200]
    df = pd.read_csv(io.StringIO("\n".join(lines[idx:])), sep="\t", dtype=str)
    return RawTable(fmt, df, df.columns[0], convention, header)


def read_generic(text: str) -> RawTable:
    sample = text[:8000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        sep = dialect.delimiter
    except csv.Error:
        sep = ","
    # lignes d'en-tête libres éventuelles : on cherche la première ligne ayant le nombre de colonnes majoritaire
    lines = text.splitlines()
    counts = [line.count(sep) for line in lines[:200] if line.strip()]
    target = max(set(counts), key=counts.count) if counts else 0
    start = next((k for k, line in enumerate(lines) if line.count(sep) == target), 0)
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])), sep=sep, dtype=str)
    tcol = guess_time_column(df)
    if tcol is None:
        raise AppError("MAST_TIME_COLUMN_NOT_FOUND", "No date/time column found")
    return RawTable("csv", df, tcol, "start", {"skipped_header_lines": start})


def guess_time_column(df: pd.DataFrame) -> str | None:
    for c in df.columns[:5]:
        sample = df[c].dropna().astype(str).head(20)
        if sample.empty:
            continue
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=_dayfirst(sample))
        if parsed.notna().mean() > 0.9:
            return c
    return None


def _dayfirst(sample: pd.Series) -> bool:
    """dd/mm/yyyy si un premier champ > 12 apparaît (convention française)."""
    for s in sample:
        m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}", s)
        if m and int(m.group(1)) > 12:
            return True
    return False


def read_file(filename: str, data: bytes) -> RawTable:
    lower = filename.lower()
    if lower.endswith((".rld", ".rwd", ".windographer", ".ndf")):
        raise AppError("MAST_BINARY_FORMAT", "Proprietary binary format: export to text first", filename=filename)
    text = _decode(data)
    fmt = detect_format(text)
    if fmt == "toa5":
        return read_toa5(text)
    if fmt == "nrg":
        return _read_after(text, "Timestamp\t", "nrg", "start")
    if fmt == "windographer":
        return _read_after(text, "Date/Time\t", "windographer", "start")
    return read_generic(text)


# --------------------------------------------------------------------------------------------
# Suggestion de correspondance colonnes → capteurs
# --------------------------------------------------------------------------------------------

_NRG = re.compile(
    r"^Ch\d+_(?P<kind>Anem|Vane|Temp|RH|Baro|BP|Analog)_(?P<h>\d+(?:\.\d+)?)m(?:_(?P<boom>[NSEW]{1,3}))?_"
    r"(?P<stat>Avg|SD|Max|Min)",
    re.I,
)
_KIND_WORDS = [
    ("ws", r"(?:ws|spd|speed|anem|anemo|vitesse|wspd|v)"),
    ("wd", r"(?:wd|dir|vane|direction|girouette|wdir)"),
    ("temp", r"(?:temp|tair|air_?t|t)"),
    ("rh", r"(?:rh|hum|humid)"),
    ("pressure", r"(?:press|baro|bp|pres|p)"),
]
_STAT_WORDS = [
    ("sd", r"(?:sd|std|stdev|sigma|ecart)"),
    ("max", r"(?:max|gust)"),
    ("min", r"(?:min)"),
    ("mean", r"(?:avg|mean|moy|ave)"),
]
_BOOM = {
    "N": 0.0,
    "NE": 45.0,
    "E": 90.0,
    "SE": 135.0,
    "S": 180.0,
    "SW": 225.0,
    "W": 270.0,
    "NW": 315.0,
    "NNE": 22.5,
    "ENE": 67.5,
    "ESE": 112.5,
    "SSE": 157.5,
    "SSW": 202.5,
    "WSW": 247.5,
    "WNW": 292.5,
    "NNW": 337.5,
}


def suggest_mapping(columns: list[str], time_column: str) -> list[dict]:
    out = []
    for c in columns:
        if c == time_column or c.upper() in ("RECORD", "REC"):
            continue
        m = _NRG.match(c)
        if m:
            kind = {"anem": "ws", "vane": "wd", "temp": "temp", "rh": "rh", "baro": "pressure", "bp": "pressure"}.get(
                m.group("kind").lower()
            )
            if kind is None:
                continue
            stat = {"avg": "mean", "sd": "sd", "max": "max", "min": "min"}[m.group("stat").lower()]
            boom = _BOOM.get((m.group("boom") or "").upper())
            out.append({"column": c, "kind": kind, "stat": stat, "height_m": float(m.group("h")), "boom_dir_deg": boom})
            continue
        low = c.lower()
        # jetons alphabétiques / numériques : « WS80N_avg » → ws, 80, n, avg
        tokens = re.findall(r"[a-z]+|\d+(?:[.,]\d+)?", low)
        words = [t for t in tokens if t.isalpha()]
        kind = next((k for k, pat in _KIND_WORDS if any(re.fullmatch(pat, w) for w in words)), None)
        if kind is None:
            continue
        stat = next((st for st, pat in _STAT_WORDS if any(re.fullmatch(pat, w) for w in words)), "mean")
        nums = [float(t.replace(",", ".")) for t in tokens if not t.isalpha()]
        height = next((v for v in nums if 1 <= v <= 250), None)
        boom = next((_BOOM[w.upper()] for w in words if w.upper() in _BOOM), None)
        out.append({"column": c, "kind": kind, "stat": stat, "height_m": height, "boom_dir_deg": boom})
    return out
