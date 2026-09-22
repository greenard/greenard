"""Téléchargement des prévisions, séries, exports, gouvernance et archivage (sans réseau : source simulée)."""

import io
import json
import os
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.db

if not os.environ.get("TEST_DATABASE_URL"):
    pytest.skip("TEST_DATABASE_URL non défini", allow_module_level=True)

import xarray as xr  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.models import ForecastJob, NwpRun, Project  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.nwp.sources import registry  # noqa: E402
from app.nwp.sources.base import Estimate, RawBuilder, Source, SourceError  # noqa: E402
from app.nwp.wind import speed_dir_to_uv  # noqa: E402
from tests.test_api import (  # noqa: E402, F401
    _project,
    _site,
    clean,
    client,
    fake_terrain_and_invariants,
    login,
    schema,
)

RUN = datetime(2026, 9, 22, 0, tzinfo=UTC)


class FakeSource(Source):
    """Source simulée : GFS horaire jusqu'à 120 h puis 3 h ; ENS 3 h, 5 membres."""

    def __init__(self, model, code="aws", fail=False):
        self.model, self.code, self.fail = model, code, fail
        self.gust_semantics = "instantaneous" if model == "gfs" else "period_max"

    def latest_run(self, client):
        return RUN

    def steps(self, max_lead):
        if self.model == "gfs":
            s = list(range(0, 121)) + list(range(123, 385, 3))
        else:
            s = list(range(0, 145, 3)) + list(range(150, 361, 6))
        return [x for x in s if x <= max_lead]

    def estimate(self, client, req):
        members = [1, 2, 3, 4, 5] if self.model == "ens" else [0]
        avail = [v for v in req.variables if v != "u_120m" and v != "v_120m"]
        return Estimate(10, 1_000_000, self.steps(req.max_lead_h), avail, [], members)

    def fetch(self, client, req, progress):
        if self.fail:
            raise SourceError("SOURCE_UNREACHABLE", "simulated outage", url="x")
        est = self.estimate(client, req)
        b = RawBuilder(req, est.members, est.steps)
        for m in est.members:
            for s in est.steps:
                ws = 8 + 3 * np.sin(s / 12) + 0.5 * m + np.arange(len(req.points))
                u, v = speed_dir_to_uv(ws, (s * 5) % 360)
                for var in est.available_variables:
                    if var.startswith("u_"):
                        b.put(var, m, s, u)
                    elif var.startswith("v_"):
                        b.put(var, m, s, v)
                    elif var == "t_2m":
                        b.put(var, m, s, np.full(len(req.points), 293.15))
                    elif var == "sp":
                        b.put(var, m, s, np.full(len(req.points), 100500.0))
                    elif var == "gust_10m":
                        b.put(var, m, s, ws * 1.4)
                    elif var == "rh_2m":
                        b.put(var, m, s, np.full(len(req.points), 60.0))
        progress(1.0, "done")
        return b.build(self, source_detail="fake")


@pytest.fixture(autouse=True)
def fake_sources(monkeypatch):
    state = {"failing": set()}

    def get_source(model, code):
        return FakeSource(model, code, fail=(model, code) in state["failing"])

    monkeypatch.setattr("app.forecasts.service.get_source", get_source)
    monkeypatch.setattr(registry, "get_source", get_source)  # archivage (import local)
    return state


def _prepare(client, h, models=("gfs",), lat=31.5125, lon=-9.77):
    pid = _project(client, h)
    sid = _site(client, h, pid, "Essaouira", lat, lon)
    client.post(f"/api/v1/sites/{sid}/grid-points", json={"models": list(models)}, headers=h)
    pts = client.get(f"/api/v1/sites/{sid}/grid-points", headers=h).json()["points"]
    for model in models:
        mine = [p for p in pts if p["model"] == model][:2]
        for p in mine:
            client.patch(f"/api/v1/sites/{sid}/grid-points/{p['grid_point_id']}", json={"selected": True}, headers=h)
    return pid, sid


def _download(client, h, sid, **body):
    r = client.post(f"/api/v1/sites/{sid}/forecasts", json={"models": ["gfs"], "max_lead_h": 132, **body}, headers=h)
    assert r.status_code == 202, r.text
    return client.get(f"/api/v1/forecasts/{r.json()['id']}", headers=h).json()


def test_download_series_and_flags(client):
    h = login(client, "eng@example.org")
    _, sid = _prepare(client, h)
    job = _download(client, h, sid, wind_heights_m=[10, 100, 120])
    assert job["status"] == "success", job
    ext = job["extracts"][0]
    assert ext["source"] == "nomads"  # première source de la préférence GFS
    assert ext["n_times"] == 125  # 0–120 h horaire + 123, 126, 129, 132 and ext["n_members"] == 1
    assert "u_120m" not in ext["variables"]
    with SessionLocal() as db:
        assert db.query(NwpRun).filter_by(model_code="gfs").one().init_time == RUN
    s = client.get(f"/api/v1/forecasts/{job['id']}/series?step=10min&method=pchip", headers=h).json()
    m = s["models"][0]
    assert m["times"][0] == "2026-09-22T00:00:00Z"
    flags = pd.Series(m["flags"], index=pd.DatetimeIndex(m["times"]))
    assert flags["2026-09-22T01:00"] == "native"
    assert flags["2026-09-22T01:10"] == "interpolated"
    assert flags["2026-09-27T01:00"] == "interpolated"  # 121 h : GFS 3-horaire
    ws = m["points"][0]["variables"]["ws_100m"]["values"]
    assert min(v for v in ws if v is not None) >= 0
    assert m["points"][0]["variables"]["t_2m"]["values"][0] == pytest.approx(20.0)
    assert m["points"][0]["variables"]["sp"]["unit"] == "hPa"


def test_auto_fallback_records_attempts(client, fake_sources):
    fake_sources["failing"].add(("gfs", "nomads"))
    h = login(client, "eng@example.org")
    _, sid = _prepare(client, h)
    job = _download(client, h, sid)
    ext = job["extracts"][0]
    assert ext["status"] == "success" and ext["source"] == "aws"
    assert ext["attempts"][0]["source"] == "nomads" and ext["attempts"][0]["code"] == "SOURCE_UNREACHABLE"


def test_no_selected_point(client):
    h = login(client, "eng@example.org")
    pid = _project(client, h)
    sid = _site(client, h, pid, "S", 31.5, -9.7)
    job = _download(client, h, sid)
    assert job["status"] == "failure"
    assert job["extracts"][0]["error"]["code"] == "FORECAST_NO_POINT_SELECTED"


def test_exports(client):
    h = login(client, "eng@example.org")
    _, sid = _prepare(client, h)
    job = _download(client, h, sid)
    base = f"/api/v1/forecasts/{job['id']}/export"
    r = client.get(f"{base}?fmt=csv&step=3h&tz=Africa/Casablanca", headers=h)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    text = r.content.decode()
    header = [line for line in text.splitlines() if line.startswith("#")]
    assert any("time_reference" in line for line in header)
    assert any(line.startswith("# model gfs:") and '"run_utc": "2026-09-22T00:00:00Z"' in line for line in header)
    df = pd.read_csv(io.StringIO(text), comment="#")
    assert {
        "time_utc",
        "time_local",
        "model",
        "grid_point_id",
        "lat",
        "lon",
        "time_flag",
        "ws_100m",
        "wd_100m",
        "t_2m",
        "sp",
    } <= set(df.columns)
    assert set(df["time_flag"]) == {"native", "aggregated"}
    # heure locale : calculée côté serveur avec la tzdata IANA embarquée (Maroc à UTC+0 depuis le
    # 20/09/2026 selon tzdata 2026d ; UTC+1 auparavant hors Ramadan)
    from zoneinfo import ZoneInfo

    t = pd.Timestamp(df["time_utc"].iloc[1]).tz_convert(ZoneInfo("Africa/Casablanca"))
    assert df["time_local"].iloc[1] == t.strftime("%Y-%m-%dT%H:%M:%S%z")
    assert df["t_2m"].iloc[0] == pytest.approx(20.0)
    r = client.get(f"{base}?fmt=xlsx", headers=h)
    x = pd.read_excel(io.BytesIO(r.content), sheet_name=None)
    assert set(x) == {"data", "metadata", "units", "points"}
    r = client.get(f"{base}?fmt=nc&step=15min", headers=h)
    with open(get_settings().data_dir / "t.nc", "wb") as fh:
        fh.write(r.content)
    g = xr.open_dataset(get_settings().data_dir / "t.nc", group="gfs")
    assert g["ws_100m"].attrs["standard_name"] == "wind_speed"
    assert (g["time"].diff("time") == np.timedelta64(15, "m")).all()
    g.close()


def test_windrose_and_comparison(client):
    h = login(client, "eng@example.org")
    _, sid = _prepare(client, h, models=("gfs", "ens"))
    r = client.post(f"/api/v1/sites/{sid}/forecasts", json={"models": ["gfs", "ens"], "max_lead_h": 72}, headers=h)
    jid = r.json()["id"]
    job = client.get(f"/api/v1/forecasts/{jid}", headers=h).json()
    assert [e["status"] for e in job["extracts"]] == ["success", "success"]
    assert job["extracts"][1]["n_members"] == 5
    rose = client.get(f"/api/v1/forecasts/{jid}/windrose?height=100&sectors=16", headers=h).json()
    f = np.array(rose["models"]["gfs"]["frequency_pct"])
    assert f.shape == (6, 16) and f.sum() == pytest.approx(100.0, abs=0.1)
    cmp_ = client.get(f"/api/v1/forecasts/{jid}/comparison?height=100", headers=h).json()
    assert cmp_["models"] == ["gfs", "ens"]
    assert sum(s["bias_vs_mmm"] for s in cmp_["stats"]) == pytest.approx(0.0, abs=1e-6)
    s = client.get(f"/api/v1/forecasts/{jid}/series?step=3h", headers=h).json()
    ens = s["models"][1]["points"][0]["variables"]["ws_100m"]
    assert {"p10", "p50", "p90", "min", "max"} <= set(ens)


def test_governance(client, monkeypatch):
    ha = login(client, "admin@example.org")
    he = login(client, "eng@example.org")
    _, sid = _prepare(client, he)
    ds = client.get("/api/v1/data-sources", headers=he).json()
    om = next(x for x in ds["sources"] if x["code"] == "open_meteo")
    assert om["usage_licence"] == "non_commercial" and om["mode"] == "public"
    # source payante non implémentée : activation refusée
    r = client.patch("/api/v1/data-sources/ecmwf_hres_01", json={"enabled": True}, headers=ha)
    assert r.json()["error"]["code"] == "SOURCE_NOT_IMPLEMENTED"
    assert client.patch("/api/v1/data-sources/aws", json={"enabled": False}, headers=he).status_code == 403
    # source désactivée par l'administrateur
    client.patch("/api/v1/data-sources/nomads", json={"enabled": False}, headers=ha)
    job = _download(client, he, sid, source="nomads")
    assert job["extracts"][0]["attempts"][0]["code"] == "SOURCE_DISABLED"
    # déploiement commercial : Open-Meteo public refusé
    monkeypatch.setattr(get_settings(), "deployment_usage", "commercial")
    job = _download(client, he, sid, source="open_meteo")
    assert job["extracts"][0]["attempts"][0]["code"] == "SOURCE_NON_COMMERCIAL"
    # Open-Meteo sur abonnement : payant → acceptation explicite requise
    monkeypatch.setattr(get_settings(), "open_meteo_mode", "api_key")
    job = _download(client, he, sid, source="open_meteo")
    assert job["extracts"][0]["attempts"][0]["code"] == "SOURCE_PAID_NOT_ACCEPTED"
    job = _download(client, he, sid, source="open_meteo", accept_paid=True)
    assert job["extracts"][0]["status"] == "success" and job["extracts"][0]["paid"] is True


def test_estimate_and_validation(client):
    h = login(client, "eng@example.org")
    _, sid = _prepare(client, h)
    r = client.post(f"/api/v1/sites/{sid}/forecasts/estimate", json={"models": ["gfs"], "max_lead_h": 24}, headers=h)
    est = r.json()["models"][0]
    assert est["points"] == 2 and est["candidates"][0]["source"] == "nomads"
    assert est["candidates"][0]["estimate"]["n_steps"] == 25
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    r = client.post(f"/api/v1/sites/{sid}/forecasts", json={"models": ["gfs"], "run": future}, headers=h)
    assert r.json()["error"]["code"] == "FORECAST_RUN_INVALID"
    r = client.post(f"/api/v1/sites/{sid}/forecasts", json={"models": ["gfs"], "surface": ["foo"]}, headers=h)
    assert r.json()["error"]["code"] == "FORECAST_VARIABLE_UNKNOWN"
    hv = login(client, "view@example.org")
    assert client.post(f"/api/v1/sites/{sid}/forecasts", json={"models": ["gfs"]}, headers=hv).status_code == 404


def test_sse_events(client):
    h = login(client, "eng@example.org")
    _, sid = _prepare(client, h)
    job = _download(client, h, sid, max_lead_h=6)
    with client.stream("GET", f"/api/v1/tasks/{job['task_id']}/events", headers=h) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = [line for line in r.iter_lines() if line.startswith("data:")]
    assert json.loads(lines[-1][5:])["status"] == "success"


def test_archive_runs(client):
    from app.tasks.jobs import archive_runs

    h = login(client, "eng@example.org")
    pid, sid = _prepare(client, h)
    client.patch(
        f"/api/v1/projects/{pid}",
        json={"archive_enabled": True, "archive_models": ["gfs"], "archive_max_lead_h": 24},
        headers=h,
    )
    first = archive_runs()
    second = archive_runs()
    assert len(first["jobs"]) == 1 and second["jobs"] == []
    with SessionLocal() as db:
        job = db.get(ForecastJob, first["jobs"][0])
        assert job.kind == "archive" and job.status == "success"
        assert db.get(Project, pid).archive_models == ["gfs"]
