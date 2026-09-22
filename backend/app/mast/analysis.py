"""Analyses du mât : cisaillement (secteur, heure, saison), turbulence, rose, densité de l'air.

Toutes les analyses portent sur les séries composites valides (`ws@h`, `wd@h`, `sd@h`) produites
par le contrôle qualité.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

R_DRY = 287.05  # J/(kg·K)
SEASONS = {
    12: "DJF",
    1: "DJF",
    2: "DJF",
    3: "MAM",
    4: "MAM",
    5: "MAM",
    6: "JJA",
    7: "JJA",
    8: "JJA",
    9: "SON",
    10: "SON",
    11: "SON",
}


def heights(df: pd.DataFrame, kind: str) -> list[float]:
    return sorted(float(c.split("@")[1]) for c in df.columns if c.startswith(f"{kind}@"))


def _sector(wd: pd.Series, n: int) -> pd.Series:
    width = 360.0 / n
    return (np.floor(((wd + width / 2) % 360.0) / width) % n).astype("Int64")


def _alpha(u1, u2, z1, z2):
    return np.log(u2 / u1) / np.log(z2 / z1)


def shear(df: pd.DataFrame, sectors: int = 12, min_ws: float = 3.0) -> dict | None:
    """Exposant de cisaillement entre les deux hauteurs d'anémomètre les plus hautes (rapport ≥ 1,2).

    α de groupe = ln(ū2/ū1) / ln(z2/z1) sur les enregistrements valides où ū1 > 3 m/s
    (plus robuste que la moyenne des α individuels).
    """
    hs = heights(df, "ws")
    if len(hs) < 2:
        return None
    z2 = hs[-1]
    lower = [h for h in hs[:-1] if z2 / h >= 1.2]
    if not lower:
        return None
    z1 = lower[-1]
    u1, u2 = df[f"ws@{z1:g}"], df[f"ws@{z2:g}"]
    wd_h = heights(df, "wd")
    wd = df[f"wd@{min(wd_h, key=lambda h: abs(h - z2)):g}"] if wd_h else None
    ok = (u1 > min_ws) & u2.notna()
    if wd is not None:
        ok &= wd.notna()
    sub = pd.DataFrame({"u1": u1[ok], "u2": u2[ok]})
    if len(sub) < 30:
        return None

    def group(key):
        g = sub.groupby(key)
        res = g.agg(u1=("u1", "mean"), u2=("u2", "mean"), n=("u1", "size"))
        res["alpha"] = _alpha(res["u1"], res["u2"], z1, z2)
        return [{"key": str(k), "alpha": round(float(r.alpha), 3), "n": int(r.n)} for k, r in res.iterrows()]

    out = {
        "z_low_m": z1,
        "z_high_m": z2,
        "alpha_all": round(float(_alpha(sub.u1.mean(), sub.u2.mean(), z1, z2)), 3),
        "n": int(len(sub)),
        "by_hour_utc": group(sub.index.hour),
        "by_season": group(sub.index.month.map(SEASONS)),
    }
    if wd is not None:
        sec = _sector(wd[ok], sectors)
        width = 360 / sectors
        rows = group(sec)
        for r in rows:
            r["sector_deg"] = round(int(r["key"]) * width, 1)
        out["by_sector"] = rows
        out["sector_width_deg"] = width
    return out


def turbulence(df: pd.DataFrame, height: float | None = None, sectors: int = 12) -> dict | None:
    """Intensité de turbulence TI = σ/ū (ū ≥ 4 m/s) : par classe de vitesse (moyenne, P90 IEC =
    moyenne + 1,28 σ_TI) et par secteur."""
    hs = [h for h in heights(df, "sd")]
    if not hs:
        return None
    h = height if height in hs else hs[-1]
    u, sd = df[f"ws@{h:g}"], df[f"sd@{h:g}"]
    ok = (u >= 4.0) & sd.notna()
    ti = (sd / u)[ok]
    if len(ti) < 30:
        return None
    bins = np.arange(4, 26, 1.0)
    cls = pd.cut(u[ok], bins, right=False)
    g = ti.groupby(cls, observed=True)
    by_speed = [
        {
            "ws_bin": float(iv.left) + 0.5,
            "ti_mean": round(float(v.mean()), 4),
            "ti_rep": round(float(v.mean() + 1.28 * v.std()), 4) if len(v) > 2 else None,
            "n": int(len(v)),
        }
        for iv, v in g
    ]
    out = {"height_m": h, "ti_mean": round(float(ti.mean()), 4), "n": int(len(ti)), "by_speed": by_speed}
    wd_h = heights(df, "wd")
    if wd_h:
        wd = df[f"wd@{min(wd_h, key=lambda x: abs(x - h)):g}"][ok]
        sec = _sector(wd, sectors)
        gs = ti.groupby(sec)
        out["by_sector"] = [
            {"sector_deg": round(int(k) * 360 / sectors, 1), "ti_mean": round(float(v.mean()), 4), "n": int(len(v))}
            for k, v in gs
        ]
    return out


def wind_rose(df: pd.DataFrame, sectors: int = 16) -> dict | None:
    hs, wds = heights(df, "ws"), heights(df, "wd")
    if not hs or not wds:
        return None
    h = hs[-1]
    u = df[f"ws@{h:g}"]
    wd = df[f"wd@{min(wds, key=lambda x: abs(x - h)):g}"]
    ok = u.notna() & wd.notna()
    if ok.sum() < 30:
        return None
    edges = [0, 3, 6, 9, 12, 15, np.inf]
    sec = _sector(wd[ok], sectors).astype(int)
    cls = np.digitize(u[ok], edges[1:-1])
    freq = np.zeros((len(edges) - 1, sectors))
    np.add.at(freq, (cls, sec.to_numpy()), 1)
    freq = 100 * freq / ok.sum()
    labels = [
        f"{edges[k]:g}–{edges[k + 1]:g}" if np.isfinite(edges[k + 1]) else f"≥ {edges[k]:g}"
        for k in range(len(edges) - 1)
    ]
    return {
        "height_m": h,
        "sectors_deg": [round(k * 360 / sectors, 1) for k in range(sectors)],
        "speed_bins": labels,
        "frequency_pct": freq.round(2).tolist(),
        "n": int(ok.sum()),
        "mean_speed": round(float(u[ok].mean()), 2),
    }


def air_density(df: pd.DataFrame) -> dict | None:
    ts, ps = heights(df, "temp"), heights(df, "pressure")
    if not ts or not ps:
        return None
    t = df[f"temp@{ts[-1]:g}"] + 273.15
    p = df[f"pressure@{ps[-1]:g}"] * 100.0
    rho = (p / (R_DRY * t)).dropna()
    if rho.empty:
        return None
    return {
        "mean": round(float(rho.mean()), 4),
        "min": round(float(rho.min()), 4),
        "max": round(float(rho.max()), 4),
        "monthly": {str(k): round(float(v), 4) for k, v in rho.groupby(rho.index.to_period("M")).mean().items()},
        "note": "dry-air density from mast temperature and pressure (humidity correction in milestone 4)",
    }


def overview(df: pd.DataFrame) -> dict:
    means = {}
    for h in heights(df, "ws"):
        u = df[f"ws@{h:g}"]
        means[f"{h:g}"] = {
            "mean": round(float(u.mean()), 3) if u.notna().any() else None,
            "valid_pct": round(float(u.notna().mean() * 100), 2),
        }
    return {"ws_heights": means, "start_utc": str(df.index[0]), "end_utc": str(df.index[-1]), "n_records": int(len(df))}
