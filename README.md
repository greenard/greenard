# Greenard — Prévision météo & production éolienne

Application web de prévision météorologique (modèles NWP ouverts : GFS, ECMWF IFS, ICON, ICON-EU)
et de prévision de production éolienne (PyWake, pertes IEC 61400-15-2, probabiliste).

- Architecture et décisions : [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (v0.3)
- Hypothèses et limites : [`docs/LIMITS.md`](docs/LIMITS.md)
- Démonstrations : [jalon 1](docs/demo/jalon1/README.md), [jalon 2](docs/demo/jalon2/README.md), [jalon 3](docs/demo/jalon3/README.md)
- Accès ECMWF 0,1° : [`docs/ECMWF_0p1.md`](docs/ECMWF_0p1.md)

## Avancement

| Jalon | Contenu | État |
|---|---|---|
| 1 | Carte, points de grille, authentification | **livré** |
| 2 | Téléchargement des prévisions | **livré** |
| 3 | Import du parc et du mât, terrain | **livré** |
| 4 | PyWake et terrain | à venir |
| 5 | Pertes et probabiliste | à venir |
| 6 | Calibration ML et évaluation | à venir |

## Installation sur serveur local (Docker Compose)

Prérequis : Linux, Docker ≥ 24 avec le plugin Compose, accès Internet sortant vers
`noaa-gfs-bdp-pds.s3.amazonaws.com`, `ecmwf-forecasts.s3.amazonaws.com`, `opendata.dwd.de`,
`copernicus-dem-30m.s3.amazonaws.com`, `esa-worldcover.s3.eu-central-1.amazonaws.com` (directement ou via le proxy de l'entreprise).
Dimensionnement recommandé : 16 vCPU, 64 Go de RAM, 1 To SSD (minimum 8 vCPU / 32 Go).

```bash
cp .env.example .env          # puis renseigner POSTGRES_PASSWORD, GREENARD_SECRET_KEY, GREENARD_HOSTNAME
openssl rand -hex 32          # valeur possible pour GREENARD_SECRET_KEY
docker compose up -d --build
# premier administrateur (saisie interactive du mot de passe) :
docker compose exec api greenard create-admin --email admin@entreprise.ma --name "Administrateur"
# (facultatif) pré-télécharger les champs invariants des modèles :
docker compose exec api greenard prepare-invariants gfs ifs icon icon_eu
```

L'application est servie en HTTPS sur `https://<GREENARD_HOSTNAME>/`. Par défaut, Caddy génère une
CA interne (`GREENARD_TLS=internal`) ; pour utiliser le certificat de l'entreprise, déposer
`cert.pem` et `key.pem` dans `deploy/certs/` et définir `GREENARD_TLS="/certs/cert.pem /certs/key.pem"`.

Services : `db` (PostgreSQL 16 + PostGIS 3.4), `redis`, `api` (FastAPI, applique les migrations au
démarrage), `worker-io` (Celery : téléchargements), `beat` (Celery beat : archivage automatique horaire),
`frontend` (nginx), `proxy` (Caddy, seul service exposé).

Prévisions stockées dans le volume `greenard_appdata` (`/data/forecasts`, NetCDF compressé par extrait).
Prévoir de l'espace : quelques Mo par extrait au point, mais des téléchargements transitoires de
plusieurs centaines de Mo (champs globaux AWS/ECMWF, plafond `GREENARD_MAX_DOWNLOAD_MB`).

**Sauvegardes** : base `docker compose exec db pg_dump -U greenard -Fc greenard > greenard.dump` ;
fichiers : volume `greenard_appdata` (champs invariants, et à partir du jalon 2 archives de prévisions).
Restauration : `pg_restore -c -d greenard` puis restauration du volume.

## Développement

Backend (environnement conda-forge **obligatoire** : les wheels pip d'ecCodes, rasterio et netCDF4
embarquent des bibliothèques natives incompatibles entre elles) :

```bash
cd backend
micromamba create -f environment.yml -n greenard && micromamba activate greenard
pip install -e ".[dev]"
export GREENARD_DATABASE_URL=postgresql+psycopg://greenard:greenard@localhost:5432/greenard
export GREENARD_COOKIE_SECURE=false        # HTTP en local uniquement
alembic upgrade head
greenard create-admin --email admin@example.org
uvicorn app.main:app --reload --port 8000
celery -A app.tasks.celery_app worker -Q io,celery   # ou GREENARD_CELERY_EAGER=true sans Redis
```

Frontend :

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxy /api → :8000)
```

Tests :

```bash
cd backend
TEST_DATABASE_URL=postgresql+psycopg://greenard:greenard@localhost:5432/greenard_test pytest   # unitaires + API
pytest -m network     # sources réelles (GFS, IFS, DWD, Copernicus DEM)
GREENARD_TEST_ICON_GRID=/chemin/icon_grid_0026_R03B07_G.nc pytest -m slow   # vraie grille ICON
cd ../frontend && npm test && npm run typecheck
```
