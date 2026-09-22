from datetime import UTC, datetime

import numpy as np
import pytest

from app.nwp.sources.base import FetchRequest, PointRef
from app.nwp.sources.dwd import IconDwd, IconEuDwd, vertical_interp


def test_vertical_interp_log_profile_is_exact():
    # profil logarithmique u(z) = a ln(z/z0) : l'interpolation en ln(z) le reproduit exactement
    z0, a = 0.05, 1.2
    z = np.array([[10.0], [35.0], [72.0], [115.0], [170.0], [240.0]])
    u = a * np.log(z / z0)
    out = vertical_interp(z, u, [80, 100, 120, 180])
    assert np.allclose(out[:, 0], a * np.log(np.array([80, 100, 120, 180]) / z0))


def test_vertical_interp_no_extrapolation():
    z = np.array([[10.0], [35.0]])
    out = vertical_interp(z, np.array([[1.0], [2.0]]), [5, 100])
    assert np.isnan(out).all()


def test_icon_file_names_and_plan():
    run = datetime(2026, 9, 22, 0, tzinfo=UTC)
    src = IconDwd()
    assert src.url(run, "u_10m", "single-level", 7, "U_10M").endswith(
        "/icon/grib/00/u_10m/icon_global_icosahedral_single-level_2026092200_007_U_10M.grib2.bz2"
    )
    assert src.url(run, "u", "model-level", 12, "U", 118).endswith(
        "/icon/grib/00/u/icon_global_icosahedral_model-level_2026092200_012_118_U.grib2.bz2"
    )
    assert src.url(run, "hhl", "time-invariant", None, "HHL", 121).endswith(
        "/icon/grib/00/hhl/icon_global_icosahedral_time-invariant_2026092200_121_HHL.grib2.bz2"
    )
    steps = src.steps(run, 999)
    assert steps[78] == 78 and steps[79] == 81 and steps[-1] == 180
    req = FetchRequest("icon", run, 12, [PointRef(1, 5, 31.5, -9.7)], ["u_10m", "v_10m", "u_100m", "v_100m", "t_2m"])
    est = src.estimate(None, req)
    assert "u_100m" in est.available_variables and est.missing_variables == []
    assert any("derived" in n for n in est.notes)
    eu = IconEuDwd()
    assert eu.url(run, "t_2m", "single-level", 3, "T_2M").endswith(
        "/icon-eu/grib/00/t_2m/icon-eu_europe_regular-lat-lon_single-level_2026092200_003_T_2M.grib2.bz2"
    )
    assert eu.steps(run, 999)[-1] == 120


@pytest.mark.parametrize("hour,last", [(0, 180), (6, 120)])
def test_icon_horizon(hour, last):
    assert IconDwd().steps(datetime(2026, 9, 22, hour, tzinfo=UTC), 999)[-1] == last
