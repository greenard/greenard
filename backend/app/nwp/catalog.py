"""Catalogue des modèles NWP (§4.2.1 de la note d'architecture).

Les géométries de grille régulière ci-dessous sont des valeurs de référence : lors de la
préparation des champs invariants, la géométrie est relue dans les en-têtes GRIB et c'est
elle qui fait foi (voir `app.nwp.invariants`). Les pas de temps natifs sont documentés ici pour
le jalon 2 (marquage natif / interpolé).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.nwp.grids.regular import RegularGrid


@dataclass(frozen=True)
class ModelSpec:
    code: str
    name: str
    provider: str
    grid_type: str  # "regular" | "icosahedral"
    resolution: str
    color: str  # couleur de la couche carte
    regular_grid: RegularGrid | None = None
    icon_grid_name: str | None = None
    regional: bool = False
    # Paliers (échéance max h, pas natif h) par run
    native_steps: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    wind_heights_m: tuple[int, ...] = ()
    members: int = 1
    milestone: int = 1  # jalon à partir duquel le modèle est exploitable
    invariants: str = ""  # champs utilisés pour altitude / terre-mer
    notes: str = ""

    @property
    def max_lead_h(self) -> int:
        return max((s[-1][0] for s in self.native_steps.values() if s), default=0)

    def to_dict(self) -> dict:
        d = {
            "code": self.code,
            "name": self.name,
            "provider": self.provider,
            "grid_type": self.grid_type,
            "resolution": self.resolution,
            "color": self.color,
            "regional": self.regional,
            "native_steps": {k: [list(t) for t in v] for k, v in self.native_steps.items()},
            "max_lead_h": self.max_lead_h,
            "wind_heights_m": list(self.wind_heights_m),
            "members": self.members,
            "milestone": self.milestone,
            "invariants": self.invariants,
            "notes": self.notes,
        }
        if self.regular_grid is not None:
            g = self.regular_grid
            d["domain"] = {"lat_min": g.lat_min, "lat_max": g.lat_max, "lon_min": g.lon_min, "lon_max": g.lon_max}
        return d


GLOBAL_025_N2S_0_360 = RegularGrid(90.0, -0.25, 721, 0.0, 0.25, 1440, global_lon=True)
# Open data IFS/ENS : longitudes 180 → 179,75 (relu dans le GRIB à la préparation des invariants).
GLOBAL_025_N2S_180 = RegularGrid(90.0, -0.25, 721, 180.0, 0.25, 1440, global_lon=True)

MODELS: dict[str, ModelSpec] = {
    "gfs": ModelSpec(
        code="gfs",
        name="GFS",
        provider="NOAA/NCEP",
        grid_type="regular",
        resolution="0.25°",
        color="#1f77b4",
        regular_grid=GLOBAL_025_N2S_0_360,
        native_steps={"00/06/12/18": [(120, 1), (384, 3)]},
        wind_heights_m=(10, 20, 30, 40, 50, 80, 100),
        invariants="HGT:surface, LAND:surface (f000)",
    ),
    "ifs": ModelSpec(
        code="ifs",
        name="ECMWF IFS HRES (open data)",
        provider="ECMWF",
        grid_type="regular",
        resolution="0.25°",
        color="#d62728",
        regular_grid=GLOBAL_025_N2S_180,
        native_steps={"00/12": [(144, 3), (360, 6)], "06/18": [(144, 3)]},
        wind_heights_m=(10, 100),
        invariants="z (sfc) / g, lsm (step 0)",
        notes="0.1° native resolution is a paid/optional source (see §4.3.5).",
    ),
    "icon": ModelSpec(
        code="icon",
        name="ICON global",
        provider="DWD",
        grid_type="icosahedral",
        resolution="~13 km (R3B07)",
        color="#2ca02c",
        icon_grid_name="icon_grid_0026_R03B07_G",
        native_steps={"00/12": [(78, 1), (180, 3)], "06/18": [(78, 1), (120, 3)]},
        wind_heights_m=(10,),
        invariants="CLAT, CLON, HSURF, FR_LAND (time-invariant)",
        notes="Heights above 10 m are derived from model levels (HHL) in milestone 2.",
    ),
    "icon_eu": ModelSpec(
        code="icon_eu",
        name="ICON-EU",
        provider="DWD",
        grid_type="regular",
        resolution="0.0625°",
        color="#ff7f0e",
        regular_grid=RegularGrid(29.5, 0.0625, 657, -23.5, 0.0625, 1377, global_lon=False),
        regional=True,
        native_steps={"00/06/12/18": [(78, 1), (120, 3)]},
        wind_heights_m=(10,),
        invariants="HSURF, FR_LAND (time-invariant)",
        notes="Regional domain: southern Morocco is outside (southern limit ≈ 29.5°N).",
    ),
    # --- Ensembles : recherche de points disponible, téléchargement au jalon 2 -----------------
    "gefs": ModelSpec(
        code="gefs",
        name="GEFS",
        provider="NOAA/NCEP",
        grid_type="regular",
        resolution="0.25°",
        color="#9467bd",
        regular_grid=GLOBAL_025_N2S_0_360,
        native_steps={"00/06/12/18": [(240, 3), (384, 6)]},
        members=31,
        milestone=2,
        invariants="from GFS (same 0.25° grid)",
    ),
    "ens": ModelSpec(
        code="ens",
        name="ECMWF ENS (open data)",
        provider="ECMWF",
        grid_type="regular",
        resolution="0.25°",
        color="#8c564b",
        regular_grid=GLOBAL_025_N2S_180,
        native_steps={"00/12": [(144, 3), (360, 6)]},
        members=51,
        milestone=2,
        invariants="from IFS (same 0.25° grid)",
    ),
}

# Modèles dont les champs invariants sont empruntés à un autre modèle sur la même grille.
INVARIANTS_FROM = {"gefs": "gfs", "ens": "ifs"}


def get_model(code: str) -> ModelSpec:
    from app.core.errors import NotFound

    try:
        return MODELS[code]
    except KeyError:
        raise NotFound("MODEL_UNKNOWN", f"Unknown model {code!r}", model=code) from None
