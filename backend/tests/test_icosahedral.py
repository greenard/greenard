import os
from pathlib import Path

import numpy as np
import pytest

from app.geo.geodesy import distance_azimuth
from app.nwp.grids.icosahedral import IcosahedralGrid, point_in_spherical_triangle, to_xyz
from tests.icogrid import synthetic_grid


@pytest.fixture(scope="module")
def grid():
    clat, clon, voc, vlat, vlon = synthetic_grid(6)  # 81 920 cellules, ~80 km
    return IcosahedralGrid(clat, clon, voc, vlat, vlon)


def _brute_force(g: IcosahedralGrid, lat, lon, k):
    p = to_xyz(np.radians(lat), np.radians(lon))
    ang = np.arccos(np.clip(to_xyz(g.clat, g.clon) @ p, -1, 1))
    return np.argsort(ang, kind="stable")[:k]


def test_kdtree_matches_brute_force(grid):
    rng = np.random.default_rng(42)
    for _ in range(1000):
        lat = float(np.degrees(np.arcsin(rng.uniform(-1, 1))))
        lon = float(rng.uniform(-180, 180))
        idx, _ = grid.nearest_indices(lat, lon, 4)
        assert idx[0] == _brute_force(grid, lat, lon, 1)[0]
        assert set(idx) == set(_brute_force(grid, lat, lon, 4))


def test_containing_cell_is_valid(grid):
    rng = np.random.default_rng(1)
    for _ in range(300):
        lat = float(np.degrees(np.arcsin(rng.uniform(-1, 1))))
        lon = float(rng.uniform(-180, 180))
        c = grid.containing_cell(lat, lon)
        assert c is not None
        assert point_in_spherical_triangle(to_xyz(np.radians(lat), np.radians(lon)), *grid.vertices_xyz[c])


def test_neighbours_distances_and_azimuths(grid):
    lat, lon = 31.5125, -9.77
    nbs = grid.neighbours(lat, lon, 4)
    assert len(nbs) in (4, 5)  # + la cellule contenant le site si elle n'est pas parmi les 4
    assert sum(nb.contains_site for nb in nbs) == 1
    d = [nb.distance_m for nb in nbs]
    assert d == sorted(d)
    for nb in nbs:
        dd, az = distance_azimuth(lat, lon, nb.lat, nb.lon)
        assert nb.distance_m == pytest.approx(dd)
        assert nb.azimuth_deg == pytest.approx(az)
        assert -180 <= nb.lon <= 180


def test_no_regular_grid_assumption(grid):
    # Les centres d'une grille icosaédrique ne sont pas alignés sur des parallèles réguliers.
    lats = np.round(np.degrees(grid.clat), 6)
    assert np.unique(lats).size > grid.ncells / 10


def test_save_load(grid, tmp_path):
    grid.save(tmp_path / "g.npz")
    g2 = IcosahedralGrid.load(tmp_path / "g.npz")
    a, _ = grid.nearest_indices(31.5, -9.77, 3)
    b, _ = g2.nearest_indices(31.5, -9.77, 3)
    assert list(a) == list(b)
    assert g2.containing_cell(31.5, -9.77) == grid.containing_cell(31.5, -9.77)


def test_fortran_indices_and_orientation():
    clat, clon, voc, vlat, vlon = synthetic_grid(2)
    g1 = IcosahedralGrid(clat, clon, voc, vlat, vlon)  # (3, n) base 1
    g2 = IcosahedralGrid(clat, clon, (voc - 1).T[:, ::-1] + 0, vlat, vlon)  # (n, 3) base 0, orientation inversée
    for lat, lon in [(31.5, -9.77), (-45.0, 120.0), (89.0, 0.0)]:
        assert g1.containing_cell(lat, lon) == g2.containing_cell(lat, lon)


@pytest.mark.slow
def test_real_icon_r3b07_grid():
    """Fichier DWD réel (GREENARD_TEST_ICON_GRID) : KD-tree = force brute sur 1000 sites."""
    path = os.environ.get("GREENARD_TEST_ICON_GRID")
    if not path or not Path(path).exists():
        pytest.skip("GREENARD_TEST_ICON_GRID non défini")
    import netCDF4

    with netCDF4.Dataset(path) as nc:
        g = IcosahedralGrid(nc["clat"][:], nc["clon"][:], nc["vertex_of_cell"][:], nc["vlat"][:], nc["vlon"][:])
    assert g.ncells == 2_949_120
    rng = np.random.default_rng(7)
    for _ in range(1000):
        lat, lon = float(rng.uniform(20, 37)), float(rng.uniform(-18, 0))
        idx, _ = g.nearest_indices(lat, lon, 1)
        assert idx[0] == _brute_force(g, lat, lon, 1)[0]
        assert g.containing_cell(lat, lon) is not None
