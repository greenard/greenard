"""Harmonisation temporelle des extraits de prévision (§4.3.2 de la note d'architecture).

Entrée : jeu de données « brut » aux seules échéances natives du modèle, dimensions
(member, point, time), variables canoniques en unités SI (`app.nwp.variables`).

Sortie au pas cible (10 min, 15 min, 1 h, 3 h), chaque horodatage portant un drapeau :

- `native` (0)       : l'échéance existe dans le modèle et le pas natif local est ≥ au pas cible ;
- `aggregated` (1)   : pas natif local plus fin que le pas cible → agrégation sur la période
  (t − Δ, t] : moyenne scalaire de la vitesse, moyenne vectorielle de la direction, maximum des
  rafales, moyenne des autres grandeurs ;
- `interpolated` (2) : échéance absente du modèle → interpolation entre échéances natives.

Règles d'interpolation :
- on interpole les composantes U et V, jamais la direction brute ; la direction est celle des U/V
  interpolés ;
- la vitesse est, par défaut, interpolée comme un scalaire (`speed_method="scalar"`) : le module
  des U/V interpolés sous-estime la vitesse quand la direction tourne entre deux échéances ;
  l'option `"vector"` utilise ce module ;
- schéma `linear` ou `pchip` (spline cubique monotone de Fritsch-Carlson : pas de dépassement
  des valeurs encadrantes, donc pas de vitesse négative) ;
- rafales « maximum sur la période précédente » (IFS `10fg`, ICON `VMAX_10M`) : jamais
  interpolées, la valeur de l'échéance native suivante s'applique à toute la période ;
- aucune extrapolation au-delà de la première / dernière échéance native.

Limite : l'interpolation ne crée aucune variabilité sous-horaire physique (voir docs/LIMITS.md).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import xarray as xr
from scipy.interpolate import PchipInterpolator

from app.nwp.variables import PRESSURE_RE, WIND_HEIGHT_RE
from app.nwp.wind import uv_to_speed_dir

STEPS = {"10min": "10min", "15min": "15min", "1h": "1h", "3h": "3h"}
FLAG_NATIVE, FLAG_AGGREGATED, FLAG_INTERPOLATED = 0, 1, 2
FLAG_MEANINGS = "native aggregated interpolated"


def _wind_pairs(names: list[str]) -> list[tuple[str, str, str]]:
    """[(suffixe, nom_u, nom_v)] pour chaque hauteur / niveau où U et V sont présents."""
    out = []
    for n in names:
        m = WIND_HEIGHT_RE.match(n) or PRESSURE_RE.match(n)
        if m and m.group(1) == "u":
            suffix = n[2:]
            if f"v_{suffix}" in names:
                out.append((suffix, n, f"v_{suffix}"))
    return out


def target_times(native: pd.DatetimeIndex, step: str) -> pd.DatetimeIndex:
    delta = pd.Timedelta(STEPS[step])
    start = native[0].ceil(delta)
    return pd.date_range(start, native[-1], freq=delta)


def _interp(x: np.ndarray, y: np.ndarray, xi: np.ndarray, method: str) -> np.ndarray:
    """Interpolation le long du dernier axe, tolérante aux valeurs manquantes."""
    if y.shape[-1] == 1:
        return np.full(y.shape[:-1] + (xi.size,), np.nan)
    flat = y.reshape(-1, y.shape[-1])
    out = np.full((flat.shape[0], xi.size), np.nan)
    complete = ~np.isnan(flat).any(axis=1)
    if complete.any():
        if method == "pchip":
            out[complete] = PchipInterpolator(x, flat[complete], axis=1, extrapolate=False)(xi)
        else:
            for k in np.flatnonzero(complete):
                out[k] = np.interp(xi, x, flat[k], left=np.nan, right=np.nan)
    for k in np.flatnonzero(~complete):
        ok = ~np.isnan(flat[k])
        if ok.sum() >= 2:
            if method == "pchip":
                out[k] = PchipInterpolator(x[ok], flat[k, ok], extrapolate=False)(xi)
            else:
                out[k] = np.interp(xi, x[ok], flat[k, ok], left=np.nan, right=np.nan)
    return out.reshape(y.shape[:-1] + (xi.size,))


def classify(native: pd.DatetimeIndex, targets: pd.DatetimeIndex, step: str) -> tuple[np.ndarray, list]:
    """Drapeau par horodatage cible et, pour les agrégats, les indices natifs de la période."""
    delta = pd.Timedelta(STEPS[step])
    native_pos = {t: k for k, t in enumerate(native)}
    flags = np.empty(targets.size, dtype=np.int8)
    windows: list = [None] * targets.size
    for n, t in enumerate(targets):
        k = native_pos.get(t)
        if k is None:
            flags[n] = FLAG_INTERPOLATED
            continue
        spacing = native[k] - native[k - 1] if k > 0 else (native[1] - native[0] if native.size > 1 else delta)
        if spacing >= delta:
            flags[n] = FLAG_NATIVE
            continue
        if t - delta >= native[0]:
            flags[n] = FLAG_AGGREGATED
            windows[n] = [j for j in range(k + 1) if native[j] > t - delta]
        else:
            flags[n] = FLAG_NATIVE  # début de série : période incomplète, valeur instantanée conservée
    return flags, windows


def harmonise(
    raw: xr.Dataset,
    step: str = "1h",
    method: str = "linear",
    speed_method: str = "scalar",
) -> xr.Dataset:
    if step not in STEPS:
        raise ValueError(f"step must be one of {list(STEPS)}")
    if method not in ("linear", "pchip"):
        raise ValueError("method must be 'linear' or 'pchip'")
    if speed_method not in ("scalar", "vector"):
        raise ValueError("speed_method must be 'scalar' or 'vector'")
    raw = raw.sortby("time").transpose("member", "point", "time")
    native = pd.DatetimeIndex(raw["time"].values)
    targets = target_times(native, step)
    flags, windows = classify(native, targets, step)
    x = (native - native[0]).total_seconds().to_numpy()
    xi = (targets - native[0]).total_seconds().to_numpy()
    ti = np.searchsorted(native, targets)  # position native (si présente)
    is_native = flags == FLAG_NATIVE
    is_agg = flags == FLAG_AGGREGATED
    is_int = flags == FLAG_INTERPOLATED
    gust_period_max = raw.attrs.get("gust_semantics", "period_max") == "period_max"

    def build(values: np.ndarray, kind: str) -> np.ndarray:
        """kind : 'mean' (u, v, scalaires), 'speed' (vitesse), 'max' (rafale période), 'inst'."""
        out = np.full(values.shape[:-1] + (targets.size,), np.nan, dtype=float)
        if is_native.any():
            out[..., is_native] = values[..., ti[is_native]]
        for n in np.flatnonzero(is_agg):
            w = values[..., windows[n]]
            out[..., n] = np.nanmax(w, axis=-1) if kind == "max" else np.nanmean(w, axis=-1)
        if is_int.any():
            if kind == "max":
                # valeur de l'échéance native suivante (maximum sur la période qui s'y termine)
                nxt = np.clip(ti[is_int], 0, native.size - 1)
                vals = values[..., nxt]
                vals[..., targets[is_int] > native[-1]] = np.nan
                out[..., is_int] = vals
            else:
                out[..., is_int] = _interp(x, values, xi[is_int], method)
                if kind == "speed":
                    out[..., is_int] = np.clip(out[..., is_int], 0.0, None)
        return out

    names = list(raw.data_vars)
    data = {}
    handled = set()
    for suffix, un, vn in _wind_pairs(names):
        u, v = raw[un].values.astype(float), raw[vn].values.astype(float)
        ws_native = np.hypot(u, v)
        uo, vo = build(u, "mean"), build(v, "mean")
        ws = build(ws_native, "speed")
        if speed_method == "vector":
            ws[..., is_int] = np.hypot(uo[..., is_int], vo[..., is_int])
        _, wd = uv_to_speed_dir(uo, vo)
        for nm, arr in ((un, uo), (vn, vo), (f"ws_{suffix}", ws), (f"wd_{suffix}", wd)):
            data[nm] = (("member", "point", "time"), arr.astype("float32"))
        handled |= {un, vn}
    for nm in names:
        if nm in handled or raw[nm].dims != ("member", "point", "time"):
            continue
        kind = "max" if nm.startswith("gust") and gust_period_max else ("inst" if nm.startswith("gust") else "mean")
        data[nm] = (("member", "point", "time"), build(raw[nm].values.astype(float), kind).astype("float32"))

    out = xr.Dataset(data, coords={"member": raw["member"], "point": raw["point"], "time": targets.values})
    for c in raw.coords:
        if c not in out.coords and raw[c].dims and raw[c].dims[0] in ("point", "member"):
            out = out.assign_coords({c: raw[c]})
    out["time_flag"] = ("time", flags)
    out["time_flag"].attrs = {"flag_values": [0, 1, 2], "flag_meanings": FLAG_MEANINGS}
    out.attrs = {
        **raw.attrs,
        "target_step": step,
        "interpolation_method": method,
        "speed_method": speed_method,
        "aggregation": "period (t - step, t]: scalar mean speed, vector mean direction, max gust, mean others",
    }
    for nm in out.data_vars:
        if nm in raw:
            out[nm].attrs = dict(raw[nm].attrs)
    return out
