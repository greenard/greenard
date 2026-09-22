"""Sources disponibles par modèle et ordre de préférence en mode « auto »."""

from __future__ import annotations

from app.core.errors import AppError
from app.nwp.sources.base import Source
from app.nwp.sources.dwd import IconDwd, IconEuDwd
from app.nwp.sources.ecmwf import EnsAws, EnsEcmwf, IfsAws, IfsEcmwf
from app.nwp.sources.noaa import GefsAws, GefsNomads, GfsAws, GfsNomads
from app.nwp.sources.openmeteo import OpenMeteo

_NATIVE = {
    "gfs": {"nomads": GfsNomads, "aws": GfsAws},
    "gefs": {"nomads": GefsNomads, "aws": GefsAws},
    "ifs": {"aws": IfsAws, "ecmwf": IfsEcmwf},
    "ens": {"aws": EnsAws, "ecmwf": EnsEcmwf},
    "icon": {"dwd": IconDwd},
    "icon_eu": {"dwd": IconEuDwd},
}

# Ordre « auto » : extraction au point / découpage serveur d'abord, fichiers globaux ensuite.
# ENS : Open-Meteo avant AWS (≈ 11 Go par run complet en fichiers globaux).
PREFERENCE = {
    "gfs": ["nomads", "aws", "open_meteo"],
    "gefs": ["nomads", "open_meteo", "aws"],
    "ifs": ["aws", "ecmwf", "open_meteo"],
    "ifs_9km": ["open_meteo"],
    "ens": ["open_meteo", "aws", "ecmwf"],
    "icon": ["dwd", "open_meteo"],
    "icon_eu": ["dwd", "open_meteo"],
}


def sources_for(model: str) -> list[str]:
    return PREFERENCE.get(model, [])


def get_source(model: str, code: str) -> Source:
    if code == "open_meteo":
        return OpenMeteo(model)
    try:
        return _NATIVE[model][code]()
    except KeyError:
        raise AppError(
            "SOURCE_MODEL_UNSUPPORTED", "Source not available for this model", model=model, source=code
        ) from None
