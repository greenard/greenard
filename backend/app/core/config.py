"""Configuration de l'application (variables d'environnement, préfixe GREENARD_)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GREENARD_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://greenard:greenard@localhost:5432/greenard"
    redis_url: str = "redis://localhost:6379/0"
    # Exécute les tâches Celery dans le processus appelant (tests, démonstration sans Redis).
    celery_eager: bool = False

    secret_key: str = "change-me-in-production"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    # False uniquement en développement local sans HTTPS.
    cookie_secure: bool = True
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    cors_origins: list[str] = []

    data_dir: Path = Path("./data")
    max_upload_mb: int = 20

    # Usage de l'instance : "internal" (non commercial) ou "commercial" (§4.3.5 de la note).
    deployment_usage: Literal["internal", "commercial"] = "internal"

    # Emprise (lat_min, lat_max, lon_min, lon_max) des champs invariants mis en cache pour les
    # grilles régulières. Par défaut : Maroc et marges.
    invariants_bbox: tuple[float, float, float, float] = (19.0, 37.5, -19.5, 0.5)

    # MNT : URL de base des tuiles Copernicus GLO-30 (COG), ou répertoire local de tuiles.
    dem_base_url: str = "https://copernicus-dem-30m.s3.amazonaws.com"
    dem_local_dir: Path | None = None

    # Fichier de grille ICON complet (facultatif) : permet de déterminer la cellule contenant le site.
    icon_grid_file: Path | None = None

    elevation_alert_m: float = 100.0

    # Téléchargement des prévisions
    max_download_mb: int = 3000  # plafond par extrait (modèle × run), estimé avant téléchargement
    download_workers: int = 8
    nomads_base_url: str = "https://nomads.ncep.noaa.gov"
    ecmwf_base_url: str = "https://data.ecmwf.int/forecasts"
    ecmwf_aws_url: str = "https://ecmwf-forecasts.s3.amazonaws.com"
    dwd_base_url: str = "https://opendata.dwd.de/weather/nwp"
    # Open-Meteo : public (non commercial) | api_key (abonnement) | self_hosted
    open_meteo_mode: Literal["public", "api_key", "self_hosted"] = "public"
    open_meteo_api_key: str = ""
    open_meteo_base_url: str = ""  # instance auto-hébergée
    # Archivage automatique des runs (Celery beat), en minutes
    archive_interval_min: int = 60
    icon_eu_margin_cells: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
