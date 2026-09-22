import numpy as np
import pytest

from app.geo.geodesy import distance_azimuth
from app.nwp.catalog import MODELS
from app.nwp.grids.regular import RegularGrid

GFS = MODELS["gfs"].regular_grid  # 90 → -90, 0 → 359.75
IFS = MODELS["ifs"].regular_grid  # 90 → -90, 180 → 179.75
ICON_EU = MODELS["icon_eu"].regular_grid  # 29.5 → 70.5, -23.5 → 62.5
ESSAOUIRA = (31.5125, -9.77)


@pytest.mark.parametrize("grid", [GFS, IFS, ICON_EU])
def test_bracketing_surrounds_site(grid):
    lat, lon = ESSAOUIRA
    ij = grid.bracketing(lat, lon)
    lats = sorted({float(grid.lat_of(i)) for i, _ in ij})
    lons = sorted({float(grid.lon_of(j)) for _, j in ij})
    assert len(ij) == 4 and len(lats) == 2 and len(lons) == 2
    assert lats[0] <= lat <= lats[1]
    assert lons[0] <= lon <= lons[1]
    assert lats[1] - lats[0] == pytest.approx(abs(grid.dlat))


def test_gfs_negative_longitude_uses_0_360_indices():
    # -9.77° ↔ 350.23° : colonnes j = 1400 et 1401 (350.0° et 350.25°).
    ij = GFS.bracketing(*ESSAOUIRA)
    assert sorted({j for _, j in ij}) == [1400, 1401]
    assert sorted({float(GFS.lon_of(j)) for _, j in ij}) == [-10.0, -9.75]
    # latitudes décroissantes : i = (90 - 31.5)/0.25 = 234 → 31.5°, 233 → 31.75°
    assert sorted({i for i, _ in ij}) == [233, 234]


def test_ifs_longitudes_from_180():
    ij = IFS.bracketing(*ESSAOUIRA)
    # (−10 − 180) mod 360 = 170 → j = 680
    assert sorted({j for _, j in ij}) == [680, 681]
    assert sorted({float(IFS.lon_of(j)) for _, j in ij}) == [-10.0, -9.75]


def test_greenwich_crossing_global():
    ij = GFS.bracketing(35.1, -0.1)
    lons = sorted({float(GFS.lon_of(j)) for _, j in ij})
    assert lons == [-0.25, 0.0]
    assert sorted({j for _, j in ij}) == [0, 1439]


def test_site_on_node():
    ij = GFS.bracketing(31.5, -9.75)
    nodes = {(float(GFS.lat_of(i)), float(GFS.lon_of(j))) for i, j in ij}
    assert (31.5, -9.75) in nodes


@pytest.mark.parametrize("n", [4, 9, 16])
def test_nearest_matches_brute_force(n):
    rng = np.random.default_rng(0)
    for _ in range(50):
        lat, lon = rng.uniform(21, 36), rng.uniform(-17, -1)
        got = GFS.nearest(lat, lon, n)
        # force brute sur un voisinage large
        ii, jj = np.meshgrid(np.arange(200, 280), np.arange(1360, 1440), indexing="ij")
        d, _ = distance_azimuth(lat, lon, GFS.lat_of(ii.ravel()), GFS.lon_of(jj.ravel()))
        expected = np.sort(d)[:n]
        assert np.allclose([g[2] for g in got], expected, atol=1e-3)
        assert len({(g[0], g[1]) for g in got}) == n


def test_nearest_9_is_centred_block_on_node():
    got = GFS.nearest(31.5, -9.75, 9)
    lats = sorted({float(GFS.lat_of(i)) for i, _, _, _ in got})
    lons = sorted({float(GFS.lon_of(j)) for _, j, _, _ in got})
    assert lats == [31.25, 31.5, 31.75] and lons == [-10.0, -9.75, -9.5]


def test_nearest_16_contains_bracketing_nodes():
    four = set(GFS.bracketing(*ESSAOUIRA))
    sixteen = {(i, j) for i, j, _, _ in GFS.nearest(*ESSAOUIRA, 16)}
    assert four <= sixteen


def test_native_index_round_trip():
    i, j = 234, 1400
    idx = GFS.native_index(i, j)
    assert tuple(int(v) for v in GFS.ij_of(idx)) == (i, j)


@pytest.mark.parametrize(
    "site,inside",
    [
        ((31.5125, -9.77), True),  # Essaouira
        ((33.57, -7.59), True),  # Casablanca
        ((29.6, -9.0), False),  # dans la marge de 5 mailles (29,5 → 29,8125)
        ((27.1253, -13.1625), False),  # Laâyoune
        ((23.6848, -15.958), False),  # Dakhla
    ],
)
def test_icon_eu_domain(site, inside):
    assert ICON_EU.contains(*site, margin_cells=5) is inside


def test_icon_eu_margin_vs_strict():
    assert ICON_EU.contains(29.6, -9.0, margin_cells=0)
    assert not ICON_EU.contains(29.4, -9.0, margin_cells=0)


def test_regional_bracketing_on_edge_is_clamped():
    g = RegularGrid(10.0, 1.0, 5, 0.0, 1.0, 5)
    ij = g.bracketing(14.0, 4.0)
    assert max(i for i, _ in ij) == 4 and max(j for _, j in ij) == 4
