"""Données d'exemple SYNTHÉTIQUES : type d'éolienne générique (courbe de puissance / Ct plausibles,
pas celle d'un constructeur) et layout près d'Essaouira."""

import numpy as np


def generic_curve(rated_kw=4200.0, d=136.0, cut_in=3.0, rated_ws=11.5, cut_out=25.0):
    ws = np.arange(cut_in, cut_out + 0.01, 0.5)
    p = np.where(ws < rated_ws, rated_kw * ((ws**3 - cut_in**3) / (rated_ws**3 - cut_in**3)), rated_kw)
    ct = np.where(ws < rated_ws, 0.80, 0.80 * (rated_ws / ws) ** 3)
    return [
        {"ws": float(w), "power_kw": round(float(x), 1), "ct": round(float(c), 3)}
        for w, x, c in zip(ws, p, ct, strict=True)
    ]


def wtg_xml(name="GEN-4.2-136 (synthetic)", d=136.0, hubs=(112,), densities=(1.225, 1.15)):
    tables = []
    for rho in densities:
        pts = "".join(
            f'<DataPoint WindSpeed="{p["ws"]}" PowerOutput="{p["power_kw"] * 1000 * (rho / 1.225) ** (0 if p["power_kw"] >= 4199 else 1):.1f}" ThrustCoEfficient="{p["ct"]}"/>'
            for p in generic_curve(d=d)
        )
        tables.append(
            f'<PerformanceTable AirDensity="{rho}" StationaryThrustCoefficient="0.12">'
            f'<StartStopStrategy LowSpeedCutIn="3" HighSpeedCutOut="25"/><DataTable>{pts}</DataTable></PerformanceTable>'
        )
    heights = "".join(f"<Height>{h}</Height>" for h in hubs)
    return (
        f'<?xml version="1.0" encoding="utf-8"?><WindTurbineGenerator Description="{name}" '
        f'ManufacturerName="Synthetic" FormatVersion="1.0" RotorDiameter="{d}">'
        f"<SuggestedHeights>{heights}</SuggestedHeights>{''.join(tables)}</WindTurbineGenerator>"
    ).encode()


def layout_csv(n_rows=2, n_cols=4, spacing_x=5 * 136, spacing_y=7 * 136, x0=426000, y0=3483000, crs="UTM29N"):
    lines = ["id;x;y;crs;type;hub_height"]
    k = 1
    for r in range(n_rows):
        for c in range(n_cols):
            lines.append(f"WTG{k:02d};{x0 + c * spacing_x};{y0 + r * spacing_y};{crs};GEN-4.2-136 (synthetic);112")
            k += 1
    return ("\n".join(lines) + "\n").encode()
