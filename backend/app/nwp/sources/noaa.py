"""GFS et GEFS (NOAA/NCEP).

- `aws` : buckets publics `noaa-gfs-bdp-pds` / `noaa-gefs-pds`, requêtes par plages d'octets via les
  index `.idx`. Chaque champ est **global** (~0,5–1 Mo) : volume important pour 16 jours.
- `nomads` : service « grib filter » de NOMADS, qui découpe côté serveur (sous-domaine + variables) :
  quelques ko par échéance. Source à privilégier en production ; NOMADS limite le nombre de requêtes
  (~120/min) et ne conserve qu'une dizaine de jours.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings
from app.nwp.grib import decode_grib, extract_regular
from app.nwp.sources.base import (
    Estimate,
    FetchRequest,
    Progress,
    RawBuilder,
    Source,
    SourceError,
    check_size,
    finalize_rh,
    get,
    get_bytes,
    parallel,
    recent_runs,
)
from app.nwp.variables import PRESSURE_RE, WIND_HEIGHT_RE

GFS_AWS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
GEFS_AWS = "https://noaa-gefs-pds.s3.amazonaws.com"
GFS_HEIGHTS = (10, 20, 30, 40, 50, 80, 100)
GEFS_HEIGHTS = (10,)
PRESSURE_LEVELS = (1000, 925, 850, 700)

# canonique → (VAR, niveau) des index NCEP
_SURFACE = {
    "gust_10m": ("GUST", "surface"),
    "t_2m": ("TMP", "2 m above ground"),
    "d_2m": ("DPT", "2 m above ground"),
    "rh_2m": ("RH", "2 m above ground"),
    "sp": ("PRES", "surface"),
    "msl": ("PRMSL", "mean sea level"),
}
# (VAR, niveau) → identifiants GRIB2 (discipline, catégorie, numéro, typeOfLevel, level)
_GRIB_ID = {
    "UGRD": (0, 2, 2),
    "VGRD": (0, 2, 3),
    "GUST": (0, 2, 22),
    "TMP": (0, 0, 0),
    "DPT": (0, 0, 6),
    "RH": (0, 1, 1),
    "PRES": (0, 3, 0),
    "PRMSL": (0, 3, 1),
}


def ncep_field(var: str, heights: tuple[int, ...]) -> tuple[str, str] | None:
    m = WIND_HEIGHT_RE.match(var)
    if m:
        comp, h = m.group(1), int(m.group(2))
        return (("UGRD" if comp == "u" else "VGRD"), f"{h} m above ground") if h in heights else None
    m = PRESSURE_RE.match(var)
    if m:
        comp, p = m.group(1), int(m.group(2))
        if p not in PRESSURE_LEVELS:
            return None
        return ({"u": "UGRD", "v": "VGRD", "t": "TMP"}[comp], f"{p} mb")
    return _SURFACE.get(var)


def parse_idx(text: str) -> list[tuple[int, str, str]]:
    rows = []
    for line in text.strip().splitlines():
        parts = line.split(":")
        if len(parts) >= 5:
            rows.append((int(parts[1]), parts[3], parts[4]))
    return rows


def idx_range(rows, var: str, level: str) -> tuple[int, int | None] | None:
    for k, (off, v, lev) in enumerate(rows):
        if v == var and lev == level:
            return off, (rows[k + 1][0] - 1 if k + 1 < len(rows) else None)
    return None


def gfs_steps(max_lead_h: int) -> list[int]:
    steps = list(range(0, 121)) + list(range(123, 385, 3))
    return [s for s in steps if s <= max_lead_h]


def gefs_steps(max_lead_h: int) -> list[int]:
    steps = list(range(0, 241, 3)) + list(range(246, 385, 6))
    return [s for s in steps if s <= max_lead_h]


class _NcepBase(Source):
    heights: tuple[int, ...] = GFS_HEIGHTS
    gust_semantics = "instantaneous"  # GUST NCEP : rafale instantanée à l'échéance
    run_hours = (0, 6, 12, 18)
    ensemble = False

    def members(self, req: FetchRequest) -> list[int]:
        if not self.ensemble:
            return [0]
        return sorted(req.members) if req.members else list(range(0, 31))

    def steps(self, max_lead_h: int) -> list[int]:
        return gefs_steps(max_lead_h) if self.ensemble else gfs_steps(max_lead_h)

    def fields(self, req: FetchRequest) -> tuple[dict[str, tuple[str, str]], list[str]]:
        fields, missing = {}, []
        for v in req.variables:
            f = ncep_field(v, self.heights)
            if f is None:
                missing.append(v)
            else:
                fields[v] = f
        # RH directement disponible : le point de rosée n'est pas nécessaire
        fields.pop("d_2m", None) if "rh_2m" in fields else None
        return fields, missing

    # chemins
    def file_name(self, run: datetime, step: int, member: int) -> str:
        if self.ensemble:
            prefix = "gec00" if member == 0 else f"gep{member:02d}"
            return f"{prefix}.t{run:%H}z.pgrb2s.0p25.f{step:03d}"
        return f"gfs.t{run:%H}z.pgrb2.0p25.f{step:03d}"

    def dir_path(self, run: datetime) -> str:
        if self.ensemble:
            return f"/gefs.{run:%Y%m%d}/{run:%H}/atmos/pgrb2sp25"
        return f"/gfs.{run:%Y%m%d}/{run:%H}/atmos"


class NcepAws(_NcepBase):
    code = "aws"

    def base(self) -> str:
        return GEFS_AWS if self.ensemble else GFS_AWS

    def url(self, run: datetime, step: int, member: int) -> str:
        return f"{self.base()}{self.dir_path(run)}/{self.file_name(run, step, member)}"

    def latest_run(self, client: httpx.Client) -> datetime:
        last = self.steps(384)[-1]
        for run in recent_runs(self.run_hours, min_age_h=4):
            if get(client, self.url(run, last, 0) + ".idx").status_code == 200:
                return run
        raise SourceError("SOURCE_NO_RECENT_RUN", "No complete recent run found", model=self.model)

    def _plan(self, client: httpx.Client, req: FetchRequest):
        fields, missing = self.fields(req)
        members = self.members(req)
        steps = self.steps(req.max_lead_h)
        # l'index d'une échéance suffit à estimer la taille (mêmes champs à chaque échéance)
        r = get(client, self.url(req.run, steps[min(1, len(steps) - 1)], members[0]) + ".idx")
        if r.status_code != 200:
            raise SourceError(
                "SOURCE_RUN_NOT_AVAILABLE", "Run not available on AWS", model=self.model, run=req.run.isoformat()
            )
        rows = parse_idx(r.text)
        per_step = 0
        for v, (var, lev) in list(fields.items()):
            rg = idx_range(rows, var, lev)
            if rg is None:
                missing.append(v)
                fields.pop(v)
            elif rg[1] is not None:
                per_step += rg[1] - rg[0] + 1
        est = Estimate(
            len(steps) * len(members) * (len(fields) + 1),
            per_step * len(steps) * len(members),
            steps,
            list(fields),
            missing,
            members,
        )
        if not self.ensemble:
            est.notes.append("AWS: global fields are downloaded then cut (no server-side subsetting)")
        return fields, est

    def estimate(self, client: httpx.Client, req: FetchRequest) -> Estimate:
        return self._plan(client, req)[1]

    def fetch(self, client: httpx.Client, req: FetchRequest, progress: Progress):
        fields, est = self._plan(client, req)
        check_size(est)
        builder = RawBuilder(req, est.members, est.steps)
        lats = [p.lat for p in req.points]
        lons = [p.lon for p in req.points]

        def job(item):
            step, member = item
            url = self.url(req.run, step, member)
            r = get(client, url + ".idx")
            if r.status_code != 200:
                return f"{step}:{member}"
            rows = parse_idx(r.text)
            for v, (var, lev) in fields.items():
                rg = idx_range(rows, var, lev)
                if rg is None:
                    continue
                msg = decode_grib(get_bytes(client, url, *rg))[0]
                builder.put(v, member, step, extract_regular(msg, lats, lons))
            return None

        items = [(s, m) for s in est.steps for m in est.members]
        failed = [x for x in parallel(items, job, progress, "GRIB", get_settings().download_workers) if x]
        ds = builder.build(self, source_detail=self.base(), missing_steps=",".join(failed) or None)
        return finalize_rh(ds)


class NcepNomads(_NcepBase):
    """Grib filter NOMADS : découpage côté serveur."""

    code = "nomads"

    def script(self) -> str:
        return "filter_gefs_atmos_0p25s.pl" if self.ensemble else "filter_gfs_0p25.pl"

    def url(self, req: FetchRequest, step: int, member: int, fields: dict[str, tuple[str, str]]) -> str:
        lats = [p.lat for p in req.points]
        lons = [p.lon for p in req.points]
        params = {"dir": self.dir_path(req.run), "file": self.file_name(req.run, step, member)}
        for var in sorted({f[0] for f in fields.values()}):
            params[f"var_{var}"] = "on"
        for lev in sorted({f[1] for f in fields.values()}):
            params[f"lev_{lev.replace(' ', '_')}"] = "on"
        params |= {
            "subregion": "",
            "toplat": max(lats) + 0.5,
            "bottomlat": min(lats) - 0.5,
            "leftlon": min(lons) - 0.5,
            "rightlon": max(lons) + 0.5,
        }
        return f"{get_settings().nomads_base_url}/cgi-bin/{self.script()}?{urlencode(params)}"

    def latest_run(self, client: httpx.Client) -> datetime:
        base = get_settings().nomads_base_url
        for run in recent_runs(self.run_hours, days=2, min_age_h=4):
            path = (
                f"/pub/data/nccf/com/{'gens' if self.ensemble else 'gfs'}/prod"
                f"{self.dir_path(run)}/{self.file_name(run, self.steps(384)[-1], 0)}.idx"
            )
            if get(client, base + path).status_code == 200:
                return run
        raise SourceError("SOURCE_NO_RECENT_RUN", "No complete recent run found on NOMADS", model=self.model)

    def estimate(self, client: httpx.Client, req: FetchRequest) -> Estimate:
        fields, missing = self.fields(req)
        members = self.members(req)
        steps = self.steps(req.max_lead_h)
        n = len(steps) * len(members)
        # sous-domaine de quelques nœuds : ~1 ko par champ
        return Estimate(
            n,
            n * len(fields) * 2000,
            steps,
            list(fields),
            missing,
            members,
            notes=["NOMADS grib filter: server-side subsetting; ~120 requests/min allowed"],
        )

    def fetch(self, client: httpx.Client, req: FetchRequest, progress: Progress):
        fields, missing = self.fields(req)
        est = self.estimate(client, req)
        builder = RawBuilder(req, est.members, est.steps)
        lats = [p.lat for p in req.points]
        lons = [p.lon for p in req.points]
        wanted = {}
        for v, (var, lev) in fields.items():
            d, c, n = _GRIB_ID[var]
            wanted[(d, c, n) + _level_key(lev)] = v

        def job(item):
            step, member = item
            data = get_bytes(client, self.url(req, step, member, fields))
            for msg in decode_grib(data):
                key = (
                    msg["discipline"],
                    msg["parameterCategory"],
                    msg["parameterNumber"],
                    msg["typeOfLevel"],
                    int(msg["level"] or 0),
                )
                v = wanted.get(key)
                if v is not None:
                    builder.put(v, member, step, extract_regular(msg, lats, lons))

        items = [(s, m) for s in est.steps for m in est.members]
        # 4 requêtes simultanées au plus : politique d'usage NOMADS
        parallel(items, job, progress, "NOMADS", workers=4)
        return finalize_rh(builder.build(self, source_detail=get_settings().nomads_base_url))


def _level_key(level: str) -> tuple[str, int]:
    if level.endswith("m above ground"):
        return ("heightAboveGround", int(level.split()[0]))
    if level.endswith(" mb"):
        return ("isobaricInhPa", int(level.split()[0]))
    if level == "surface":
        return ("surface", 0)
    if level == "mean sea level":
        return ("meanSea", 0)
    raise ValueError(level)


class GfsAws(NcepAws):
    model = "gfs"


class GfsNomads(NcepNomads):
    model = "gfs"


class GefsAws(NcepAws):
    model = "gefs"
    ensemble = True
    heights = GEFS_HEIGHTS


class GefsNomads(NcepNomads):
    model = "gefs"
    ensemble = True
    heights = GEFS_HEIGHTS
