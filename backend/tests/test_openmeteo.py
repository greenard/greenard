from datetime import UTC, datetime

import httpx
import numpy as np
import pandas as pd
import pytest

from app.nwp.sources.base import FetchRequest, PointRef
from app.nwp.sources.openmeteo import OpenMeteo
from app.nwp.wind import uv_to_speed_dir

RUN = datetime(2026, 9, 22, 0, tzinfo=UTC)


def _hourly(n_hours: int, member_count: int = 0):
    t0 = int(pd.Timestamp("2026-09-22T00:00Z").timestamp())
    hourly = {"time": [t0 + 3600 * k for k in range(n_hours)]}
    for m in range(member_count + 1):
        sfx = "" if m == 0 else f"_member{m:02d}"
        hourly[f"wind_speed_100m{sfx}"] = [5.0 + m + 0.1 * k for k in range(n_hours)]
        hourly[f"wind_direction_100m{sfx}"] = [270.0] * n_hours
        hourly[f"temperature_2m{sfx}"] = [20.0] * n_hours
        hourly[f"surface_pressure{sfx}"] = [1000.0] * n_hours
        hourly[f"wind_speed_120m{sfx}"] = [None] * n_hours  # non fournie par ce modèle
        hourly[f"wind_direction_120m{sfx}"] = [None] * n_hours
    return hourly


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ifs_keeps_only_native_3h_steps_and_forces_raw_cell_values():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[{"hourly": _hourly(48)}, {"hourly": _hourly(48)}])

    pts = [PointRef(1, 0, 31.5, -9.75), PointRef(2, 0, 31.5, -10.0)]
    req = FetchRequest("ifs", RUN, 24, pts, ["u_100m", "v_100m", "u_120m", "v_120m", "t_2m", "sp"])
    ds = OpenMeteo("ifs").fetch(_client(handler), req, lambda p, m: None)
    assert seen["cell_selection"] == "nearest"
    assert seen["elevation"] == "nan,nan"
    assert seen["models"] == "ecmwf_ifs025"
    assert seen["latitude"] == "31.50000,31.50000"
    # IFS : échéances natives 3-horaires uniquement (les heures interpolées par Open-Meteo sont écartées)
    assert list(ds["lead_h"].values) == [0, 3, 6, 9, 12, 15, 18, 21, 24]
    ws, wd = uv_to_speed_dir(ds["u_100m"].values, ds["v_100m"].values)
    assert np.allclose(wd, 270.0)
    assert ws[0, 0, 1] == pytest.approx(5.3)  # 03:00
    assert ds["t_2m"].values[0, 0, 0] == pytest.approx(293.15)
    assert ds["sp"].values[0, 1, 0] == pytest.approx(100000.0)
    assert "u_120m" not in ds and "u_120m" in ds.attrs["missing_variables"]


def test_ensemble_members():
    def handler(request):
        assert request.url.host == "ensemble-api.open-meteo.com"
        return httpx.Response(200, json={"hourly": _hourly(30, member_count=3)})

    req = FetchRequest("gefs", RUN, 12, [PointRef(1, 0, 31.5, -9.75)], ["u_100m", "v_100m"])
    ds = OpenMeteo("gefs").fetch(_client(handler), req, lambda p, m: None)
    assert list(ds["member"].values) == [0, 1, 2, 3]
    ws, _ = uv_to_speed_dir(ds["u_100m"].values, ds["v_100m"].values)
    assert ws[2, 0, 0] == pytest.approx(7.0)


def test_http_error_is_reported():
    from app.nwp.sources.base import SourceError

    req = FetchRequest("gfs", RUN, 6, [PointRef(1, 0, 31.5, -9.75)], ["t_2m"])
    with pytest.raises(SourceError) as e:
        OpenMeteo("gfs").fetch(_client(lambda r: httpx.Response(400, json={"reason": "bad"})), req, lambda p, m: None)
    assert e.value.code == "SOURCE_HTTP_ERROR"
