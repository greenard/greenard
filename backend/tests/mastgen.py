"""Générateur de données de mât SYNTHÉTIQUES (tests et démonstration uniquement).

Profil : Weibull (k = 2,2), cisaillement α = 0,14, cycle diurne, régimes de NNE et de SSW,
anémomètres redondants à 80 m (bras N et S), 60 et 40 m, girouette 78 m, T, HR, P.
Défauts injectés et connus : ombrage du mât, givrage, girouette bloquée, pics, trou de données.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

ALPHA = 0.14


def generate(days: int = 60, start: str = "2025-01-01", seed: int = 1) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, periods=days * 144, freq="10min")  # début d'intervalle, UTC+1 « logger »
    n = len(t)
    hour = t.hour.to_numpy() + t.minute.to_numpy() / 60
    base = rng.weibull(2.2, n) * 8.0
    base = pd.Series(base).rolling(12, min_periods=1).mean().to_numpy() * (
        1 + 0.15 * np.sin(2 * np.pi * (hour - 15) / 24)
    )
    # deux régimes : alizé de NNE (dominant) et épisodes de SSW (~20 % du temps)
    southerly = np.sin(np.arange(n) / 900) > 0.6
    wd = np.where(southerly, 190.0, 22.5 + 40 * np.sin(np.arange(n) / 700)) + rng.normal(0, 15, n)
    wd = wd % 360
    u80 = base * 1.0
    data = {}
    truth: dict = {"alpha": ALPHA}
    for h in (80, 60, 40):
        u = u80 * (h / 80) ** ALPHA
        data[h] = u

    # anémomètres 80 m : bras N (0°) et S (180°) ; ombrage quand le vent vient de l'arrière du bras
    def shadowed(boom):
        return np.abs((wd - (boom + 180) + 180) % 360 - 180) <= 25

    n80 = data[80] * np.where(shadowed(0), 0.82, 1.0) + rng.normal(0, 0.05, n)
    s80 = data[80] * np.where(shadowed(180), 0.82, 1.0) + rng.normal(0, 0.05, n)
    ti = 0.10 + 0.6 / np.maximum(data[80], 1)
    df = pd.DataFrame(
        {
            "ws80N_avg": n80,
            "ws80N_sd": n80 * ti,
            "ws80N_max": n80 * (1 + 2.5 * ti),
            "ws80S_avg": s80,
            "ws80S_sd": s80 * ti,
            "ws60_avg": data[60] + rng.normal(0, 0.05, n),
            "ws60_sd": data[60] * ti,
            "ws40_avg": data[40] + rng.normal(0, 0.05, n),
            "ws40_sd": data[40] * ti,
            "wd78_avg": wd,
            "temp_2m_avg": 14 + 7 * np.sin(2 * np.pi * (hour - 9) / 24) + rng.normal(0, 0.3, n),
            "rh_2m_avg": np.clip(60 - 20 * np.sin(2 * np.pi * (hour - 9) / 24) + rng.normal(0, 3, n), 5, 100),
            "press_avg": 1005 + rng.normal(0, 1.5, n),
        },
        index=t,
    )
    # givrage : 12 h à T < 0, HR 97 %, anémomètre 60 m bloqué à 0,0 ; sd = 0
    ice = slice(2000, 2072)
    df.iloc[ice, df.columns.get_loc("temp_2m_avg")] = -2.0
    df.iloc[ice, df.columns.get_loc("rh_2m_avg")] = 97.0
    df.iloc[ice, df.columns.get_loc("ws60_avg")] = 1.2
    df.iloc[ice, df.columns.get_loc("ws60_sd")] = 0.0
    truth["icing_rows"] = (2000, 2072)
    # girouette bloquée 3 h
    df.iloc[5000:5018, df.columns.get_loc("wd78_avg")] = 123.4
    truth["stuck_vane_rows"] = (5000, 5018)
    # pics
    for k in (3000, 6100):
        df.iloc[k, df.columns.get_loc("ws40_avg")] += 25
    truth["spike_rows"] = [3000, 6100]
    # trou de 2 jours
    df = df.drop(df.index[7000:7288])
    truth["gap_records"] = 288
    return df, truth


def to_toa5(df: pd.DataFrame, station: str = "MAST_DEMO") -> str:
    """Export au format Campbell TOA5 (horodatage de fin d'intervalle)."""
    cols = list(df.columns)
    lines = [
        f'"TOA5","{station}","CR1000X","12345","CR1000X.Std.05","CPU:mast.CR1X","1234","Table10min"',
        '"TIMESTAMP","RECORD",' + ",".join(f'"{c}"' for c in cols),
        '"TS","RN",' + ",".join('"m/s"' if c.startswith("ws") else '""' for c in cols),
        '"","",' + ",".join('"Avg"' for _ in cols),
    ]
    end = df.index + pd.Timedelta(minutes=10)
    for k, (ts, row) in enumerate(zip(end, df.to_numpy(), strict=True)):
        lines.append(f'"{ts:%Y-%m-%d %H:%M:%S}",{k},' + ",".join(f"{v:.3f}" for v in row))
    return "\n".join(lines) + "\n"
