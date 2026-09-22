import eccodes
import numpy as np
import pytest

from app.nwp.grids.regular import RegularGrid
from app.nwp.invariants import RegularInvariants, crop_regular
from app.nwp.invariants.fetch import decode_grib, parse_wgrib_idx, regular_from_message, wgrib_ranges


def _make_grib(values: np.ndarray, lat_first, lon_first, dlat, dlon, j_pos: int) -> bytes:
    gid = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        nj, ni = values.shape
        eccodes.codes_set(gid, "Ni", ni)
        eccodes.codes_set(gid, "Nj", nj)
        eccodes.codes_set(gid, "jScansPositively", j_pos)
        eccodes.codes_set(gid, "latitudeOfFirstGridPointInDegrees", lat_first)
        eccodes.codes_set(gid, "longitudeOfFirstGridPointInDegrees", lon_first)
        eccodes.codes_set(gid, "latitudeOfLastGridPointInDegrees", lat_first + (nj - 1) * dlat * (1 if j_pos else -1))
        eccodes.codes_set(gid, "longitudeOfLastGridPointInDegrees", lon_first + (ni - 1) * dlon)
        eccodes.codes_set(gid, "iDirectionIncrementInDegrees", dlon)
        eccodes.codes_set(gid, "jDirectionIncrementInDegrees", dlat)
        eccodes.codes_set_values(gid, values.ravel())
        return eccodes.codes_get_message(gid)
    finally:
        eccodes.codes_release(gid)


def test_decode_regular_grib_north_to_south():
    vals = np.arange(12, dtype=float).reshape(3, 4)  # 3 latitudes × 4 longitudes
    msg = decode_grib(_make_grib(vals, 32.0, 350.0, 0.25, 0.25, 0))[0]
    grid, arr = regular_from_message(msg)
    assert grid.lat_first == 32.0 and grid.dlat == -0.25 and grid.nlat == 3
    assert grid.lon_first == 350.0 and grid.dlon == 0.25 and grid.nlon == 4
    assert not grid.global_lon
    np.testing.assert_allclose(arr, vals, atol=1e-3)
    assert float(grid.lat_of(2)) == 31.5 and float(grid.lon_of(0)) == -10.0


def test_decode_regular_grib_south_to_north():
    vals = np.arange(6, dtype=float).reshape(2, 3)
    grid, arr = regular_from_message(decode_grib(_make_grib(vals, 29.5, -23.5, 0.0625, 0.0625, 1))[0])
    assert grid.dlat == 0.0625 and grid.lat_first == 29.5
    np.testing.assert_allclose(arr, vals, atol=1e-3)


def test_wgrib_idx_ranges():
    idx = (
        "1:0:d=2026092100:PRMSL:mean sea level:anl:\n2:100:d=2026092100:HGT:surface:anl:\n"
        "3:250:d=2026092100:LAND:surface:anl:\n"
    )
    rows = parse_wgrib_idx(idx)
    assert wgrib_ranges(rows, [("HGT", "surface"), ("LAND", "surface")]) == [(100, 249), (250, None)]


def test_crop_and_lookup_global_wrap():
    grid = RegularGrid(90.0, -0.25, 721, 0.0, 0.25, 1440, global_lon=True)
    ii, jj = np.meshgrid(np.arange(721), np.arange(1440), indexing="ij")
    elev = (ii * 10000 + jj).astype(float)  # valeur encodant (i, j)
    land = np.ones_like(elev)
    inv = crop_regular(grid, elev, land, (19.0, 37.5, -19.5, 0.5))
    # Essaouira : i=234, j=1400 ; point à l'est de Greenwich : j=1
    for i, j in [(234, 1400), (230, 1), (280, 1362)]:  # 280 → 20°N
        e, f = inv.lookup(i, j)
        assert e == i * 10000 + j and f == 1.0
    assert inv.lookup(0, 0) == (None, None)


def test_regular_invariants_save_load(tmp_path):
    grid = RegularGrid(29.5, 0.0625, 657, -23.5, 0.0625, 1377)
    elev = np.random.default_rng(0).uniform(0, 3000, (657, 1377))
    inv = crop_regular(grid, elev, np.zeros_like(elev), (19.0, 37.5, -19.5, 0.5))
    inv.save(tmp_path / "x.nc")
    inv2 = RegularInvariants.load(tmp_path / "x.nc")
    assert inv2.grid == grid
    i, j = 40, 230
    assert inv2.lookup(i, j)[0] == pytest.approx(elev[i, j], rel=1e-6)
