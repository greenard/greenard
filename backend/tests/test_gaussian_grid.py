from datetime import UTC, datetime

import httpx
import numpy as np
import pandas as pd
import pytest

from app.geo.geodesy import distance_azimuth
from app.nwp.catalog import native_leads
from app.nwp.gridpoints import find_candidates
from app.nwp.grids.gaussian import ReducedGaussianGrid
from app.nwp.sources.base import FetchRequest, PointRef
from app.nwp.sources.openmeteo import OpenMeteo


@pytest.fixture(scope="module")
def o1280():
    return ReducedGaussianGrid(1280)


def test_o1280_structure(o1280):
    assert o1280.size == 6_599_680
    assert o1280.counts[0] == 20 and o1280.counts[1279] == 5136 and o1280.counts[1280] == 5136
    assert o1280.lats[0] == pytest.approx(89.946187, abs=1e-5)  # première latitude O1280 (ECMWF)
    assert np.allclose(o1280.lats, -o1280.lats[::-1])  # symétrie équatoriale
    assert o1280.row_j_of(o1280.native_index(1500, 17)) == (1500, 17)


def test_bracketing_surrounds_site(o1280):
    lat, lon = 31.5125, -9.77
    pts = o1280.bracketing(lat, lon)
    lats = sorted({float(o1280.lats[r]) for r, _ in pts})
    assert lats[0] <= lat <= lats[1] and lats[1] - lats[0] < 0.08
    for r in {r for r, _ in pts}:
        lons = sorted(o1280.lon_of(r, j) for rr, j in pts if rr == r)
        assert lons[0] <= lon <= lons[1]


@pytest.mark.parametrize("n", [4, 9, 16])
def test_nearest_matches_brute_force(o1280, n):
    rng = np.random.default_rng(3)
    for _ in range(20):
        lat, lon = rng.uniform(21, 36), rng.uniform(-17, -1)
        got = [d for _, _, d, _ in o1280.nearest(lat, lon, n)]
        r0 = o1280._row_above(lat)
        cand = [
            (r, j)
            for r in range(r0 - 6, r0 + 8)
            for j in range(int(o1280.counts[r]))
            if abs(o1280.lon_of(r, j) - lon) < 0.5
        ]
        d, _ = distance_azimuth(
            lat, lon, [float(o1280.lats[r]) for r, _ in cand], [o1280.lon_of(r, j) for r, j in cand]
        )
        assert np.allclose(got, np.sort(d)[:n], atol=1e-3)


def test_find_candidates_ifs_9km():
    res = find_candidates("ifs_9km", 31.5125, -9.77, method="bracket")
    assert res.status == "ok" and len(res.candidates) == 4
    assert max(c.distance_m for c in res.candidates) < 12_000
    assert res.message_code == "INVARIANTS_UNAVAILABLE"


def test_open_meteo_ifs_9km_native_steps():
    seen = {}
    t0 = int(pd.Timestamp("2026-09-22T00:00Z").timestamp())
    hourly = {
        "time": [t0 + 3600 * k for k in range(200)],
        "wind_speed_100m": [5.0] * 200,
        "wind_direction_100m": [180.0] * 200,
    }

    def handler(req):
        seen.update(dict(req.url.params))
        return httpx.Response(200, json={"hourly": hourly})

    run = datetime(2026, 9, 22, 0, tzinfo=UTC)
    req = FetchRequest("ifs_9km", run, 150, [PointRef(1, 0, 31.49, -9.77)], ["u_100m", "v_100m"])
    ds = OpenMeteo("ifs_9km").fetch(httpx.Client(transport=httpx.MockTransport(handler)), req, lambda p, m: None)
    assert seen["models"] == "ecmwf_ifs"
    leads = list(ds["lead_h"].values)
    assert leads[:91] == list(range(91)) and leads[91:94] == [93, 96, 99] and leads[-1] == 150
    assert leads == native_leads("ifs_9km", 0, 150)


@pytest.mark.parametrize(
    "model,hour,checks",
    [
        ("gfs", 0, {120: True, 121: False, 123: True, 384: True}),
        ("ifs", 0, {144: True, 147: False, 150: True, 360: True}),
        ("ifs", 6, {144: True, 150: False}),
        ("icon", 0, {78: True, 79: False, 81: True, 180: True}),
    ],
)
def test_native_leads_schedule_transitions(model, hour, checks):
    leads = set(native_leads(model, hour))
    for h, expected in checks.items():
        assert (h in leads) is expected, (model, h)
