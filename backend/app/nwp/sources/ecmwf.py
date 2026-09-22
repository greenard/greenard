"""ECMWF IFS HRES et ENS (open data, licence CC-BY 4.0).

Même arborescence sur `data.ecmwf.int/forecasts` (`ecmwf`) et sur le miroir AWS
`ecmwf-forecasts` (`aws`) : un fichier GRIB par échéance et un index JSON (une ligne par champ,
avec `_offset` et `_length`). Les échéances disponibles sont lues dans le miroir (listing), sinon
déduites du calendrier officiel. Les champs sont globaux : pour ENS, une échéance pèse plusieurs
centaines de Mo pour les 51 membres — le plafond de volume s'applique.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

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

PRESSURE_LEVELS = (1000, 925, 850, 700, 600, 500)
_SURFACE = {"gust_10m": "10fg", "t_2m": "2t", "d_2m": "2d", "sp": "sp", "msl": "msl"}


def ecmwf_param(var: str) -> tuple[str, str, str] | None:
    """canonique → (param, levtype, levelist)."""
    m = WIND_HEIGHT_RE.match(var)
    if m:
        comp, h = m.group(1), int(m.group(2))
        return (f"{h}{comp}", "sfc", "") if h in (10, 100) else None
    m = PRESSURE_RE.match(var)
    if m:
        comp, p = m.group(1), int(m.group(2))
        return (comp, "pl", str(p)) if p in PRESSURE_LEVELS else None
    if var == "rh_2m":
        return None  # dérivée de 2t et 2d
    p = _SURFACE.get(var)
    return (p, "sfc", "") if p else None


def default_steps(run: datetime, ensemble: bool, max_lead_h: int) -> list[int]:
    if run.hour in (0, 12):
        steps = list(range(0, 145, 3)) + list(range(150, 361, 6))
    else:
        steps = list(range(0, 145, 3))
    return [s for s in steps if s <= max_lead_h]


class _EcmwfBase(Source):
    stream = "oper"
    ftype = "fc"
    ensemble = False
    gust_semantics = "period_max"  # 10fg : maximum depuis le post-traitement précédent

    def base(self) -> str:
        raise NotImplementedError

    def stem(self, run: datetime, step: int) -> str:
        return (
            f"{self.base()}/{run:%Y%m%d}/{run:%H}z/ifs/0p25/{self.stream}/"
            f"{run:%Y%m%d%H}0000-{step}h-{self.stream}-{self.ftype}"
        )

    def steps(self, client: httpx.Client, run: datetime, max_lead_h: int) -> list[int]:
        """Échéances publiées pour ce run (listing du miroir AWS), à défaut le calendrier officiel."""
        aws = get_settings().ecmwf_aws_url
        prefix = f"{run:%Y%m%d}/{run:%H}z/ifs/0p25/{self.stream}/"
        try:
            r = get(client, f"{aws}/?list-type=2&prefix={prefix}&max-keys=1000")
            found = sorted({int(x) for x in re.findall(r"-(\d+)h-[a-z]+-[a-z]+\.index</Key>", r.text)})
        except SourceError:
            found = []
        steps = found or default_steps(run, self.ensemble, 360)
        return [s for s in steps if s <= max_lead_h]

    def latest_run(self, client: httpx.Client) -> datetime:
        for run in recent_runs((0, 6, 12, 18), min_age_h=6):
            steps = default_steps(run, self.ensemble, 360)
            if get(client, self.stem(run, steps[-1]) + ".index").status_code == 200:
                return run
        raise SourceError("SOURCE_NO_RECENT_RUN", "No complete recent run found", model=self.model)

    def _params(self, req: FetchRequest):
        wanted, missing = {}, []
        for v in req.variables:
            key = ecmwf_param(v)
            if key is None and v == "rh_2m":
                wanted[ecmwf_param("t_2m")] = "t_2m"
                wanted[ecmwf_param("d_2m")] = "d_2m"
            elif key is None:
                missing.append(v)
            else:
                wanted[key] = v
        return wanted, missing

    def _entries(self, client, run, step, wanted, members):
        r = get(client, self.stem(run, step) + ".index")
        if r.status_code != 200:
            return None
        out = []
        for line in r.text.splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            key = (e.get("param"), e.get("levtype"), e.get("levelist", ""))
            if key not in wanted:
                continue
            member = int(e.get("number", 0) or 0)
            if members is not None and member not in members:
                continue
            out.append((wanted[key], member, int(e["_offset"]), int(e["_length"])))
        return out

    def _plan(self, client, req):
        wanted, missing = self._params(req)
        steps = self.steps(client, req.run, req.max_lead_h)
        if not steps:
            raise SourceError(
                "SOURCE_RUN_NOT_AVAILABLE", "Run not available", model=self.model, run=req.run.isoformat()
            )
        # ENS open data : 50 membres perturbés (1..50), pas de contrôle dans les fichiers « ef »
        members = (sorted(req.members) if req.members else None) if self.ensemble else [0]
        sample = self._entries(client, req.run, steps[min(1, len(steps) - 1)], wanted, members)
        if sample is None:
            raise SourceError(
                "SOURCE_RUN_NOT_AVAILABLE", "Run not available", model=self.model, run=req.run.isoformat()
            )
        found_vars = {e[0] for e in sample}
        missing += [v for v in set(wanted.values()) if v not in found_vars and v not in ("t_2m", "d_2m")]
        mem_list = sorted({e[1] for e in sample}) or [0]
        per_step = sum(e[3] for e in sample)
        est = Estimate(
            len(steps) * len(sample),
            per_step * len(steps),
            steps,
            sorted(found_vars | ({"rh_2m"} if {"t_2m", "d_2m"} <= found_vars else set())),
            missing,
            mem_list,
        )
        est.notes.append("ECMWF open data: global fields, downloaded then cut; CC-BY 4.0")
        return wanted, members, est

    def estimate(self, client: httpx.Client, req: FetchRequest) -> Estimate:
        return self._plan(client, req)[2]

    def fetch(self, client: httpx.Client, req: FetchRequest, progress: Progress):
        wanted, members, est = self._plan(client, req)
        check_size(est)
        builder = RawBuilder(req, est.members, est.steps)
        lats = [p.lat for p in req.points]
        lons = [p.lon for p in req.points]
        url_of = {s: self.stem(req.run, s) + ".grib2" for s in est.steps}
        entries = parallel(
            est.steps,
            lambda s: self._entries(client, req.run, s, wanted, members),
            progress,
            "index",
            get_settings().download_workers,
            0.0,
            0.1,
        )
        items = [(s, e) for s, es in zip(est.steps, entries, strict=True) if es for e in es]

        def job(item):
            step, (var, member, off, length) = item
            msg = decode_grib(get_bytes(client, url_of[step], off, off + length - 1))[0]
            builder.put(var, member, step, extract_regular(msg, lats, lons))

        parallel(items, job, progress, "GRIB", get_settings().download_workers, 0.1, 1.0)
        missing_steps = [str(s) for s, es in zip(est.steps, entries, strict=True) if es is None]
        return finalize_rh(
            builder.build(
                self,
                source_detail=self.base(),
                missing_steps=",".join(missing_steps) or None,
                licence="CC-BY-4.0 (ECMWF open data)",
            )
        )


class _Aws:
    code = "aws"

    def base(self) -> str:
        return get_settings().ecmwf_aws_url


class _Ecmwf:
    code = "ecmwf"

    def base(self) -> str:
        return get_settings().ecmwf_base_url


class IfsAws(_Aws, _EcmwfBase):
    model = "ifs"


class IfsEcmwf(_Ecmwf, _EcmwfBase):
    model = "ifs"


class EnsAws(_Aws, _EcmwfBase):
    model = "ens"
    stream = "enfo"
    ftype = "ef"
    ensemble = True


class EnsEcmwf(_Ecmwf, _EcmwfBase):
    model = "ens"
    stream = "enfo"
    ftype = "ef"
    ensemble = True
