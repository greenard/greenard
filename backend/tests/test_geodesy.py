import numpy as np
import pytest

from app.geo.geodesy import distance_azimuth


def test_cardinal_azimuths():
    _, az = distance_azimuth(31.0, -9.0, 31.1, -9.0)
    assert az == pytest.approx(0.0, abs=1e-9)
    _, az = distance_azimuth(31.0, -9.0, 31.0, -8.9)
    assert az == pytest.approx(90.0, abs=0.1)
    _, az = distance_azimuth(31.0, -9.0, 30.9, -9.0)
    assert az == pytest.approx(180.0, abs=1e-9)
    _, az = distance_azimuth(31.0, -9.0, 31.0, -9.1)
    assert az == pytest.approx(270.0, abs=0.1)


def test_meridian_degree_length():
    # 1° de latitude autour de 31°N ≈ 110,86 km (ellipsoïde WGS84)
    d, _ = distance_azimuth(30.5, -9.0, 31.5, -9.0)
    assert d == pytest.approx(110_860, rel=2e-4)


def test_vectorised():
    d, az = distance_azimuth(31.0, -9.0, np.array([31.25, 31.0]), np.array([-9.0, -8.75]))
    assert d.shape == (2,) and az.shape == (2,)
