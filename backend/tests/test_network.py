"""Tests sur les sources réelles (`pytest -m network`). Désactivés par défaut."""

import pytest

from app.nwp import invariants
from app.nwp.invariants.fetch import prepare
from app.terrain import dem

pytestmark = pytest.mark.network


@pytest.mark.parametrize("model", ["gfs", "ifs"])
def test_regular_invariants_real(model):
    prepare(model)
    inv = invariants.load(model)
    g = inv.grid
    assert g.global_lon and abs(g.dlat) == 0.25
    # Essaouira (terre) : nœud 31.5N -9.75E ; océan : 31.5N -10.5E
    i = round((31.5 - g.lat_first) / g.dlat)
    land_j = round(((-9.75 - g.lon_first) % 360) / g.dlon)
    sea_j = round(((-10.5 - g.lon_first) % 360) / g.dlon)
    assert inv.lookup(i, land_j)[1] > 0.5
    assert inv.lookup(i, sea_j)[1] < 0.5
    assert abs(inv.lookup(i, sea_j)[0]) < 20


@pytest.mark.parametrize("model", ["icon", "icon_eu"])
def test_dwd_invariants_real(model):
    prepare(model)
    assert invariants.status(model)["ready"]


def test_copernicus_dem():
    toubkal = dem.sample(31.0597, -7.9160)  # Jbel Toubkal, 4167 m
    assert 3900 < toubkal.elevation_m < 4250
    sea = dem.sample(31.5, -11.0)
    assert sea.sea_tile and sea.elevation_m == 0.0
    m = dem.box_mean(31.375, 31.625, -9.875, -9.625)
    assert m is not None and 0 < m < 1000
