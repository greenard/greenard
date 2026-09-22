import io
import zipfile

import pytest

from app.core.errors import AppError
from app.farm import layout
from app.farm.turbines import TurbineSpec, parse_curve_csv, parse_wtg, validate
from app.terrain.rasters import parse_wasp_map
from tests.farmgen import generic_curve, layout_csv, wtg_xml


def test_parse_wtg_multi_density():
    spec = parse_wtg(wtg_xml(), "gen.wtg")
    assert spec.name == "GEN-4.2-136 (synthetic)" and spec.rotor_d_m == 136
    assert spec.hub_heights_m == [112.0]
    assert spec.rho_ref == 1.225 and len(spec.density_curves) == 2
    assert spec.cut_in_ms == 3 and spec.cut_out_ms == 25
    assert spec.rated_kw == pytest.approx(4200.0)  # W → kW
    issues = validate(spec)
    assert [i.code for i in issues] == ["TURBINE_RESTART_MISSING"]  # seul avertissement


def test_wtg_errors():
    with pytest.raises(AppError):
        parse_wtg(b"<notxml")
    with pytest.raises(AppError):
        parse_wtg(b"<Foo/>")


def test_curve_csv_and_validation():
    csv = "ws;power_kw;ct\n" + "\n".join(
        f"{p['ws']};{p['power_kw']};{p['ct']}".replace(".", ",") for p in generic_curve()
    )
    pc = parse_curve_csv(csv.encode())
    spec = TurbineSpec(
        name="x", rotor_d_m=136, hub_heights_m=[112], cut_in_ms=3, cut_out_ms=25, restart_ms=22, power_curve=pc
    )
    assert validate(spec) == []
    bad = TurbineSpec(
        name="x",
        rotor_d_m=500,
        cut_in_ms=10,
        cut_out_ms=5,
        restart_ms=30,
        power_curve=[{"ws": w, "power_kw": 10, "ct": 1.5} for w in (1, 2, 2, 3, 4)],
    )
    codes = {i.code for i in validate(bad)}
    assert {
        "TURBINE_ROTOR_INVALID",
        "TURBINE_WS_NOT_INCREASING",
        "TURBINE_CT_RANGE",
        "TURBINE_CUT_SPEEDS_INVALID",
        "TURBINE_RESTART_INVALID",
        "TURBINE_HUB_HEIGHTS_MISSING",
    } <= codes


TYPES = {"gen-4.2-136 (synthetic)": {"rotor_d_m": 136.0, "hub_heights_m": [112.0]}}


def test_layout_csv_utm_and_spacing():
    res = layout.parse_file("layout.csv", layout_csv(), None)
    layout.check_layout(res, TYPES)
    assert res.ok, res.issues
    assert len(res.rows) == 8 and res.rows[0].input_crs == "EPSG:32629"
    assert 31.4 < res.rows[0].lat < 31.6
    # espacement trop faible → avertissement (< 2 D) puis erreur (< 1 D)
    tight = layout.parse_file("l.csv", layout_csv(spacing_x=200, spacing_y=1500), None)
    layout.check_layout(tight, TYPES)
    assert tight.ok and any(i.code == "LAYOUT_SPACING_LOW" for i in tight.issues)
    overlap = layout.parse_file("l.csv", layout_csv(spacing_x=100, spacing_y=1500), None)
    layout.check_layout(overlap, TYPES)
    assert not overlap.ok and any(i.code == "LAYOUT_OVERLAP" for i in overlap.issues)


def test_layout_errors():
    data = b"id,x,y,type,hub_height\nA,1,2,GEN-4.2-136 (synthetic),112\nA,3,4,Unknown,90\nB,5,6,GEN-4.2-136 (synthetic),900\n"
    res = layout.parse_file("l.csv", data, None)
    assert {i.code for i in res.issues} == {"IMPORT_MISSING_CRS"}
    res = layout.parse_file("l.csv", data, "UTM29N")
    layout.check_layout(res, TYPES)
    codes = {i.code for i in res.issues}
    assert {"IMPORT_DUPLICATE_NAME", "LAYOUT_TYPE_UNKNOWN", "LAYOUT_HUB_INVALID"} <= codes


def test_layout_kml_and_xlsx():
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
    <Placemark><name>T1</name><ExtendedData><Data name="type"><value>GEN-4.2-136 (synthetic)</value></Data></ExtendedData>
    <Point><coordinates>-9.70,31.45,0</coordinates></Point></Placemark></Document></kml>"""
    res = layout.parse_file("l.kml", kml, None)
    layout.check_layout(res, TYPES)
    assert res.ok and res.rows[0].hub_height_m == 112.0  # hauteur par défaut du type (avertissement)
    import pandas as pd

    buf = io.BytesIO()
    pd.DataFrame(
        {"ID": ["T1"], "Latitude": [31.45], "Longitude": [-9.7], "Type": ["GEN-4.2-136 (synthetic)"], "HH": [112]}
    ).to_excel(buf, index=False)
    res = layout.parse_file("l.xlsx", buf.getvalue(), None)
    assert res.ok and res.rows[0].lon == -9.7


def test_layout_shapefile(tmp_path):
    from osgeo import ogr, osr

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(32629)
    drv = ogr.GetDriverByName("ESRI Shapefile")
    ds = drv.CreateDataSource(str(tmp_path / "wtg.shp"))
    lyr = ds.CreateLayer("wtg", srs, ogr.wkbPoint)
    for name in ("id", "type"):
        lyr.CreateField(ogr.FieldDefn(name, ogr.OFTString))
    lyr.CreateField(ogr.FieldDefn("hub", ogr.OFTReal))
    for k, x in enumerate((426000, 426700)):
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("id", f"T{k}")
        f.SetField("type", "GEN-4.2-136 (synthetic)")
        f.SetField("hub", 112.0)
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint(x, 3483000)
        f.SetGeometry(g)
        lyr.CreateFeature(f)
    ds = None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in tmp_path.glob("wtg.*"):
            z.write(p, f"sub/{p.name}")
    res = layout.parse_file("wtg.zip", buf.getvalue(), None)
    layout.check_layout(res, TYPES)
    assert res.ok, res.issues
    assert res.rows[0].input_crs == "EPSG:32629" and res.rows[1].x == 426700


def test_wasp_map_parser():
    text = """Test map
 0.0 0.0 0.0 0.0
 1.0 0.0 1.0 0.0
 1.0 0.0
 0.03 0.5 3
 426000 3483000 426500 3483500
 427000 3484000
 100 2
 426000 3480000 428000 3480000
"""
    m = parse_wasp_map(text.encode())
    assert m["roughness_lines"][0]["z0_right"] == 0.5 and len(m["roughness_lines"][0]["coords"]) == 3
    assert m["contour_lines"][0]["height"] == 100
