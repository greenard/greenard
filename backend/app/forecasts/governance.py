"""Gouvernance des sources : licence d'usage et coût (§4.3.5 de la note d'architecture).

- En déploiement `commercial`, les sources à licence `non_commercial` sont refusées.
- Les sources `paid` doivent être activées par un administrateur **et** acceptées explicitement
  dans chaque demande (`accept_paid=true`) ; le choix est tracé dans l'extrait.
- Open-Meteo : licence et coût effectifs selon le mode configuré (public → non commercial,
  api_key → abonnement payant, self_hosted → libre).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import DataSource

DEFAULT_SOURCES = [
    ("nomads", "NOAA NOMADS (grib filter)", "open", "free", True, True),
    ("aws", "AWS Open Data (NOAA GFS/GEFS, ECMWF open data mirror)", "open", "free", True, True),
    ("ecmwf", "ECMWF open data (data.ecmwf.int)", "open", "free", True, True),
    ("dwd", "DWD opendata (ICON, ICON-EU)", "open", "free", True, True),
    ("open_meteo", "Open-Meteo", "non_commercial", "free", True, True),
    ("ecmwf_hres_01", "ECMWF IFS HRES 0.1° (real-time catalogue)", "contract", "paid", False, False),
]


class SourceNotAllowed(AppError):
    status_code = 403


@dataclass
class Effective:
    code: str
    name: str
    usage_licence: str
    cost: str
    enabled: bool
    implemented: bool
    mode: str = ""


def seed(db: Session) -> None:
    """Insère les sources manquantes (base de test ; en production : migration 0002)."""
    existing = set(db.scalars(select(DataSource.code)))
    for code, name, lic, cost, enabled, impl in DEFAULT_SOURCES:
        if code not in existing:
            db.add(DataSource(code=code, name=name, usage_licence=lic, cost=cost, enabled=enabled, implemented=impl))
    db.flush()


def effective(db: Session, code: str) -> Effective:
    row = db.get(DataSource, code)
    if row is None:
        raise SourceNotAllowed("SOURCE_UNKNOWN", f"Unknown data source {code!r}", source=code)
    eff = Effective(row.code, row.name, row.usage_licence, row.cost, row.enabled, row.implemented)
    if code == "open_meteo":
        mode = get_settings().open_meteo_mode
        eff.mode = mode
        eff.usage_licence, eff.cost = {
            "public": ("non_commercial", "free"),
            "api_key": ("contract", "paid"),
            "self_hosted": ("open", "free"),
        }[mode]
    return eff


def check_allowed(db: Session, code: str, accept_paid: bool) -> Effective:
    eff = effective(db, code)
    if not eff.implemented:
        raise SourceNotAllowed("SOURCE_NOT_IMPLEMENTED", "This data source is not implemented", source=code)
    if not eff.enabled:
        raise SourceNotAllowed("SOURCE_DISABLED", "This data source is disabled by an administrator", source=code)
    if get_settings().deployment_usage == "commercial" and eff.usage_licence == "non_commercial":
        raise SourceNotAllowed("SOURCE_NON_COMMERCIAL", "This source is restricted to non-commercial use", source=code)
    if eff.cost == "paid" and not accept_paid:
        raise SourceNotAllowed("SOURCE_PAID_NOT_ACCEPTED", "Paid source requires explicit acceptance", source=code)
    return eff


def list_sources(db: Session) -> list[dict]:
    out = []
    for row in db.scalars(select(DataSource).order_by(DataSource.code)):
        e = effective(db, row.code)
        out.append(
            {
                **e.__dict__,
                "terms_url": row.terms_url,
                "notes": row.notes,
                "blocked_by_deployment": get_settings().deployment_usage == "commercial"
                and e.usage_licence == "non_commercial",
            }
        )
    return out
