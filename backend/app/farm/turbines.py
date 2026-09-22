"""Import et validation des types d'éoliennes : fichier WAsP `.wtg` (XML) ou CSV.

`.wtg` : une ou plusieurs `PerformanceTable` (une par densité de l'air), points `DataPoint`
(`WindSpeed` m/s, `PowerOutput` W, `ThrustCoEfficient`), `StartStopStrategy` (démarrage, arrêt).
CSV : colonnes `ws` (m/s), `power_kw`, `ct` ; les caractéristiques (diamètre, hauteurs, densité de
référence, vitesses de démarrage / arrêt / redémarrage) sont saisies dans le formulaire.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pandas as pd
from defusedxml import ElementTree as ET

from app.core.errors import AppError


@dataclass
class Issue:
    code: str
    message: str
    severity: str = "error"
    params: dict = field(default_factory=dict)


@dataclass
class TurbineSpec:
    name: str
    manufacturer: str = ""
    rotor_d_m: float | None = None
    hub_heights_m: list[float] = field(default_factory=list)
    rho_ref: float = 1.225
    cut_in_ms: float | None = None
    cut_out_ms: float | None = None
    restart_ms: float | None = None
    power_curve: list[dict] = field(default_factory=list)  # [{ws, power_kw, ct}]
    density_curves: list[dict] = field(default_factory=list)
    source_format: str = ""

    @property
    def rated_kw(self) -> float:
        return max((p["power_kw"] for p in self.power_curve), default=0.0)


def _attr(el, *names, default=None):
    """Attribut XML insensible à la casse et aux variantes d'orthographe."""
    low = {k.lower(): v for k, v in el.attrib.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return default


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_wtg(data: bytes, filename: str = "") -> TurbineSpec:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise AppError("TURBINE_WTG_INVALID", f"Invalid .wtg XML: {exc}") from exc
    if _local(root.tag) != "windturbinegenerator":
        raise AppError("TURBINE_WTG_INVALID", "Root element is not WindTurbineGenerator")
    spec = TurbineSpec(
        name=_attr(root, "Description", "Name", default="") or filename.rsplit(".", 1)[0],
        manufacturer=_attr(root, "ManufacturerName", "Manufacturer", default="") or "",
        source_format="wtg",
    )
    d = _attr(root, "RotorDiameter")
    spec.rotor_d_m = float(d) if d else None
    for el in root.iter():
        if _local(el.tag) == "height" and el.text:
            spec.hub_heights_m.append(float(el.text))
    tables = [el for el in root.iter() if _local(el.tag) == "performancetable"]
    if not tables:
        raise AppError("TURBINE_WTG_INVALID", "No PerformanceTable in .wtg")
    for t in tables:
        rho = float(_attr(t, "AirDensity", default="1.225"))
        pts = []
        for p in t.iter():
            if _local(p.tag) != "datapoint":
                continue
            ws = float(_attr(p, "WindSpeed"))
            pw = float(_attr(p, "PowerOutput", default="0")) / 1000.0  # W → kW
            ct = float(_attr(p, "ThrustCoEfficient", "ThrustCoefficient", "Ct", default="nan"))
            pts.append({"ws": ws, "power_kw": pw, "ct": ct})
        strat = next((s for s in t.iter() if _local(s.tag) == "startstopstrategy"), None)
        entry = {"rho": rho, "points": sorted(pts, key=lambda x: x["ws"])}
        if strat is not None:
            entry["cut_in"] = float(_attr(strat, "LowSpeedCutIn", default="nan"))
            entry["cut_out"] = float(_attr(strat, "HighSpeedCutOut", default="nan"))
        spec.density_curves.append(entry)
    spec.density_curves.sort(key=lambda c: c["rho"])
    ref = min(spec.density_curves, key=lambda c: abs(c["rho"] - 1.225))
    spec.rho_ref = ref["rho"]
    spec.power_curve = ref["points"]
    if ref.get("cut_in") == ref.get("cut_in"):  # non NaN
        spec.cut_in_ms = ref.get("cut_in")
    if ref.get("cut_out") == ref.get("cut_out"):
        spec.cut_out_ms = ref.get("cut_out")
    if len(spec.density_curves) == 1:
        spec.density_curves = []
    return spec


def parse_curve_csv(data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    head = text.splitlines()[0] if text else ""
    sep = ";" if head.count(";") > head.count(",") else ("\t" if "\t" in head else ",")
    df = pd.read_csv(io.StringIO(text), sep=sep, decimal="," if sep != "," and "," in text else ".")
    cols = {c.strip().lower(): c for c in df.columns}

    def pick(*names):
        for n in names:
            if n in cols:
                return cols[n]
        return None

    ws, pw, ct = (
        pick("ws", "wind_speed", "v", "vitesse"),
        pick("power_kw", "power", "p", "puissance"),
        pick("ct", "thrust"),
    )
    if ws is None or pw is None:
        raise AppError("TURBINE_CSV_COLUMNS", "CSV needs columns ws and power_kw (and ct)")
    return [
        {"ws": float(r[ws]), "power_kw": float(r[pw]), "ct": float(r[ct]) if ct is not None else float("nan")}
        for _, r in df.sort_values(ws).iterrows()
    ]


def validate(spec: TurbineSpec) -> list[Issue]:
    issues: list[Issue] = []
    pc = spec.power_curve
    if len(pc) < 5:
        issues.append(Issue("TURBINE_CURVE_TOO_SHORT", "Power curve needs at least 5 points", params={"n": len(pc)}))
        return issues
    if spec.rotor_d_m is None or not (10 <= spec.rotor_d_m <= 300):
        issues.append(
            Issue("TURBINE_ROTOR_INVALID", "Rotor diameter missing or out of range", params={"value": spec.rotor_d_m})
        )
    ws = [p["ws"] for p in pc]
    if any(b <= a for a, b in zip(ws, ws[1:], strict=False)):
        issues.append(Issue("TURBINE_WS_NOT_INCREASING", "Wind speeds must be strictly increasing"))
    if any(p["power_kw"] < 0 for p in pc):
        issues.append(Issue("TURBINE_POWER_NEGATIVE", "Negative power in curve"))
    rated = spec.rated_kw
    # décroissance de puissance avant la puissance nominale : suspect (hors bruit numérique)
    peak = next(k for k, p in enumerate(pc) if p["power_kw"] >= 0.999 * rated)
    if any(pc[k + 1]["power_kw"] < pc[k]["power_kw"] - 0.01 * rated for k in range(peak)):
        issues.append(Issue("TURBINE_POWER_NOT_MONOTONIC", "Power decreases below rated speed", "warning"))
    cts = [p["ct"] for p in pc if p["ct"] == p["ct"]]
    if not cts:
        issues.append(Issue("TURBINE_CT_MISSING", "Thrust coefficient (Ct) curve missing: required for wakes"))
    elif any(c < 0 or c > 1.1 for c in cts):
        issues.append(Issue("TURBINE_CT_RANGE", "Ct outside [0, 1.1]", params={"max": max(cts)}))
    if spec.cut_in_ms is None or spec.cut_out_ms is None:
        issues.append(Issue("TURBINE_CUT_SPEEDS_MISSING", "Cut-in / cut-out speeds missing"))
    else:
        if not (0 < spec.cut_in_ms < spec.cut_out_ms <= 40):
            issues.append(Issue("TURBINE_CUT_SPEEDS_INVALID", "Inconsistent cut-in / cut-out speeds"))
        if spec.restart_ms is not None and not (spec.cut_in_ms < spec.restart_ms < spec.cut_out_ms):
            issues.append(Issue("TURBINE_RESTART_INVALID", "Restart speed must be between cut-in and cut-out"))
        if spec.restart_ms is None:
            issues.append(Issue("TURBINE_RESTART_MISSING", "High-wind restart speed (hysteresis) not given", "warning"))
    if not (1.0 <= spec.rho_ref <= 1.3):
        issues.append(
            Issue("TURBINE_RHO_RANGE", "Reference air density outside [1.0, 1.3] kg/m³", params={"value": spec.rho_ref})
        )
    if not spec.hub_heights_m:
        issues.append(Issue("TURBINE_HUB_HEIGHTS_MISSING", "No hub height given", "warning"))
    return issues
