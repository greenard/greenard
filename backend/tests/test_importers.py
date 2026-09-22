import pytest

from app.sites.importers import parse_csv, parse_file, parse_kml

KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>Essaouira</name><Point><coordinates>-9.77,31.5125,0</coordinates></Point></Placemark>
<Placemark><name>Dakhla</name><Point><coordinates>-15.958,23.6848</coordinates></Point></Placemark>
<Placemark><name>Zone</name><Polygon/></Placemark>
</Document></kml>"""


def test_csv_latlon_semicolon_french_decimal_and_dms():
    data = "nom;latitude;longitude\nA;31,5125;-9,77\nB;33°35'12.3\"N;7°36'W\n".encode("cp1252")
    r = parse_csv(data)
    assert r.ok, r.errors
    assert [s.name for s in r.sites] == ["A", "B"]
    assert r.sites[0].lat == pytest.approx(31.5125)
    assert r.sites[1].lon == pytest.approx(-7.6)


def test_csv_projected_mixed_crs():
    data = b"name,x,y,crs\nU,426886.197,3486658.868,UTM29N\nL,84821.02,110799.39,EPSG:26191\n"
    r = parse_csv(data)
    assert r.ok, r.errors
    for s in r.sites:
        assert s.lat == pytest.approx(31.5125, abs=1e-5)
        assert s.lon == pytest.approx(-9.77, abs=1e-5)
    assert r.sites[1].input_crs == "EPSG:26191"


def test_csv_row_errors_are_reported_per_row():
    data = b"name,lat,lon,x,y,crs\nok,31.5,-9.7,,,\n,31,-9,,,\nbadlat,95,-9,,,\nnocrs,,,1000,2000,\nok,32,-9,,,\n"
    r = parse_csv(data)
    assert not r.ok
    codes = {(e.row, e.code) for e in r.errors}
    assert (3, "IMPORT_EMPTY_NAME") in codes
    assert (4, "COORD_OUT_OF_RANGE") in codes
    assert (5, "IMPORT_MISSING_CRS") in codes
    assert (6, "IMPORT_DUPLICATE_NAME") in codes
    assert [s.name for s in r.sites] == ["ok"]


def test_csv_missing_columns():
    r = parse_csv(b"site,foo\nA,1\n")
    assert [e.code for e in r.errors] == ["IMPORT_MISSING_COORD_COLUMNS"]


def test_csv_ballpark_warning_is_not_blocking():
    data = b"name,x,y,crs\nD,425084.02,570731.63,EPSG:26195\n"
    r = parse_csv(data)
    assert r.ok
    assert [e.code for e in r.errors] == ["MERCHICH_BALLPARK"]
    assert r.errors[0].severity == "warning"


def test_kml():
    r = parse_kml(KML)
    assert r.ok
    assert [s.name for s in r.sites] == ["Essaouira", "Dakhla"]
    assert r.sites[1].lat == pytest.approx(23.6848)
    assert [e.code for e in r.errors] == ["IMPORT_KML_NOT_POINT"]


def test_kml_rejects_external_entities():
    evil = b"""<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/passwd">]>
<kml><Placemark><name>&x;</name><Point><coordinates>0,0</coordinates></Point></Placemark></kml>"""
    from defusedxml import DTDForbidden, EntitiesForbidden

    with pytest.raises((EntitiesForbidden, DTDForbidden)):
        parse_kml(evil)


def test_unsupported_format():
    from app.core.errors import AppError

    with pytest.raises(AppError):
        parse_file("sites.xlsx", b"")
