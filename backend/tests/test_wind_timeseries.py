import numpy as np
import pandas as pd
import pytest
import xarray as xr

from app.nwp.timeseries import FLAG_AGGREGATED, FLAG_INTERPOLATED, FLAG_NATIVE, harmonise
from app.nwp.wind import relative_humidity, speed_dir_to_uv, uv_to_speed_dir


@pytest.mark.parametrize(
    "u,v,wd",
    [(0, -5, 0.0), (-5, 0, 90.0), (0, 5, 180.0), (5, 0, 270.0), (-3, -3, 45.0)],
)
def test_meteorological_convention(u, v, wd):
    # vent du Nord : souffle vers le Sud (v < 0) ; vent d'Est : souffle vers l'Ouest (u < 0)
    ws, d = uv_to_speed_dir(u, v)
    assert d == pytest.approx(wd)
    assert ws == pytest.approx(np.hypot(u, v))


def test_calm_has_undefined_direction():
    ws, wd = uv_to_speed_dir(0.0, 0.0)
    assert ws == 0 and np.isnan(wd)


def test_round_trip_random():
    rng = np.random.default_rng(0)
    ws, wd = rng.uniform(0.1, 40, 100_000), rng.uniform(0, 360, 100_000)
    u, v = speed_dir_to_uv(ws, wd)
    ws2, wd2 = uv_to_speed_dir(u, v)
    assert np.allclose(ws, ws2)
    assert np.allclose(np.angle(np.exp(1j * np.radians(wd - wd2))), 0, atol=1e-9)


def test_relative_humidity():
    assert relative_humidity(293.15, 293.15) == pytest.approx(100.0)
    assert relative_humidity(303.15, 283.15) == pytest.approx(28.6, abs=0.5)


def _raw(times, u, v, gust=None, semantics="period_max"):
    t = pd.DatetimeIndex(times)
    data = {
        "u_100m": (("member", "point", "time"), np.asarray(u, float)[None, None, :]),
        "v_100m": (("member", "point", "time"), np.asarray(v, float)[None, None, :]),
    }
    if gust is not None:
        data["gust_10m"] = (("member", "point", "time"), np.asarray(gust, float)[None, None, :])
    return xr.Dataset(data, coords={"member": [0], "point": [1], "time": t.values}, attrs={"gust_semantics": semantics})


def test_interpolation_across_north_uses_uv_not_raw_direction():
    # 350° → 10° : la direction interpolée doit passer par 0°, pas par 180°
    u0, v0 = speed_dir_to_uv(10, 350)
    u1, v1 = speed_dir_to_uv(10, 10)
    raw = _raw(["2026-09-22T00:00", "2026-09-22T01:00"], [u0, u1], [v0, v1])
    out = harmonise(raw, "10min")
    wd = out["wd_100m"].values[0, 0]
    assert wd[3] == pytest.approx(0.0, abs=1e-6) or wd[3] == pytest.approx(360.0, abs=1e-6)
    assert all(min(d, 360 - d) <= 10.0001 for d in wd)
    # vitesse scalaire : reste à 10 m/s ; le module des U/V interpolés tomberait à ~9,85
    assert np.allclose(out["ws_100m"].values[0, 0], 10.0)
    vec = harmonise(raw, "10min", speed_method="vector")["ws_100m"].values[0, 0]
    assert vec[3] == pytest.approx(10 * np.cos(np.radians(10)), rel=1e-6)


def test_flags_gfs_like_transition():
    # horaire jusqu'à 120 h puis 3-horaire (GFS)
    t0 = pd.Timestamp("2026-09-22T00:00")
    times = [t0 + pd.Timedelta(hours=h) for h in list(range(0, 121)) + [123, 126, 129]]
    n = len(times)
    raw = _raw(times, np.linspace(1, 10, n), np.zeros(n))
    out1 = harmonise(raw, "1h")
    f = pd.Series(out1["time_flag"].values, index=pd.DatetimeIndex(out1["time"].values))
    assert f[t0 + pd.Timedelta(hours=100)] == FLAG_NATIVE
    assert f[t0 + pd.Timedelta(hours=121)] == FLAG_INTERPOLATED
    assert f[t0 + pd.Timedelta(hours=123)] == FLAG_NATIVE
    out3 = harmonise(raw, "3h")
    f3 = pd.Series(out3["time_flag"].values, index=pd.DatetimeIndex(out3["time"].values))
    assert f3[t0] == FLAG_NATIVE  # période incomplète au début
    assert f3[t0 + pd.Timedelta(hours=3)] == FLAG_AGGREGATED
    assert f3[t0 + pd.Timedelta(hours=126)] == FLAG_NATIVE
    # agrégation : moyenne des échéances 1, 2, 3 h
    u = raw["u_100m"].values[0, 0]
    assert out3["u_100m"].values[0, 0, 1] == pytest.approx(u[1:4].mean(), rel=1e-6)


def test_pchip_no_overshoot_and_no_negative_speed():
    times = pd.date_range("2026-09-22", periods=6, freq="3h")
    ws = np.array([0.0, 0.0, 12.0, 12.0, 0.2, 0.0])
    raw = _raw(times, -ws, np.zeros(6))  # vent d'Est
    out = harmonise(raw, "15min", method="pchip")
    s = out["ws_100m"].values[0, 0]
    assert s.min() >= 0 and s.max() <= 12.0 + 1e-9
    lin = harmonise(raw, "15min", method="linear")["ws_100m"].values[0, 0]
    assert lin[2] == pytest.approx(0.0)  # 00:30 entre deux calmes


def test_period_max_gust_is_step_function():
    times = pd.date_range("2026-09-22", periods=3, freq="3h")
    raw = _raw(times, [1, 1, 1], [0, 0, 0], gust=[5, 20, 8])
    out = harmonise(raw, "1h")
    g = out["gust_10m"].values[0, 0]
    # 01:00 et 02:00 appartiennent à la période se terminant à 03:00 (max = 20)
    assert list(g[:7]) == [5, 20, 20, 20, 8, 8, 8]
    inst = harmonise(_raw(times, [1, 1, 1], [0, 0, 0], gust=[5, 20, 8], semantics="instantaneous"), "1h")
    assert inst["gust_10m"].values[0, 0, 1] == pytest.approx(10.0)


def test_no_extrapolation_and_invalid_args():
    times = pd.date_range("2026-09-22T00:30", periods=2, freq="1h")
    out = harmonise(_raw(times, [1, 2], [0, 0]), "1h")
    assert pd.Timestamp(out["time"].values[0]) == pd.Timestamp("2026-09-22T01:00")
    with pytest.raises(ValueError):
        harmonise(_raw(times, [1, 2], [0, 0]), "5min")
