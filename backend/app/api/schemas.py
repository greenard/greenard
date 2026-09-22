import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.db.models import ProjectRole

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def _email(v: str) -> str:
    # Validation volontairement souple : les serveurs locaux utilisent souvent des domaines internes
    # (.local, .lan, .intra) refusés par les validateurs stricts.
    v = v.strip().lower()
    if len(v) > 320 or not _EMAIL_RE.match(v):
        raise ValueError("invalid email")
    return v


Email = Annotated[str, AfterValidator(_email)]


class LoginIn(BaseModel):
    email: Email
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    full_name: str
    is_admin: bool
    is_active: bool
    locale: str
    created_at: datetime
    last_login_at: datetime | None


class UserCreate(BaseModel):
    email: Email
    full_name: str = ""
    password: str
    is_admin: bool = False
    locale: str = Field("fr", pattern="^(fr|en)$")


class UserUpdate(BaseModel):
    full_name: str | None = None
    is_admin: bool | None = None
    is_active: bool | None = None
    locale: str | None = Field(None, pattern="^(fr|en)$")
    password: str | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class MeUpdate(BaseModel):
    full_name: str | None = None
    locale: str | None = Field(None, pattern="^(fr|en)$")


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    display_tz: str = Field("UTC", pattern="^(UTC|Africa/Casablanca)$")


class ProjectUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    display_tz: str | None = Field(None, pattern="^(UTC|Africa/Casablanca)$")
    archive_enabled: bool | None = None
    archive_models: list[str] | None = None
    archive_max_lead_h: int | None = Field(None, ge=6, le=384)


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str
    display_tz: str
    created_at: datetime
    role: ProjectRole
    site_count: int
    archive_enabled: bool = False
    archive_models: list[str] = []
    archive_max_lead_h: int = 168


class MemberIn(BaseModel):
    email: Email
    role: ProjectRole


class MemberOut(BaseModel):
    user_id: int
    email: str
    full_name: str
    role: ProjectRole


class SiteIn(BaseModel):
    """Site saisi soit en lat/lon (décimal ou DMS), soit en x/y + crs."""

    name: str = Field(min_length=1, max_length=200)
    lat: str | float | None = None
    lon: str | float | None = None
    x: float | None = None
    y: float | None = None
    crs: str = "EPSG:4326"


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    lat: float
    lon: float
    input_crs: str
    input_x: float | None
    input_y: float | None
    dem_elevation_m: float | None
    created_at: datetime


class ConvertIn(BaseModel):
    lat: str | float | None = None
    lon: str | float | None = None
    x: float | None = None
    y: float | None = None
    crs: str = "EPSG:4326"


class GridPointsRequest(BaseModel):
    models: list[str] = Field(min_length=1)
    method: str = Field("bracket", pattern="^(bracket|nearest)$")
    n: int = 4


class SelectionIn(BaseModel):
    selected: bool


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    status: str
    progress: float
    message: str
    result: dict
    started_at: datetime
    finished_at: datetime | None
