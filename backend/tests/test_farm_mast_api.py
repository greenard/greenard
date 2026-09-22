"""API du jalon 3 : types d'éoliennes, layout, mât (aperçu → import → QC → analyses), terrain."""

import io
import json
import os

import numpy as np
import pytest

pytestmark = pytest.mark.db

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("TEST_DATABASE_URL non défini", allow_module_level=True)

import rasterio  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

from app.terrain import rasters  # noqa: E402
from tests.farmgen import layout_csv, wtg_xml  # noqa: E402
from tests.mastgen import generate, to_toa5  # noqa: E402
from tests.test_api import _project, clean, client, fake_terrain_and_invariants, login, schema  # noqa: E402, F401


def _write_tif(path, arr, lon0, lat0, res, dtype, nodata=None):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=arr.shape[1],
        height=arr.shape[0],
        count=1,
        dtype=dtype,
        crs="EPSG:4326",
        transform=from_origin(lon0, lat0, res, res),
        nodata=nodata,
    ) as dst:
        dst.write(arr.astype(dtype), 1)


@pytest.fixture(autouse=True)
def fake_rasters(monkeypatch):
    def dem(bbox, out):
        y, x = np.mgrid[0:200, 0:200]
        _write_tif(out, 50 + 2 * x + np.sin(y / 10) * 30, bbox[0], bbox[3], (bbox[2] - bbox[0]) / 200, "float32")
        return {"min_m": 20.0, "max_m": 480.0}

    def landcover(bbox, out):
        arr = np.full((300, 300), 30, dtype="uint8")  # prairie
        arr[:, 150:] = 60  # sol nu à l'est
        arr[:20, :] = 80  # eau au nord
        _write_tif(out, arr, bbox[0], bbox[3], (bbox[2] - bbox[0]) / 300, "uint8", nodata=0)
        return {"classes_pct": {"30": 50.0, "60": 43.3, "80": 6.7}}

    monkeypatch.setattr(rasters, "download_dem", dem)
    monkeypatch.setattr(rasters, "download_landcover", landcover)


def _type(client, h, pid):
    r = client.post(
        f"/api/v1/projects/{pid}/turbine-types/import",
        files={"file": ("gen.wtg", wtg_xml())},
        data={"meta": json.dumps({"restart_ms": 22})},
        headers=h,
    )
    assert r.status_code == 200 and r.json()["ok"], r.text
    return r.json()["created"]


def test_turbine_types_and_layout(client):
    h = login(client, "eng@example.org")
    pid = _project(client, h)
    r = client.post(
        f"/api/v1/projects/{pid}/turbine-types/import?dry_run=true", files={"file": ("gen.wtg", wtg_xml())}, headers=h
    )
    assert r.json()["ok"] and r.json()["created"] is None
    assert [i["code"] for i in r.json()["issues"]] == ["TURBINE_RESTART_MISSING"]
    tid = _type(client, h, pid)
    r = client.post(f"/api/v1/projects/{pid}/turbine-types/import", files={"file": ("gen.wtg", wtg_xml())}, headers=h)
    assert any(i["code"] == "TURBINE_NAME_EXISTS" for i in r.json()["issues"])
    r = client.patch(f"/api/v1/turbine-types/{tid}", json={"restart_ms": 30}, headers=h)
    assert r.json()["error"]["code"] == "TURBINE_INVALID"
    farm = client.post(f"/api/v1/projects/{pid}/farms", json={"name": "Parc A"}, headers=h).json()
    r = client.post(
        f"/api/v1/farms/{farm['id']}/layout/import?dry_run=true", files={"file": ("l.csv", layout_csv())}, headers=h
    )
    assert r.json()["ok"] and len(r.json()["rows"]) == 8 and r.json()["created"] == 0
    r = client.post(f"/api/v1/farms/{farm['id']}/layout/import", files={"file": ("l.csv", layout_csv())}, headers=h)
    assert r.json()["created"] == 8
    farms = client.get(f"/api/v1/projects/{pid}/farms", headers=h).json()
    assert farms[0]["n_turbines"] == 8 and farms[0]["capacity_mw"] == pytest.approx(33.6)
    assert farms[0]["spacing"]["min_d"] == pytest.approx(5.0, abs=0.05)
    assert farms[0]["turbines"][0]["dem_elevation_m"] is not None
    # type utilisé : suppression refusée
    assert client.delete(f"/api/v1/turbine-types/{tid}", headers=h).status_code == 409
    # le lecteur ne peut pas importer
    client.put(f"/api/v1/projects/{pid}/members", json={"email": "view@example.org", "role": "viewer"}, headers=h)
    hv = login(client, "view@example.org")
    r = client.post(f"/api/v1/farms/{farm['id']}/layout/import", files={"file": ("l.csv", layout_csv())}, headers=hv)
    assert r.status_code == 403


def test_mast_import_qc_analysis_and_terrain(client):
    h = login(client, "eng@example.org")
    pid = _project(client, h)
    mast = client.post(
        f"/api/v1/projects/{pid}/masts", json={"name": "M1", "x": 426500, "y": 3483500, "crs": "UTM29N"}, headers=h
    ).json()
    df, truth = generate(days=60)
    pv = client.post(
        f"/api/v1/masts/{mast['id']}/data/preview", files={"file": ("mast.dat", to_toa5(df).encode())}, headers=h
    ).json()
    assert pv["format"] == "toa5" and pv["default_convention"] == "end"
    mapping = pv["suggested_mapping"]
    for m in mapping:
        if m["kind"] == "pressure":
            m["height_m"] = 2
    r = client.post(
        f"/api/v1/masts/{mast['id']}/data/import",
        json={"token": pv["token"], "mapping": mapping, "timezone": "UTC", "convention": "end"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    rep = r.json()["report"]
    assert rep["missing_records_inserted"] == truth["gap_records"] and rep["interval_min"] == 10
    dsid = r.json()["dataset_id"]
    # le jeton est consommé
    r2 = client.post(
        f"/api/v1/masts/{mast['id']}/data/import", json={"token": pv["token"], "mapping": mapping}, headers=h
    )
    assert r2.json()["error"]["code"] == "MAST_UPLOAD_EXPIRED"
    masts = client.get(f"/api/v1/projects/{pid}/masts", headers=h).json()
    assert {s["code"] for s in masts[0]["sensors"]} >= {"ws_80_n", "ws_80_s", "wd_78"}
    qcr = client.get(f"/api/v1/mast-datasets/{dsid}/qc", headers=h).json()
    assert any(s["flags"]["icing"] > 0 for s in qcr["sensors"])
    s = client.get(f"/api/v1/mast-datasets/{dsid}/series?start=2025-01-10&end=2025-01-12", headers=h).json()
    assert s["resample"] == "10min" and "flags" in s["sensors"]["ws_80_n"] and "ws@80" in s["composites"]
    s = client.get(f"/api/v1/mast-datasets/{dsid}/series", headers=h).json()
    assert s["resample"] == "1h" and "wd@78" not in s["composites"]
    # terrain : MNT et occupation du sol → rugosité
    for kind in ("dem", "landcover"):
        t = client.post(
            f"/api/v1/projects/{pid}/terrain/download", json={"kind": kind, "margin_km": 3}, headers=h
        ).json()
        task = client.get(f"/api/v1/tasks/{t['id']}", headers=h).json()
        assert task["status"] == "success", task
    layers = client.get(f"/api/v1/projects/{pid}/terrain", headers=h).json()["layers"]
    assert [x["kind"] for x in layers] == ["dem", "landcover", "roughness"]
    png = client.get(f"/api/v1/terrain/{layers[0]['id']}/preview.png", headers=h)
    assert png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    assert len(json.loads(png.headers["x-bounds"])) == 4
    an = client.get(f"/api/v1/mast-datasets/{dsid}/analysis", headers=h).json()
    assert an["shear"]["alpha_all"] == pytest.approx(0.14, abs=0.015)
    z0 = {r["sector_deg"]: r["z0_m"] for r in an["z0_by_sector"]}
    assert z0[90.0] < z0[270.0]  # sol nu à l'est, prairie à l'ouest
    # table z0 éditable
    table = rasters.DEFAULT_Z0 | {"30": {"label": "Grassland", "z0": 0.1}}
    r = client.put(f"/api/v1/terrain/{layers[1]['id']}/z0-table", json={"table": table}, headers=h)
    assert r.status_code == 200
    an2 = client.get(f"/api/v1/mast-datasets/{dsid}/analysis", headers=h).json()
    z0b = {x["sector_deg"]: x["z0_m"] for x in an2["z0_by_sector"]}
    assert z0b[270.0] > z0[270.0]
    bad = client.put(f"/api/v1/terrain/{layers[1]['id']}/z0-table", json={"table": {"30": {"z0": -1}}}, headers=h)
    assert bad.json()["error"]["code"] == "TERRAIN_Z0_INVALID"


def test_wasp_map_and_dem_upload(client):
    h = login(client, "eng@example.org")
    pid = _project(client, h)
    text = "t\n0 0 0 0\n1 0 1 0\n1 0\n0.03 0.5 2\n426000 3483000 427000 3484000\n"
    r = client.post(
        f"/api/v1/projects/{pid}/terrain/wasp-map",
        files={"file": ("a.map", text.encode())},
        data={"crs": "UTM29N"},
        headers=h,
    )
    assert r.status_code == 201 and r.json()["stats"]["z0_values"] == [0.03, 0.5]
    gj = client.get(f"/api/v1/terrain/{r.json()['id']}/geojson", headers=h).json()
    assert gj["features"][0]["geometry"]["coordinates"][0][0] == pytest.approx(-9.78, abs=0.05)
    # MNT importé en UTM : reprojeté en WGS84
    buf = io.BytesIO()
    with rasterio.MemoryFile() as mem:
        with mem.open(
            driver="GTiff",
            width=50,
            height=50,
            count=1,
            dtype="float32",
            crs="EPSG:32629",
            transform=from_origin(426000, 3484000, 30, 30),
        ) as dst:
            dst.write(np.full((50, 50), 120.0, dtype="float32"), 1)
        buf.write(mem.read())
    r = client.post(
        f"/api/v1/projects/{pid}/terrain/upload-dem", files={"file": ("dem.tif", buf.getvalue())}, headers=h
    )
    assert r.status_code == 201 and r.json()["crs"] == "EPSG:4326"
    assert r.json()["stats"]["mean_m"] == pytest.approx(120.0)
