"""Modèles SQLAlchemy du jalon 1 (voir §6 de docs/ARCHITECTURE.md).

Le catalogue des modèles NWP est défini dans le code (`app.nwp.catalog`), versionné avec lui ;
les tables ne référencent un modèle que par son code.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ProjectRole(StrEnum):
    owner = "owner"
    engineer = "engineer"
    viewer = "viewer"


class User(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    locale: Mapped[str] = mapped_column(String(5), default="fr")
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "auth_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), index=True)
    object_type: Mapped[str] = mapped_column(String(64), default="")
    object_id: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Project(Base):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    display_tz: Mapped[str] = mapped_column(String(64), default="UTC")
    default_crs: Mapped[str] = mapped_column(String(32), default="EPSG:4326")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Archivage automatique des runs sur les points sélectionnés (calibration, jalon 6)
    archive_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    archive_models: Mapped[list[str]] = mapped_column(JSONType, default=list, server_default="[]")
    archive_max_lead_h: Mapped[int] = mapped_column(Integer, default=168, server_default="168")

    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    sites: Mapped[list["Site"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_member"

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class Site(Base):
    __tablename__ = "site"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    input_crs: Mapped[str] = mapped_column(String(32), default="EPSG:4326")
    input_x: Mapped[float | None] = mapped_column(Float)
    input_y: Mapped[float | None] = mapped_column(Float)
    dem_elevation_m: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="sites")
    grid_links: Mapped[list["SiteGridPoint"]] = relationship(back_populates="site", cascade="all, delete-orphan")


class GridPoint(Base):
    """Nœud (grille régulière) ou cellule (grille icosaédrique) d'un modèle NWP."""

    __tablename__ = "grid_point"
    __table_args__ = (UniqueConstraint("model_code", "native_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_code: Mapped[str] = mapped_column(String(32), index=True)
    # Grille régulière : i * nlon + j ; grille icosaédrique : index de cellule (base 0).
    native_index: Mapped[int] = mapped_column(BigInteger)
    i: Mapped[int | None] = mapped_column(Integer)
    j: Mapped[int | None] = mapped_column(Integer)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    model_elevation_m: Mapped[float | None] = mapped_column(Float)
    land_fraction: Mapped[float | None] = mapped_column(Float)
    dem_elevation_m: Mapped[float | None] = mapped_column(Float)
    dem_cell_mean_m: Mapped[float | None] = mapped_column(Float)


class SiteGridPoint(Base):
    __tablename__ = "site_grid_point"

    site_id: Mapped[int] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"), primary_key=True)
    grid_point_id: Mapped[int] = mapped_column(ForeignKey("grid_point.id", ondelete="CASCADE"), primary_key=True)
    model_code: Mapped[str] = mapped_column(String(32), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(16))  # "bracket" | "nearest"
    distance_m: Mapped[float] = mapped_column(Float)
    azimuth_deg: Mapped[float] = mapped_column(Float)
    elevation_diff_m: Mapped[float | None] = mapped_column(Float)
    contains_site: Mapped[bool] = mapped_column(Boolean, default=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)

    site: Mapped[Site] = relationship(back_populates="grid_links")
    grid_point: Mapped[GridPoint] = relationship()


class TaskLog(Base):
    __tablename__ = "task_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    celery_id: Mapped[str | None] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataSource(Base):
    """Gouvernance des sources de données : licence d'usage et coût (§4.3.5)."""

    __tablename__ = "data_source"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    usage_licence: Mapped[str] = mapped_column(String(16))  # open | non_commercial | contract
    cost: Mapped[str] = mapped_column(String(8))  # free | paid
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    implemented: Mapped[bool] = mapped_column(Boolean, default=True)
    terms_url: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class NwpRun(Base):
    __tablename__ = "nwp_run"
    __table_args__ = (UniqueConstraint("model_code", "init_time", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    model_code: Mapped[str] = mapped_column(String(32), index=True)
    init_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(32))
    first_retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ForecastJob(Base):
    """Demande de téléchargement (un site, plusieurs modèles) ou archivage automatique."""

    __tablename__ = "forecast_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("site.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="manual")  # manual | archive
    params: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task_log.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    extracts: Mapped[list["ForecastExtract"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ForecastExtract.id"
    )


class ForecastExtract(Base):
    """Résultat d'un modèle pour un job : fichier NetCDF brut (échéances natives) et traçabilité."""

    __tablename__ = "forecast_extract"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("forecast_job.id", ondelete="CASCADE"), index=True)
    model_code: Mapped[str] = mapped_column(String(32))
    nwp_run_id: Mapped[int | None] = mapped_column(ForeignKey("nwp_run.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(32), default="")
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | success | failure
    error: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    grid_point_ids: Mapped[list[int]] = mapped_column(JSONType, default=list)
    n_members: Mapped[int] = mapped_column(Integer, default=0)
    n_times: Mapped[int] = mapped_column(Integer, default=0)
    variables: Mapped[list[str]] = mapped_column(JSONType, default=list)
    missing_variables: Mapped[list[str]] = mapped_column(JSONType, default=list)
    estimate: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    file_uri: Mapped[str] = mapped_column(String(500), default="")
    file_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[ForecastJob] = relationship(back_populates="extracts")
    run: Mapped[NwpRun | None] = relationship()


# --------------------------------------------------------------------------------------------
# Jalon 3 : parc, éoliennes, mât, terrain
# --------------------------------------------------------------------------------------------


class TurbineType(Base):
    __tablename__ = "turbine_type"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    manufacturer: Mapped[str] = mapped_column(String(200), default="")
    rotor_d_m: Mapped[float] = mapped_column(Float)
    rated_kw: Mapped[float] = mapped_column(Float)
    hub_heights_m: Mapped[list[float]] = mapped_column(JSONType, default=list)
    rho_ref: Mapped[float] = mapped_column(Float, default=1.225)
    cut_in_ms: Mapped[float] = mapped_column(Float)
    cut_out_ms: Mapped[float] = mapped_column(Float)
    restart_ms: Mapped[float | None] = mapped_column(Float)  # hystérésis haut vent
    # [{ws, power_kw, ct}] à la densité de référence
    power_curve: Mapped[list[dict[str, float]]] = mapped_column(JSONType, default=list)
    # [{rho, points: [{ws, power_kw, ct}]}] si le fichier contient plusieurs densités
    density_curves: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    # [{t_c, max_kw}] déclassement haute température (facultatif)
    temp_derating: Mapped[list[dict[str, float]]] = mapped_column(JSONType, default=list)
    source_format: Mapped[str] = mapped_column(String(16), default="")
    source_filename: Mapped[str] = mapped_column(String(300), default="")
    file_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WindFarm(Base):
    __tablename__ = "wind_farm"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    is_neighbour: Mapped[bool] = mapped_column(Boolean, default=False)
    layout_filename: Mapped[str] = mapped_column(String(300), default="")
    layout_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    turbines: Mapped[list["Turbine"]] = relationship(
        back_populates="farm", cascade="all, delete-orphan", order_by="Turbine.label"
    )


class Turbine(Base):
    __tablename__ = "turbine"
    __table_args__ = (UniqueConstraint("farm_id", "label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(ForeignKey("wind_farm.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(64))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    input_crs: Mapped[str] = mapped_column(String(32), default="EPSG:4326")
    input_x: Mapped[float | None] = mapped_column(Float)
    input_y: Mapped[float | None] = mapped_column(Float)
    hub_height_m: Mapped[float] = mapped_column(Float)
    type_id: Mapped[int] = mapped_column(ForeignKey("turbine_type.id", ondelete="RESTRICT"))
    dem_elevation_m: Mapped[float | None] = mapped_column(Float)

    farm: Mapped[WindFarm] = relationship(back_populates="turbines")
    type: Mapped[TurbineType] = relationship()


class MetMast(Base):
    __tablename__ = "met_mast"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True))
    input_crs: Mapped[str] = mapped_column(String(32), default="EPSG:4326")
    input_x: Mapped[float | None] = mapped_column(Float)
    input_y: Mapped[float | None] = mapped_column(Float)
    elevation_m: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sensors: Mapped[list["MastSensor"]] = relationship(
        back_populates="mast", cascade="all, delete-orphan", order_by="MastSensor.height_m"
    )
    datasets: Mapped[list["MastDataset"]] = relationship(back_populates="mast", cascade="all, delete-orphan")


class MastSensor(Base):
    """Capteur : grandeur (ws, wd, temp, rh, pressure) à une hauteur, avec l'orientation du bras."""

    __tablename__ = "mast_sensor"
    __table_args__ = (UniqueConstraint("mast_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mast_id: Mapped[int] = mapped_column(ForeignKey("met_mast.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64))  # ex. ws_100_n, wd_97
    kind: Mapped[str] = mapped_column(String(16))  # ws | wd | temp | rh | pressure
    height_m: Mapped[float] = mapped_column(Float)
    boom_dir_deg: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16), default="")

    mast: Mapped[MetMast] = relationship(back_populates="sensors")


class MastDataset(Base):
    __tablename__ = "mast_dataset"

    id: Mapped[int] = mapped_column(primary_key=True)
    mast_id: Mapped[int] = mapped_column(ForeignKey("met_mast.id", ondelete="CASCADE"), index=True)
    original_filename: Mapped[str] = mapped_column(String(300))
    format: Mapped[str] = mapped_column(String(32))
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    time_reference: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    file_uri: Mapped[str] = mapped_column(String(500))
    file_hash: Mapped[str] = mapped_column(String(64))
    t_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    t_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_records: Mapped[int] = mapped_column(Integer, default=0)
    qc_summary: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    mast: Mapped[MetMast] = relationship(back_populates="datasets")


class TerrainLayer(Base):
    __tablename__ = "terrain_layer"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # dem | landcover | roughness | roughness_map
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(64))  # copernicus_glo30 | esa_worldcover | upload | wasp_map
    file_uri: Mapped[str] = mapped_column(String(500))
    crs: Mapped[str] = mapped_column(String(64), default="")
    resolution_m: Mapped[float | None] = mapped_column(Float)
    bbox: Mapped[list[float]] = mapped_column(JSONType, default=list)  # lon_min, lat_min, lon_max, lat_max
    z0_table: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
