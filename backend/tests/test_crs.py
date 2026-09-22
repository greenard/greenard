import math

import pytest

from app.geo.crs import (
    CoordinateError,
    format_dms,
    from_wgs84,
    lambert_zones_for,
    normalize_crs,
    parse_angle,
    representations,
    to_wgs84,
    utm_epsg,
    utm_zone,
)

ESSAOUIRA = (31.5125, -9.77)
CASABLANCA = (33.5731, -7.5898)
OUJDA = (34.6814, -1.9086)
DAKHLA = (23.6848, -15.9580)
LAAYOUNE = (27.1253, -13.1625)


@pytest.mark.parametrize(
    "text,kind,expected",
    [
        ("-7.6", "lon", -7.6),
        ("-7,6", "lon", -7.6),
        ("33°35'12.3\"N", "lat", 33 + 35 / 60 + 12.3 / 3600),
        ("33 35 12.3 N", "lat", 33 + 35 / 60 + 12.3 / 3600),
        ("N33°35.2'", "lat", 33 + 35.2 / 60),
        ("7°36'W", "lon", -7.6),
        ("7d36m0s O", "lon", -7.6),  # O = Ouest
        ("E 5 30", "lon", 5.5),
        ("33°35'12,3\" n", "lat", 33 + 35 / 60 + 12.3 / 3600),
        (12.5, "lat", 12.5),
    ],
)
def test_parse_angle(text, kind, expected):
    assert parse_angle(text, kind) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize(
    "text,kind,code",
    [
        ("33°75'N", "lat", "COORD_MINUTES_SECONDS_RANGE"),
        ("91", "lat", "COORD_OUT_OF_RANGE"),
        ("181", "lon", "COORD_OUT_OF_RANGE"),
        ("abc", "lat", "COORD_UNPARSABLE"),
        ("-33 N", "lat", "COORD_SIGN_AND_HEMISPHERE"),
        ("33 E", "lat", "COORD_WRONG_HEMISPHERE"),
        ("7 N", "lon", "COORD_WRONG_HEMISPHERE"),
        ("", "lat", "COORD_EMPTY"),
        ("33.5°10'", "lat", "COORD_UNPARSABLE"),
    ],
)
def test_parse_angle_errors(text, kind, code):
    with pytest.raises(CoordinateError) as e:
        parse_angle(text, kind)
    assert e.value.code == code


def test_dms_round_trip():
    for v in (31.5125, -9.77, 0.0, -0.000139, 89.99999):
        kind = "lat"
        assert parse_angle(format_dms(v, kind, decimals=4), kind) == pytest.approx(v, abs=1e-7)
    assert format_dms(-9.77, "lon") == "9°46'12.00\"W"


@pytest.mark.parametrize(
    "site,zone",
    [(DAKHLA, 28), (LAAYOUNE, 28), (ESSAOUIRA, 29), (CASABLANCA, 29), (OUJDA, 30)],
)
def test_utm_zone_morocco(site, zone):
    assert utm_zone(*site) == (zone, "N")


def test_utm_zone_boundaries():
    assert utm_zone(30.0, -12.0) == (29, "N")  # la limite ouest appartient à la zone suivante
    assert utm_zone(30.0, -12.000001) == (28, "N")
    assert utm_zone(30.0, -6.0) == (30, "N")
    assert utm_zone(30.0, -6.000001) == (29, "N")
    assert utm_zone(-10.0, 3.0) == (31, "S")
    assert utm_zone(60.0, 5.0) == (32, "N")  # exception Norvège
    assert utm_zone(10.0, 180.0) == (1, "N")


def test_utm_central_meridian_and_equator():
    # Méridien central de la zone 29 : -9° → X = 500 000 m exactement ; équateur → Y = 0.
    x, y, _ = from_wgs84(0.0, -9.0, "EPSG:32629")
    assert x == pytest.approx(500_000.0, abs=1e-6)
    assert y == pytest.approx(0.0, abs=1e-6)
    # Sur le méridien central, Y = k0 · longueur d'arc méridien (1° ≈ 110 574 m à l'équateur).
    _, y1, _ = from_wgs84(1.0, -9.0, "EPSG:32629")
    assert y1 == pytest.approx(0.9996 * 110_574.4, abs=1.0)


@pytest.mark.parametrize("site", [ESSAOUIRA, CASABLANCA, OUJDA, DAKHLA])
def test_utm_round_trip(site):
    zone, hemi = utm_zone(*site)
    x, y, _ = from_wgs84(*site, utm_epsg(zone, hemi))
    lat, lon, _ = to_wgs84(x, y, f"UTM{zone}{hemi}")
    assert lat == pytest.approx(site[0], abs=1e-9)
    assert lon == pytest.approx(site[1], abs=1e-9)


def test_normalize_crs():
    assert normalize_crs("UTM29N") == "EPSG:32629"
    assert normalize_crs("utm 28 n") == "EPSG:32628"
    assert normalize_crs("32630") == "EPSG:32630"
    assert normalize_crs("Lambert Nord") == "EPSG:26191"
    assert normalize_crs("wgs84") == "EPSG:4326"
    with pytest.raises(CoordinateError):
        normalize_crs("foo")


def test_lambert_zones():
    assert lambert_zones_for(*CASABLANCA) == ["EPSG:26191"]
    assert lambert_zones_for(*DAKHLA) == ["EPSG:26195"]


def test_lambert_nord_round_trip_and_datum_shift():
    x, y, info = from_wgs84(*CASABLANCA, "EPSG:26191")
    assert "Merchich to WGS 84" in info.description
    assert info.accuracy_m == pytest.approx(7.0)
    assert info.warnings == []
    lat, lon, _ = to_wgs84(x, y, "EPSG:26191")
    # aller-retour à ~1 cm près (changement de datum géocentrique)
    assert lat == pytest.approx(CASABLANCA[0], abs=1e-7)
    assert lon == pytest.approx(CASABLANCA[1], abs=1e-7)
    # Plausibilité : origine Nord Maroc à -5,4° (6 grades), fausse abscisse 500 km.
    assert 270_000 < x < 320_000


def test_merchich_sahara_sud_is_flagged_ballpark():
    _, _, info = from_wgs84(*DAKHLA, "EPSG:26195")
    assert info.ballpark
    assert "MERCHICH_BALLPARK" in info.warnings


def test_representations():
    r = representations(*ESSAOUIRA)
    assert r["utm"]["zone"] == 29
    assert r["dms"]["lat"].endswith("N")
    assert [lz["crs"] for lz in r["lambert"]] == ["EPSG:26191"]
    assert all(math.isfinite(v) for v in (r["utm"]["x"], r["utm"]["y"]))
