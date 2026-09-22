"""Socle commun des sources de prévision.

Chaque source produit un jeu de données « brut » (`xr.Dataset`) aux seules échéances natives du
modèle : dimensions (member, point, time), variables canoniques en SI (`app.nwp.variables`),
coordonnées `lead_h`, `grid_point_id`, `lat`, `lon`, et attributs de traçabilité.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
import pandas as pd
import xarray as xr

from app.core.config import get_settings
from app.core.errors import AppError
from app.nwp.variables import info

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]


class SourceError(AppError):
    status_code = 502


class DownloadTooLarge(AppError):
    status_code = 413


@dataclass(frozen=True)
class PointRef:
    grid_point_id: int
    native_index: int
    lat: float
    lon: float
    i: int | None = None
    j: int | None = None


@dataclass
class FetchRequest:
    model: str
    run: datetime  # initialisation (UTC)
    max_lead_h: int
    points: list[PointRef]
    variables: list[str]  # canoniques
    members: list[int] | None = None  # None = tous (ensembles) / [0] (déterministe)


@dataclass
class Estimate:
    n_requests: int
    bytes: int | None  # None si inconnu à l'avance
    steps: list[int]
    available_variables: list[str]
    missing_variables: list[str]
    members: list[int]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n_requests": self.n_requests,
            "bytes": self.bytes,
            "megabytes": None if self.bytes is None else round(self.bytes / 1e6, 1),
            "n_steps": len(self.steps),
            "first_lead_h": self.steps[0] if self.steps else None,
            "last_lead_h": self.steps[-1] if self.steps else None,
            "available_variables": self.available_variables,
            "missing_variables": self.missing_variables,
            "members": len(self.members),
            "notes": self.notes,
        }


class Source:
    """Interface d'une source pour un modèle donné."""

    code: str = ""  # code de la source de données (gouvernance, voir app.forecasts.governance)
    model: str = ""
    gust_semantics: str = "period_max"  # ou "instantaneous"

    def latest_run(self, client: httpx.Client) -> datetime:
        raise NotImplementedError

    def estimate(self, client: httpx.Client, req: FetchRequest) -> Estimate:
        raise NotImplementedError

    def fetch(self, client: httpx.Client, req: FetchRequest, progress: Progress) -> xr.Dataset:
        raise NotImplementedError


# --------------------------------------------------------------------------------------------
# Outils HTTP
# --------------------------------------------------------------------------------------------


def http_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(90.0, connect=20.0),
        follow_redirects=True,
        trust_env=True,
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=16),
        headers={"User-Agent": "greenard/0.2 (+wind forecasting; contact: admin)"},
    )


def get(client: httpx.Client, url: str, headers: dict | None = None, ok=(200, 206), retries: int = 3) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = client.get(url, headers=headers)
            if r.status_code in ok or r.status_code == 404:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                last = SourceError("SOURCE_HTTP_ERROR", f"HTTP {r.status_code} on {url}", url=url, status=r.status_code)
                threading.Event().wait(2**attempt)
                continue
            return r
        except httpx.HTTPError as exc:
            last = exc
            threading.Event().wait(2**attempt)
    if isinstance(last, AppError):
        raise last
    raise SourceError("SOURCE_UNREACHABLE", f"Cannot reach {url}: {last}", url=url)


def get_bytes(client: httpx.Client, url: str, start: int | None = None, end: int | None = None) -> bytes:
    headers = None
    if start is not None:
        headers = {"Range": f"bytes={start}-{'' if end is None else end}"}
    r = get(client, url, headers=headers)
    if r.status_code not in (200, 206):
        raise SourceError("SOURCE_HTTP_ERROR", f"HTTP {r.status_code} on {url}", url=url, status=r.status_code)
    return r.content


def parallel(items: Iterable, fn: Callable, progress: Progress, label: str, workers: int = 8, lo=0.0, hi=1.0):
    """Exécute fn(item) en parallèle ; renvoie la liste des résultats dans l'ordre des items."""
    items = list(items)
    out: list = [None] * len(items)
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, it): k for k, it in enumerate(items)}
        for fut in as_completed(futures):
            out[futures[fut]] = fut.result()
            done += 1
            if done % max(1, len(items) // 50) == 0 or done == len(items):
                progress(lo + (hi - lo) * done / len(items), f"{label} {done}/{len(items)}")
    return out


def check_size(est: Estimate) -> None:
    cap = get_settings().max_download_mb
    if est.bytes is not None and est.bytes > cap * 1e6:
        raise DownloadTooLarge(
            "DOWNLOAD_TOO_LARGE",
            f"Estimated download {est.bytes / 1e6:.0f} MB exceeds the {cap} MB limit",
            megabytes=round(est.bytes / 1e6),
            limit_mb=cap,
        )


def recent_runs(hours: Iterable[int], days: int = 3, min_age_h: float = 3.0) -> list[datetime]:
    now = datetime.now(UTC)
    out = []
    for d in range(days):
        day = (now - timedelta(days=d)).date()
        for h in sorted(hours, reverse=True):
            t = datetime(day.year, day.month, day.day, h, tzinfo=UTC)
            if t <= now - timedelta(hours=min_age_h):
                out.append(t)
    return out


# --------------------------------------------------------------------------------------------
# Assemblage du jeu de données brut
# --------------------------------------------------------------------------------------------


class RawBuilder:
    """Accumule des valeurs (variable, membre, échéance) → tableau (points) de façon thread-safe."""

    def __init__(self, req: FetchRequest, members: list[int], steps: list[int]):
        self.req = req
        self.members = members
        self.steps = steps
        self._m = {m: k for k, m in enumerate(members)}
        self._s = {s: k for k, s in enumerate(steps)}
        self.data: dict[str, np.ndarray] = {}
        self._lock = threading.Lock()

    def put(self, var: str, member: int, step: int, values, point: int | None = None) -> None:
        """Valeurs de tous les points (ou d'un seul si `point` est donné) pour une échéance."""
        with self._lock:
            arr = self.data.get(var)
            if arr is None:
                arr = np.full((len(self.members), len(self.req.points), len(self.steps)), np.nan, dtype="float32")
                self.data[var] = arr
            arr[self._m[member], slice(None) if point is None else point, self._s[step]] = values

    def put_series(self, var: str, member: int, point: int, values: np.ndarray) -> None:
        """Série complète (toutes les échéances) d'un point."""
        with self._lock:
            arr = self.data.get(var)
            if arr is None:
                arr = np.full((len(self.members), len(self.req.points), len(self.steps)), np.nan, dtype="float32")
                self.data[var] = arr
            arr[self._m[member], point, :] = values

    def build(self, source: Source, **attrs) -> xr.Dataset:
        run = (
            pd.Timestamp(self.req.run).tz_convert(None)
            if pd.Timestamp(self.req.run).tzinfo
            else pd.Timestamp(self.req.run)
        )
        times = run + pd.to_timedelta(self.steps, unit="h")
        pts = self.req.points
        ds = xr.Dataset(
            {k: (("member", "point", "time"), v) for k, v in self.data.items()},
            coords={
                "member": self.members,
                "point": np.arange(len(pts)),
                "time": times.values,
                "lead_h": ("time", np.asarray(self.steps, dtype="int32")),
                "grid_point_id": ("point", [p.grid_point_id for p in pts]),
                "native_index": ("point", [p.native_index for p in pts]),
                "lat": ("point", [p.lat for p in pts]),
                "lon": ("point", [p.lon for p in pts]),
            },
        )
        for k in ds.data_vars:
            vi = info(k)
            ds[k].attrs = {"units": vi.units, "long_name": vi.long_name, "standard_name": vi.standard_name}
        missing = [v for v in self.req.variables if v not in ds.data_vars]
        ds.attrs = {
            "model": self.req.model,
            "run": run.isoformat() + "Z",
            "source": source.code,
            "gust_semantics": source.gust_semantics,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "missing_variables": ",".join(missing),
            **{k: v for k, v in attrs.items() if v is not None},
        }
        return ds


def finalize_rh(ds: xr.Dataset) -> xr.Dataset:
    """Calcule rh_2m depuis t_2m et d_2m si la source ne fournit que le point de rosée."""
    from app.nwp.wind import relative_humidity

    if "rh_2m" not in ds and "t_2m" in ds and "d_2m" in ds:
        ds["rh_2m"] = (ds["t_2m"].dims, relative_humidity(ds["t_2m"].values, ds["d_2m"].values).astype("float32"))
        ds["rh_2m"].attrs = {
            "units": "%",
            "long_name": "relative humidity at 2 m (derived from dew point)",
            "standard_name": "relative_humidity",
        }
    missing = [v for v in ds.attrs.get("missing_variables", "").split(",") if v and v not in ds]
    ds.attrs["missing_variables"] = ",".join(missing)
    return ds
