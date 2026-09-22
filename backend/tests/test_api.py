"""Tests d'API (PostgreSQL/PostGIS requis : TEST_DATABASE_URL)."""

import os

import numpy as np
import pytest

pytestmark = pytest.mark.db

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("TEST_DATABASE_URL non défini", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.models import Base, User  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.forecasts import governance  # noqa: E402
from app.main import app  # noqa: E402
from app.nwp import invariants  # noqa: E402
from app.nwp.catalog import MODELS  # noqa: E402
from app.nwp.grids.icosahedral import IcosahedralGrid  # noqa: E402
from app.nwp.invariants import IconInvariants, crop_regular, write_meta  # noqa: E402
from app.terrain import dem  # noqa: E402
from tests.icogrid import synthetic_grid  # noqa: E402

PWD = "correct-horse-battery"


@pytest.fixture(scope="module", autouse=True)
def schema():
    with engine.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def clean():
    with engine.begin() as c:
        c.execute(
            text(
                "TRUNCATE app_user, project, grid_point, task_log, audit_log, nwp_run, data_source "
                "RESTART IDENTITY CASCADE"
            )
        )
    with SessionLocal() as db:
        governance.seed(db)
        db.add_all(
            [
                User(email="admin@example.org", password_hash=hash_password(PWD), is_admin=True),
                User(email="eng@example.org", password_hash=hash_password(PWD)),
                User(email="view@example.org", password_hash=hash_password(PWD)),
                User(email="other@example.org", password_hash=hash_password(PWD)),
            ]
        )
        db.commit()


@pytest.fixture(autouse=True)
def fake_terrain_and_invariants(monkeypatch):
    """MNT et invariants synthétiques : pas d'accès réseau dans les tests d'API."""
    monkeypatch.setattr(dem, "sample", lambda lat, lon: dem.DemSample(100.0 + lat, False))
    monkeypatch.setattr(dem, "box_mean", lambda *a, **k: 123.0)
    g = MODELS["gfs"].regular_grid
    ii, _ = np.meshgrid(np.arange(g.nlat), np.arange(g.nlon), indexing="ij")
    elev = np.where(ii < 234, 400.0, 20.0)
    # terre à l'est de -10° (j > 1400), mer sur la colonne -10° (j = 1400)
    land = np.where(np.arange(g.nlon)[None, :] >= 1401, 1.0, 0.0) * np.ones((g.nlat, 1))
    crop_regular(g, elev, land, (19.0, 37.5, -19.5, 0.5)).save(invariants.cache_path("gfs"))
    write_meta("gfs", source="synthetic")
    clat, clon, voc, vlat, vlon = synthetic_grid(5)
    grid = IcosahedralGrid(clat, clon, voc, vlat, vlon)
    IconInvariants(grid, np.full(grid.ncells, 55.0), np.ones(grid.ncells)).save(invariants.cache_path("icon"))
    write_meta("icon", source="synthetic")
    yield


@pytest.fixture
def client():
    return TestClient(app)


def login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PWD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---- authentification ---------------------------------------------------------------------------


def test_requires_auth(client):
    r = client.get("/api/v1/projects")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_REQUIRED"


def test_login_refresh_logout(client):
    r = client.post("/api/v1/auth/login", json={"email": "eng@example.org", "password": PWD})
    assert r.status_code == 200
    assert "greenard_refresh" in r.cookies
    # sans en-tête anti-CSRF
    assert client.post("/api/v1/auth/refresh").json()["error"]["code"] == "AUTH_CSRF"
    r2 = client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "greenard"})
    assert r2.status_code == 200 and r2.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r2.json()['access_token']}"})
    assert me.json()["email"] == "eng@example.org"
    client.post("/api/v1/auth/logout")
    r3 = client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "greenard"})
    assert r3.status_code == 401


def test_refresh_token_rotation(client):
    client.post("/api/v1/auth/login", json={"email": "eng@example.org", "password": PWD})
    old = client.cookies.get("greenard_refresh")
    client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "greenard"})
    client.cookies.set("greenard_refresh", old, path="/api/v1/auth")
    r = client.post("/api/v1/auth/refresh", headers={"X-Requested-With": "greenard"})
    assert r.json()["error"]["code"] == "AUTH_SESSION_EXPIRED"


def test_lockout_after_failed_logins(client):
    for _ in range(5):
        r = client.post("/api/v1/auth/login", json={"email": "eng@example.org", "password": "wrong-password"})
        assert r.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    r = client.post("/api/v1/auth/login", json={"email": "eng@example.org", "password": PWD})
    assert r.json()["error"]["code"] == "AUTH_LOCKED"


def test_admin_user_management(client):
    h = login(client, "admin@example.org")
    r = client.post("/api/v1/users", json={"email": "New@Example.org", "password": "short"}, headers=h)
    assert r.json()["error"]["code"] == "PASSWORD_TOO_SHORT"
    r = client.post("/api/v1/users", json={"email": "New@Example.org", "password": PWD, "locale": "en"}, headers=h)
    assert r.status_code == 201 and r.json()["email"] == "new@example.org"
    h2 = login(client, "eng@example.org")
    assert client.get("/api/v1/users", headers=h2).status_code == 403
    uid = r.json()["id"]
    client.patch(f"/api/v1/users/{uid}", json={"is_active": False}, headers=h)
    r = client.post("/api/v1/auth/login", json={"email": "new@example.org", "password": PWD})
    assert r.status_code == 401


# ---- projets et rôles ---------------------------------------------------------------------------


def _project(client, h, name="Parc test"):
    r = client.post("/api/v1/projects", json={"name": name}, headers=h)
    assert r.status_code == 201
    return r.json()["id"]


def test_project_roles(client):
    he = login(client, "eng@example.org")
    pid = _project(client, he)
    assert (
        client.put(
            f"/api/v1/projects/{pid}/members", json={"email": "view@example.org", "role": "viewer"}, headers=he
        ).status_code
        == 200
    )
    hv = login(client, "view@example.org")
    ho = login(client, "other@example.org")
    # viewer : lecture seule
    assert client.get(f"/api/v1/projects/{pid}/sites", headers=hv).status_code == 200
    r = client.post(f"/api/v1/projects/{pid}/sites", json={"name": "S", "lat": 31.5, "lon": -9.7}, headers=hv)
    assert r.status_code == 403 and r.json()["error"]["code"] == "PROJECT_ROLE_INSUFFICIENT"
    # non-membre : le projet est invisible
    assert client.get(f"/api/v1/projects/{pid}", headers=ho).status_code == 404
    assert [p["id"] for p in client.get("/api/v1/projects", headers=ho).json()] == []
    # le dernier owner ne peut pas être retiré
    me = client.get("/api/v1/auth/me", headers=he).json()
    r = client.delete(f"/api/v1/projects/{pid}/members/{me['id']}", headers=he)
    assert r.json()["error"]["code"] == "PROJECT_LAST_OWNER"
    # l'admin voit tout
    ha = login(client, "admin@example.org")
    assert client.get(f"/api/v1/projects/{pid}", headers=ha).status_code == 200


# ---- sites et conversions -----------------------------------------------------------------------


def test_convert(client):
    h = login(client, "eng@example.org")
    r = client.post("/api/v1/geo/convert", json={"lat": "31°30'45\"N", "lon": "9°46'12\"W"}, headers=h).json()
    assert r["utm"]["zone"] == 29
    assert r["lambert"][0]["crs"] == "EPSG:26191"
    r = client.post(
        "/api/v1/geo/convert", json={"x": r["utm"]["x"], "y": r["utm"]["y"], "crs": "UTM29N"}, headers=h
    ).json()
    assert r["wgs84"]["lat"] == pytest.approx(31.5125, abs=1e-8)
    r = client.post("/api/v1/geo/convert", json={"lat": "95", "lon": "0"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "COORD_OUT_OF_RANGE"


def test_site_crud_and_import(client):
    h = login(client, "eng@example.org")
    pid = _project(client, h)
    r = client.post(
        f"/api/v1/projects/{pid}/sites",
        json={"name": "Essaouira", "x": 426886.197, "y": 3486658.868, "crs": "UTM29N"},
        headers=h,
    )
    assert r.status_code == 201
    assert r.json()["lat"] == pytest.approx(31.5125, abs=1e-6)
    assert r.json()["input_crs"] == "EPSG:32629"
    csv = b"name,lat,lon\nEssaouira,31.5,-9.7\nTan-Tan,28.43,-11.1\n"
    r = client.post(f"/api/v1/projects/{pid}/sites/import", files={"file": ("s.csv", csv, "text/csv")}, headers=h)
    body = r.json()
    assert not body["ok"] and body["errors"][0]["code"] == "IMPORT_NAME_EXISTS"
    csv = b"name,lat,lon\nTan-Tan,28.43,-11.1\nGuelmim,28.98,-10.05\n"
    r = client.post(f"/api/v1/projects/{pid}/sites/import?dry_run=true", files={"file": ("s.csv", csv)}, headers=h)
    assert r.json()["ok"] and r.json()["created"] == []
    r = client.post(f"/api/v1/projects/{pid}/sites/import", files={"file": ("s.csv", csv)}, headers=h)
    assert len(r.json()["created"]) == 2
    assert len(client.get(f"/api/v1/projects/{pid}/sites", headers=h).json()) == 3


# ---- points de grille ---------------------------------------------------------------------------


def _site(client, h, pid, name, lat, lon):
    r = client.post(f"/api/v1/projects/{pid}/sites", json={"name": name, "lat": lat, "lon": lon}, headers=h)
    return r.json()["id"]


def test_grid_points_gfs_icon_and_selection(client):
    h = login(client, "eng@example.org")
    pid = _project(client, h)
    sid = _site(client, h, pid, "Essaouira", 31.5125, -9.77)
    r = client.post(f"/api/v1/sites/{sid}/grid-points", json={"models": ["gfs", "icon"]}, headers=h)
    assert r.status_code == 202
    task = client.get(f"/api/v1/tasks/{r.json()['id']}", headers=h).json()
    assert task["status"] == "success", task
    data = client.get(f"/api/v1/sites/{sid}/grid-points", headers=h).json()
    gfs = [p for p in data["points"] if p["model"] == "gfs"]
    icon = [p for p in data["points"] if p["model"] == "icon"]
    assert len(gfs) == 4
    assert sorted({p["lat"] for p in gfs}) == [31.5, 31.75]
    assert sorted({p["lon"] for p in gfs}) == [-10.0, -9.75]
    # invariants synthétiques : 400 m au nord de i=234, 20 m au sud/sur ; site DEM = 100 + lat
    p_north = next(p for p in gfs if p["lat"] == 31.75 and p["lon"] == -9.75)
    assert p_north["model_elevation_m"] == 400.0
    assert p_north["elevation_diff_m"] == pytest.approx(400.0 - (100 + 31.5125))
    assert p_north["elevation_alert"] is True
    p_west = next(p for p in gfs if p["lat"] == 31.5 and p["lon"] == -10.0)
    assert p_west["is_land"] is False and p_west["land_sea_mismatch"] is True
    assert sum(p["contains_site"] for p in icon) == 1
    assert all(0 <= p["azimuth_deg"] < 360 for p in data["points"])
    # sélection, conservée après recalcul
    gp = p_north["grid_point_id"]
    r = client.patch(f"/api/v1/sites/{sid}/grid-points/{gp}", json={"selected": True}, headers=h)
    assert r.json()["selected"] is True
    client.post(f"/api/v1/sites/{sid}/grid-points", json={"models": ["gfs"], "method": "nearest", "n": 9}, headers=h)
    data = client.get(f"/api/v1/sites/{sid}/grid-points", headers=h).json()
    gfs = [p for p in data["points"] if p["model"] == "gfs"]
    assert len(gfs) == 9
    assert next(p for p in gfs if p["grid_point_id"] == gp)["selected"] is True


def test_icon_eu_out_of_domain_for_dakhla(client):
    h = login(client, "eng@example.org")
    pid = _project(client, h)
    sid = _site(client, h, pid, "Dakhla", 23.6848, -15.958)
    av = {m["model"]: m for m in client.get(f"/api/v1/sites/{sid}/model-availability", headers=h).json()}
    assert av["icon_eu"]["available"] is False
    assert av["icon_eu"]["message_code"] == "MODEL_SITE_OUT_OF_DOMAIN"
    assert av["icon_eu"]["params"]["lat_min"] == 29.5
    assert av["gfs"]["available"] is True
    r = client.post(f"/api/v1/sites/{sid}/grid-points", json={"models": ["icon_eu"]}, headers=h)
    task = client.get(f"/api/v1/tasks/{r.json()['id']}", headers=h).json()
    summary = task["result"]["models"][0]
    assert summary["status"] == "out_of_domain" and summary["count"] == 0


def test_grid_points_invalid_n(client):
    h = login(client, "eng@example.org")
    pid = _project(client, h)
    sid = _site(client, h, pid, "S", 31.5, -9.7)
    r = client.post(
        f"/api/v1/sites/{sid}/grid-points", json={"models": ["gfs"], "method": "nearest", "n": 5}, headers=h
    )
    assert r.json()["error"]["code"] == "GRID_N_INVALID"
    r = client.post(f"/api/v1/sites/{sid}/grid-points", json={"models": ["foo"]}, headers=h)
    assert r.status_code == 404


def test_internal_domain_email_accepted(client):
    h = login(client, "admin@example.org")
    r = client.post("/api/v1/users", json={"email": "Ingenieur@Parc.LOCAL", "password": PWD}, headers=h)
    assert r.status_code == 201 and r.json()["email"] == "ingenieur@parc.local"
    assert client.post("/api/v1/auth/login", json={"email": "ingenieur@parc.local", "password": PWD}).status_code == 200
    r = client.post("/api/v1/users", json={"email": "not-an-email", "password": PWD}, headers=h)
    assert r.status_code == 422
