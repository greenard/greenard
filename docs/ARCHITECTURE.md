# Note d'architecture — Greenard

> Application web de prévision météorologique (Module A « Météo ») et de prévision de production éolienne (Module B « Énergie »).
>
> Version 0.4 — 2026-09-22 — mises à jour issues des jalons 1 et 2 (voir « Écarts constatés » en §11).
>
> Changements par rapport à la v0.1 :
> - authentification multi-utilisateurs dès le jalon 1 (§8.1) ;
> - speed-ups WAsP Engineering comme voie principale de transposition, avec spécification du format d'import (§5.3) ;
> - sources de données gouvernées par licence (non commercial / commercial) et par coût (gratuit / payant, activation explicite) (§4.3.5) ;
> - déploiement sur serveur local (§2.5) ;
> - dimensionnement pour des parcs de 59 à 400 MW (§5.2, étape 4).

---

## Sommaire

1. [Principes directeurs](#1-principes-directeurs)
2. [Stack technique retenue et justification](#2-stack-technique-retenue-et-justification)
3. [Vue d'ensemble : services et flux](#3-vue-densemble--services-et-flux)
4. [Module A — Météo](#4-module-a--météo)
5. [Module B — Énergie](#5-module-b--énergie)
6. [Schéma de base de données](#6-schéma-de-base-de-données)
7. [API REST (esquisse)](#7-api-rest-esquisse)
8. [Exigences transversales](#8-exigences-transversales)
9. [Stratégie de tests](#9-stratégie-de-tests)
10. [Plan de jalons](#10-plan-de-jalons)
11. [Limites identifiées et journal des décisions](#11-limites-identifiées-et-journal-des-décisions)

---

## 1. Principes directeurs

| Principe | Conséquence concrète |
|---|---|
| **Honnêteté des données** | Chaque valeur d'une série temporelle porte un drapeau `native` / `interpolated` et le nom de la méthode. Rien n'est « lissé » en silence. |
| **Données brutes des modèles** | En production, les valeurs viennent du point de grille lui-même (GRIB natif), sans la réduction d'échelle implicite appliquée par certaines API (voir §4.3.3). |
| **Reproductibilité** | Un run = un enregistrement immuable : run NWP (date d'initialisation, membres), versions logicielles, paramètres, hypothèses de pertes, empreinte (hash) des fichiers d'entrée. |
| **Séparation calcul / interface** | Tout calcul de plus de 2 s passe par une file de tâches, avec progression visible. L'API reste réactive. |
| **Libre et gratuit par défaut** | Aucune dépendance logicielle sous licence payante. Les résultats d'outils propriétaires dont vous avez la licence (WAsP Engineering) entrent par **import de fichiers** (§5.3). Les sources de données payantes restent optionnelles, désactivées par défaut et activées explicitement (§4.3.5). |
| **UTC partout** | Stockage et calcul en UTC ; l'heure locale n'intervient qu'à l'affichage. |

---

## 2. Stack technique retenue et justification

### 2.1 Backend

| Brique | Choix | Justification | Alternative écartée |
|---|---|---|---|
| Langage | **Python 3.11+** (3.12 visé) | Écosystème scientifique (xarray, PyWake, WindKit), tout est natif. | — |
| API | **FastAPI** + Pydantic v2 | Validation stricte des entrées (imports de fichiers, coordonnées), OpenAPI généré automatiquement pour le frontend (client TS typé). | Django : trop lourd pour une API de calcul. |
| ORM / migrations | **SQLAlchemy 2** + **GeoAlchemy2** + **Alembic** | Gestion PostGIS mature, migrations versionnées. | SQLModel : moins mature sur la géométrie. |
| Données maillées | **xarray**, **cfgrib/ecCodes**, **netCDF4**, **zarr** | Standard de fait pour les GRIB2/NetCDF et le découpage spatial. | pygrib : moins intégré à xarray. |
| Géodésie | **pyproj** (PROJ ≥ 9) | Transformations UTM / Lambert Merchich, calcul géodésique (distance, azimut) sur l'ellipsoïde. | — |
| Voisinage | **scipy.spatial.cKDTree** | Recherche des plus proches voisins sur la grille icosaédrique ICON (≈ 2,9 M cellules) en quelques ms après construction. | BallTree scikit-learn : équivalent mais dépendance plus lourde. |
| Sillages | **PyWake** (DTU, licence MIT) | Référence ouverte, modèles de la littérature déjà implémentés, mode séries temporelles, `XRSite` pour speed-ups. | FLORIS (NREL) : bon, mais l'intégration d'un site hétérogène est moins directe. |
| Formats éoliens | **WindKit** (DTU, BSD-3) | Lecture des `.map`, `.wtg`, `.tab`, `.gwc` WAsP, structures de données compatibles PyWake. | Parsers maison : à écrire seulement si WindKit échoue sur un fichier. |
| Écoulement en relief | **Import des speed-ups WAsP Engineering** (voie principale, vous disposez de la licence) ; **WindNinja** (USFS, libre) en repli, dans un conteneur séparé | Les speed-ups et déviations sont calculés par l'équipe dans WAsP Engineering, hors de l'application, puis importés (§5.3). L'application reste 100 % libre et reprend les résultats du modèle de référence. WindNinja couvre les projets sans étude WAsP. | Pilotage direct de WAsP via PyWAsP : exige une licence PyWAsP distincte de la licence WAsP desktop, à vérifier ; possible plus tard derrière la même interface. |
| Raster | **rasterio / GDAL** | MNT, occupation du sol, reprojection. | — |
| ML | **LightGBM** (objectif `quantile`) | Rapide, gère nativement les valeurs manquantes, régression quantile intégrée. XGBoost reste possible via une interface commune. | Réseaux de neurones : peu justifiés avec ~1 an de données de calibration. |
| Scores probabilistes | **scoringrules** (ou implémentation interne testée) | CRPS d'ensemble et CRPS paramétrique, pinball loss. | properscoring : n'est plus maintenu. |
| Rapports | **Jinja2 + WeasyPrint** (HTML → PDF), figures **matplotlib** | Rendu PDF sans navigateur headless, figures stables et reproductibles. | Export statique Plotly (kaleido) : dépend d'un Chromium, fragile en conteneur. |
| Exports | **openpyxl** (XLSX), **xarray** (NetCDF CF-1.8) | Métadonnées dans les attributs NetCDF et dans un onglet `metadata` du XLSX. | — |

### 2.2 Calculs longs

**Celery + Redis**, plutôt que RQ :

- files séparées selon la nature du travail :
  - `io` : téléchargements NWP, limités par le réseau ;
  - `compute` : PyWake et WindNinja, limités par le CPU, concurrence réduite ;
  - `ml` : entraînement ;
- **Celery beat** pour l'ingestion périodique des nouveaux runs et **l'archivage automatique** (indispensable pour ICON, voir §4.3.4) ;
- progression publiée dans l'état de la tâche (`update_state(meta={pct, step, message})`), relayée au frontend par **SSE** (Server-Sent Events), avec repli sur une interrogation périodique (polling).

RQ serait plus simple, mais n'offre ni ordonnanceur intégré ni routage fin des files.

### 2.3 Persistance

- **PostgreSQL 16 + PostGIS 3** pour les métadonnées : projets, sites, points de grille, parcs, runs, configurations.
- **Fichiers volumineux** (extraits NWP, séries du mât, résultats de production) : **NetCDF/Zarr et Parquet** sur un volume, via une abstraction `fsspec`. Le passage à **MinIO** (compatible S3, libre) ne demande alors aucune modification de code.
  - *Justification* : 14 jours × 144 pas/jour × 51 membres × 40 éoliennes × 5 quantiles ≈ 73 M valeurs par run. Stocker cela ligne par ligne en SQL serait inefficace ; la base ne garde que les agrégats et le chemin du fichier.
- TimescaleDB n'est pas retenu à ce stade : pas nécessaire tant que les séries restent dans des fichiers.

### 2.4 Frontend

| Brique | Choix | Justification |
|---|---|---|
| Framework | **React 18 + TypeScript + Vite** | Imposé. Vite pour la rapidité de build. |
| Carte | **MapLibre GL JS** | Rendu WebGL (des milliers de points de grille sans ralentissement), couches activables, relief ombré. Libre, sans jeton. Fonds : OSM / OpenFreeMap, plus une couche relief Terrarium. |
| Graphiques | **Plotly.js** (bundle partiel) | Waterfall (cascade de pertes), `barpolar` (rose des vents), heatmaps et bandes de quantiles disponibles nativement ; zoom synchronisé multi-séries. ECharts est plus léger mais sa rose des vents et sa cascade demandent plus de code. |
| État / données | **TanStack Query** + client généré depuis l'OpenAPI | Cache, reprise sur erreur, types partagés avec le backend. |
| i18n | **react-i18next** (FR par défaut, EN) | Les messages d'erreur du backend sont des **codes** traduits côté client. |
| Formulaires | react-hook-form + zod | Validation côté client alignée sur les schémas Pydantic. |

### 2.5 Déploiement

**Cible : serveur local (Linux) sous Docker Compose.** Services : `db` (postgis), `redis`, `api`, `worker-io`, `worker-compute`, `beat`, `frontend` (nginx), `proxy` (Caddy) et `windninja` (profil optionnel).

- Image Python de base **micromamba / conda-forge** : ecCodes, GDAL et PROJ y sont fournis de façon fiable, contrairement aux wheels pip.
- Configuration par variables d'environnement (`.env.example` fourni) ; secrets (clé JWT, mot de passe BDD, clés d'API éventuelles) hors du dépôt.
- **HTTPS** sur le réseau interne via Caddy, avec certificat de l'entreprise ou CA interne. Seul le port 443 est exposé.
- **Accès Internet sortant** requis vers NOAA/AWS, ECMWF, DWD, Open-Meteo et Copernicus. Le proxy d'entreprise est pris en charge (`HTTPS_PROXY`, `NO_PROXY`). Un fonctionnement totalement isolé (air-gap) n'est pas prévu.
- **Sauvegardes** : `pg_dump` quotidien et sauvegarde du volume de fichiers (script et procédure de restauration fournis).
- **Dimensionnement recommandé** :
  - 16 vCPU et 64 Go de RAM (minimum 8 vCPU / 32 Go) ;
  - 1 To de SSD : MNT, GRIB temporaires, archives de prévisions, résultats.
- README d'installation livré au jalon 1.

---

## 3. Vue d'ensemble : services et flux

```mermaid
flowchart LR
  subgraph Client
    UI[React + MapLibre + Plotly]
  end
  subgraph Serveur
    API[FastAPI]
    DB[(PostgreSQL/PostGIS)]
    R[(Redis)]
    WIO[Worker io<br/>téléchargements NWP]
    WC[Worker compute<br/>PyWake / MOS / pertes]
    WN[WindNinja<br/>conteneur CLI]
    BEAT[Celery beat<br/>ingestion + archivage]
    FS[(Stockage fichiers<br/>NetCDF/Zarr/Parquet)]
  end
  subgraph Sources ouvertes
    NOAA[NOAA NOMADS / AWS<br/>GFS, GEFS]
    ECMWF[ECMWF open data<br/>IFS, ENS, AIFS]
    DWD[DWD opendata<br/>ICON, ICON-EU, ICON-EPS]
    OM[Open-Meteo<br/>prototypage + archives]
    COP[Copernicus DEM GLO-30<br/>ESA WorldCover]
  end
  UI <-->|REST + SSE| API
  API --> DB
  API --> R
  R --> WIO & WC
  BEAT --> R
  WIO --> NOAA & ECMWF & DWD & OM
  WIO --> FS
  WC --> FS
  WC --> WN
  WC --> COP
  WC --> DB
```

### Chaîne de bout en bout (Module B)

```mermaid
flowchart TD
  A[Runs NWP : points sélectionnés<br/>déterministes + ensembles] --> B[Harmonisation temporelle<br/>U/V, pas cible, drapeaux]
  B --> C[MOS / calibration au mât<br/>biais secteur×saison, QM, LightGBM quantile]
  C --> D[Extrapolation verticale<br/>cisaillement mesuré ou loi log z0]
  D --> E[Transposition mât → éoliennes<br/>speed-up + turning par secteur, WindNinja]
  E --> F[PyWake XRSite<br/>sillage + turbulence + blocage option]
  F --> G[Correction densité IEC 61400-12-1<br/>T, p prévues]
  G --> H[Production brute par éolienne]
  H --> I[Pertes IEC 61400-15-2<br/>dynamiques / forfaitaires]
  I --> J[Production nette<br/>P10…P90, éolienne + parc]
  J --> K[Exports + rapport PDF + évaluation a posteriori]
```

### Arborescence du dépôt

```
backend/
  app/
    api/            # routes FastAPI (v1)
    core/           # config, logging, i18n des codes d'erreur
    db/             # modèles SQLAlchemy, migrations Alembic
    geo/            # conversions CRS, DMS, géodésie
    nwp/
      models/       # un adaptateur par modèle : gfs.py, ifs.py, icon.py, icon_eu.py, …
      grids/        # grilles régulières, grille icosaédrique ICON (KD-tree)
      sources/      # nomads.py, aws.py, ecmwf_opendata.py, dwd.py, openmeteo.py
      timeseries.py # U/V, harmonisation temporelle, drapeaux natif/interpolé
    mast/           # parsers (CSV, NRG txt, Campbell TOA5, Windographer), contrôle qualité
    farm/           # layout, éoliennes (.wtg/CSV), validation
    terrain/        # MNT, rugosité, WindNinja
    energy/         # PyWake, densité, pertes, probabiliste
    mos/            # calibration, ML, évaluation
    reports/        # exports, PDF
    tasks/          # tâches Celery
  tests/
frontend/
  src/{map,charts,modules/meteo,modules/energy,i18n,api}
docker/
docs/
```

---

## 4. Module A — Météo

### 4.1 A1 — Saisie de la localisation

| Mode | Détail |
|---|---|
| WGS84 | Degrés décimaux ou DMS. Parser tolérant : `33°35'12.3"N 7°36'W`, `33 35 12.3 N`, signe ou lettre N/S/E/W. |
| UTM | Zone + hémisphère + X/Y. **Détection automatique de la zone** : `zone = floor((lon + 180) / 6) + 1`. Pas d'exception norvégienne/Svalbard au Maroc. |
| Lambert Merchich | EPSG:26191 (Nord Maroc), 26192 (Sud Maroc), 26194 (Sahara Nord), 26195 (Sahara Sud). EPSG:26193 (Sahara) est déprécié : proposé en lecture seule. |
| Carte | Clic : coordonnées capturées en WGS84. |
| Fichier | CSV (colonnes `name, x, y, crs` ; `crs` accepte `EPSG:xxxx` ou `UTM29N`…) et KML (Placemarks). Validation ligne par ligne, avec rapport d'erreurs. |

La conversion est bidirectionnelle et **affichée en permanence** : WGS84 décimal, DMS, UTM (zone auto), et Lambert si une zone est applicable.

> ⚠️ **Correction du cahier des charges** : le Maroc ne couvre pas seulement les zones UTM 29N/30N. Les provinces du Sud (Laâyoune, Dakhla) sont en **28N** (longitudes −18° à −12°). La détection automatique les couvre toutes.
>
> ⚠️ **Précision Merchich → WGS84** : PROJ applique par défaut une transformation à 3 paramètres (Helmert), dont la précision est de l'ordre de **quelques mètres à ~10 m**. C'est suffisant pour la sélection des points de grille, mais c'est à signaler pour un layout d'éoliennes fourni en Lambert. L'application affiche la transformation utilisée (`pyproj.Transformer.description`) et sa précision déclarée.

### 4.2 A2 — Points de grille entourant le site

#### 4.2.1 Catalogue des modèles

Les valeurs sont à re-vérifier au jalon 1 : les producteurs font évoluer leurs flux.

| Modèle | Grille | Domaine | Pas natif / échéance max | Accès |
|---|---|---|---|---|
| **GFS** | 0,25° régulière | Global | 1 h jusqu'à 120 h, puis 3 h jusqu'à 384 h | NOMADS (grib filter avec découpage), AWS `noaa-gfs-bdp-pds` (requêtes par plage d'octets via `.idx`) |
| **ECMWF IFS HRES** (open data) | 0,25° régulière | Global | 3 h jusqu'à 144 h, puis 6 h jusqu'à 360 h (runs 00/12) | `ecmwf-opendata`, miroir AWS |
| **ECMWF AIFS** (option) | 0,25° | Global | 6 h, 15 j | `ecmwf-opendata` |
| **ICON global** | Icosaédrique R3B07, ~13 km, ≈ 2,95 M cellules | Global | 1 h jusqu'à 78 h, puis 3 h jusqu'à 180 h (runs 00/12) | `opendata.dwd.de` |
| **ICON-EU** | 0,0625° régulière (interpolée par le DWD), 1377 × 657 points | 29,5°N–70,5°N, 23,5°W–62,5°E (corrigé au jalon 1, la v0.2 indiquait à tort 23,5°N) | 1 h jusqu'à 78 h, puis 3 h jusqu'à 120 h | `opendata.dwd.de` |
| **GEFS** | 0,25° / 0,5° selon les champs | Global | 3 h, 16 j ; 31 membres | AWS `noaa-gefs-pds` |
| **ECMWF ENS** | 0,25° | Global | 3 h puis 6 h, 15 j ; 51 membres | `ecmwf-opendata` |
| **ICON-EPS** | Icosaédrique R3B06 (~26 km) | Global | 1 h puis 3 h/6 h, 180 h ; 40 membres | `opendata.dwd.de` |

> **« Plus haute résolution ouverte » ECMWF** : le flux open data standard est à 0,25°. L'ECMWF a annoncé en 2025 l'ouverture de son catalogue temps réel complet, dont la résolution native HRES (~9 km, 0,1°). Les modalités d'accès (portail, quotas, frais de service éventuels) sont à confirmer au jalon 2. **Si l'accès n'est pas gratuit, je le signalerai et resterai à 0,25°.**

#### 4.2.2 Grilles régulières (GFS, IFS, ICON-EU, GEFS, ENS)

- Normalisation des longitudes : GFS et IFS peuvent utiliser 0–360°, alors que le Maroc est en longitudes négatives. Une conversion unique `lon % 360` sert à l'indexation ; l'affichage reste en −180..180. **Cas testé explicitement.**
- **4 nœuds encadrants** : `i0 = floor((lat − lat0) / Δ)`, `j0 = floor((lon − lon0) / Δ)`, puis les nœuds (i0, i0+1) × (j0, j0+1). Les orientations de latitude décroissante (GFS : 90 → −90) sont gérées.
- **N plus proches (4, 9, 16)** : une fenêtre de candidats (±3 nœuds) est triée par **distance géodésique** (`pyproj.Geod.inv`, ellipsoïde WGS84). Pour 9 et 16, on obtient un bloc centré, pas nécessairement carré si le site est proche d'un nœud.

#### 4.2.3 ICON global (icosaédrique)

- Chargement du fichier de définition de grille DWD `icon_grid_0026_R03B07_G.nc` (variables `clat`, `clon` en **radians**), mis en cache dans le volume après le premier téléchargement.
- Conversion en vecteurs unitaires 3D : `(cos φ cos λ, cos φ sin λ, sin φ)`, puis construction d'un `cKDTree` **sérialisé** : quelques secondes au premier démarrage, lecture instantanée ensuite.
- Requête des k plus proches voisins. La distance de corde `c` donne l'angle au centre `θ = 2 arcsin(c/2)`, puis la distance est recalculée précisément par `Geod.inv`.
- On retient **aussi la cellule qui contient le site** (triangle ICON, via `vertex_of_cell` et `clat_vertices`/`clon_vertices`). Elle n'est pas toujours le plus proche centre, et c'est cette valeur que le modèle attribue au site.
- **Aucune hypothèse de grille régulière** : les index sont des index de cellules 1D, et les extractions GRIB se font par index.
- Même logique pour ICON-EPS (grille R3B06, fichier distinct).

#### 4.2.4 ICON-EU : contrôle de domaine

- Contrôle d'appartenance au domaine du modèle, avec une **marge de sécurité** paramétrable (par défaut 5 mailles). Près du bord, les conditions aux limites latérales dégradent la qualité de la prévision.
- Si le site est hors domaine ou dans la marge, le modèle est **désactivé** avec un message explicite (FR/EN), par exemple : *« ICON-EU ne couvre pas ce site (latitude 23,7°, domaine 29,5°–70,5°N). Utilisez un modèle global. »*
- En pratique au Maroc : couverture au nord d'environ 29,8°N (limite 29,5°N + marge de 5 mailles), soit jusqu'à Agadir / Tiznit. Guelmim, Tan-Tan, Tarfaya, Laâyoune et Dakhla sont **hors domaine**. La géométrie réelle est relue dans les en-têtes GRIB lors de la préparation des invariants et remplace la valeur du catalogue.

#### 4.2.5 Attributs affichés par point

| Attribut | Source |
|---|---|
| Distance (km) et azimut (°) site → point | `pyproj.Geod.inv` |
| Altitude modèle | GFS : `HGT:surface`. IFS : géopotentiel `z` (sfc, step 0) / 9,80665, présent dans le flux open data (vérifié au jalon 1). ICON / ICON-EU : `HSURF` (invariants DWD). |
| Altitude réelle | Copernicus DEM GLO-30 au point de grille **et** au site, avec la moyenne sur la maille (plus représentative pour la comparaison). |
| Écart d'altitude | Alerte si \|Δz\| > seuil (par défaut 100 m, modifiable). |
| Drapeau terre/mer | GFS `LAND`, IFS `lsm`, ICON `FR_LAND` (fraction). Un point est « mer » si la fraction de terre est < 0,5. Alerte si le site est à terre et le point en mer (côte atlantique). |

**Interface** : une couche MapLibre par modèle (couleur dédiée, interrupteur), un tableau synchronisé avec cases à cocher, sélection par clic, et une ligne site → point avec étiquette de distance et d'azimut.

### 4.3 A3 — Téléchargement des prévisions

#### 4.3.1 Variables

| Grandeur | Niveaux disponibles (indicatifs, confirmés au jalon 2) |
|---|---|
| Vent U/V | GFS : 10, 20, 30, 40, 50, 80, 100 m + niveaux pression. IFS open data : 10 et 100 m (vérifié au jalon 1 : pas de 200 m dans le flux open data) + niveaux pression. ICON / ICON-EU : 10 m + niveaux modèle, convertis en hauteur via `HHL`, pour fournir 80/100/120/180 m par interpolation verticale **marquée comme dérivée**. |
| Rafales | GFS `GUST` (instantanée) ; IFS `10fg` (maximum sur la période précédente) ; ICON `VMAX_10M` (maximum depuis la dernière sortie). **La sémantique diffère** : elle est stockée en métadonnée. |
| Température | 2 m + niveaux pression (850, 925 hPa) : utile comme indicateur de stabilité pour la calibration. |
| Pression | Surface (`sp` / `PS`) et niveau de la mer (`msl` / `PMSL`). |
| Humidité relative | 2 m (calculée depuis le point de rosée si seul celui-ci est fourni). |

**Conversion U/V ↔ vitesse/direction** (convention météorologique, direction d'où vient le vent) :

```
ws  = hypot(u, v)
wd  = (270 − degrees(atan2(v, u))) mod 360     # 0° = vient du Nord, 90° = vient de l'Est
u   = −ws · sin(radians(wd))
v   = −ws · cos(radians(wd))
```

Des tests unitaires couvrent les 4 cardinaux, les cas limites (ws = 0 → wd = NaN, et non 0) et un aller-retour sur 10⁵ tirages aléatoires.

#### 4.3.2 Harmonisation temporelle

Pas cibles : **10 min, 15 min, 1 h, 3 h**.

- **Aucun modèle ci-dessus ne fournit nativement du 10 ou 15 min sur le Maroc.** Ces pas sont **toujours interpolés**, et l'interface l'indique (colonne `is_native`, style de courbe en pointillé, légende).
- Chaque échéance est marquée `native` si elle existe dans le fichier source. La baisse de résolution temporelle (GFS 1 h → 3 h après 120 h ; ICON 1 h → 3 h après 78 h ; IFS 3 h → 6 h après 144 h) est donc **tracée point par point**, et un 1 h au-delà de 120 h pour GFS est marqué interpolé.
- **Méthode d'interpolation** :
  - on interpole les **composantes U et V**, jamais la direction brute (pas de problème de passage 359° → 1°) ;
  - direction = direction de (U, V) interpolés ;
  - vitesse : par défaut interpolation **scalaire** de ws, car le module de (U, V) interpolés sous-estime la vitesse quand la direction tourne vite. L'autre option (module des U/V interpolés) est sélectionnable, et le choix est enregistré dans le run ;
  - schéma **linéaire** ou **PCHIP** (spline cubique monotone). PCHIP est préféré à une spline cubique classique, qui dépasse les valeurs encadrantes et peut produire des vitesses négatives ;
  - grandeurs de type « maximum sur période » (rafales IFS/ICON) : **pas d'interpolation**, valeur attribuée à tous les sous-pas de la période (fonction en escalier) ;
  - agrégation vers un pas plus long (ex. 1 h → 3 h) : moyenne vectorielle pour la direction, moyenne scalaire pour la vitesse, maximum pour les rafales.
- **Limite documentée** : l'interpolation temporelle ne crée **aucune variabilité physique sous-horaire**. Une série à 10 min interpolée est lisse ; sa variance est très inférieure à celle d'une mesure à 10 min. C'est important pour le calcul de pertes non linéaires (hystérésis haut vent) : voir §5.4.

#### 4.3.3 Sources et stratégie

| Usage | Source | Remarques |
|---|---|---|
| Prototypage (jalon 2a) | **API Open-Meteo** | Rapide, unifiée. **Deux pièges** : (1) Open-Meteo applique par défaut une **correction d'altitude** et une sélection de maille ; on force `cell_selection=nearest`, on interroge aux **coordonnées exactes du nœud**, et on neutralise la correction d'altitude (`elevation=nan`) pour obtenir la valeur brute du point ; (2) **licence** : l'API gratuite est réservée à un **usage non commercial**. Trois modes, choisis par configuration : `public` (gratuit, non commercial, mode actuel), `api_key` (abonnement commercial, `customer-api.open-meteo.com`), `self_hosted` (instance Open-Meteo auto-hébergée, code AGPL). Voir §4.3.5. |
| Production (jalon 2b) | GRIB2 natif | GFS : NOMADS grib filter (sous-domaine + variables) ou AWS avec plages d'octets `.idx`. IFS : `ecmwf-opendata`, qui télécharge par variable ; le découpage est fait localement. ICON : fichiers `.grib2.bz2` par variable et par échéance, décompression puis extraction **par index de cellule**. |
| Découpage spatial | Local ou serveur | Le téléchargement de fichiers globaux par variable est inévitable pour IFS et ICON. Ils sont découpés immédiatement sur une boîte englobant les points sélectionnés (+ marge), puis supprimés. Seuls les extraits sont conservés (NetCDF, quelques Mo). |
| Ensembles | GEFS, ENS, ICON-EPS | Mêmes adaptateurs, avec une dimension `member`. |

#### 4.3.4 Archives historiques (calibration)

| Modèle | Disponibilité des archives de *prévisions* |
|---|---|
| GFS / GEFS | AWS (`noaa-gfs-bdp-pds`, `noaa-gefs-pds`) : plusieurs années. Bon. |
| IFS / ENS open data | Miroir AWS `ecmwf-forecasts` depuis 2023 environ. Le portail ECMWF ne garde que quelques jours. |
| ICON / ICON-EU / ICON-EPS | **DWD opendata ne garde que ~24 h.** Pas d'archive ouverte des fichiers natifs. |
| Tous | **Open-Meteo Historical Forecast API / Previous Runs API** : séries de prévisions archivées (selon les modèles, depuis 2021–2022). Solution pratique pour la calibration, avec les réserves de licence ci-dessus. |

> **Conséquence** : un **archiveur automatique** (Celery beat) est mis en place **dès le jalon 2**, pour extraire et conserver chaque run sur les points sélectionnés. Sans lui, la calibration ICON ne pourra reposer que sur Open-Meteo.

#### 4.3.5 Gouvernance des sources : licence d'usage et coût

Chaque adaptateur de source déclare dans le catalogue (`data_source`) deux attributs.

| Attribut | Valeurs | Effet |
|---|---|---|
| `usage_licence` | `open` (libre, y compris commercial : NOAA, DWD, ECMWF open data CC-BY 4.0, Copernicus) · `non_commercial` (Open-Meteo public) · `contract` (abonnement) | Un paramètre d'instance `DEPLOYMENT_USAGE=internal|commercial` est défini à `internal` aujourd'hui. En mode `commercial`, les sources `non_commercial` sont **bloquées** avec un message explicite, et l'administrateur doit basculer Open-Meteo en `api_key` ou `self_hosted`. **Le passage au commercial ne change aucun code**, seulement la configuration. |
| `cost` | `free` · `paid` | Les sources `paid` sont **désactivées par défaut**. Un administrateur les active en saisissant ses identifiants. L'utilisateur doit ensuite les **choisir explicitement** pour chaque téléchargement ; un badge « payant » et une confirmation s'affichent, et le choix est tracé dans le run. |

**Sources payantes envisagées** (points d'extension, non implémentés tant que vous ne les commandez pas) :
- ECMWF IFS HRES à 0,1° ou ENS à résolution native, via le catalogue temps réel ECMWF (frais de service éventuels) ;
- Open-Meteo commercial (`api_key`).

Le jalon 2 livre la **mécanique d'activation** et un rapport sur l'offre ECMWF 0,1° (gratuit ou non, modalités), sans souscription.

#### 4.3.6 Exports et visualisation

- **Exports CSV, XLSX, NetCDF**, avec les métadonnées suivantes :
  - modèle, run (init UTC), membre ;
  - point : id natif, lat/lon, altitude du modèle, distance au site ;
  - hauteur, unités (CF), fuseau `UTC` ;
  - drapeau natif/interpolé, méthode d'interpolation ;
  - source, date de récupération, version de l'application.
- **Visualisations** :
  - séries temporelles multi-modèles superposées, avec bande min–max ou P10–P90 pour les ensembles ;
  - rose des vents (secteurs de 30° ou 15°, classes de vitesse) ;
  - comparaison inter-modèles : diagramme de dispersion, écart au multi-modèle moyen, tableau des statistiques.

---

## 5. Module B — Énergie

### 5.1 B1 — Imports utilisateur

Chaque import passe par un **validateur** qui renvoie une liste structurée `{ligne, champ, code, message_fr, message_en, gravité}`. Rien n'est enregistré tant qu'il reste une erreur bloquante.

| Import | Formats | Contrôles |
|---|---|---|
| Layout | CSV, XLSX, KML, Shapefile (zip) | Colonnes obligatoires (ID, X, Y, CRS, type, hauteur de moyeu) ; CRS reconnu ; ID uniques ; distance inter-éoliennes < 2 D → avertissement ; éolienne hors de l'emprise du MNT → erreur. |
| Mât — métadonnées | Formulaire ou CSV | Coordonnées, altitude, et pour chaque capteur : hauteur, type (anémomètre, girouette, thermomètre, baromètre), orientation du bras. |
| Mât — séries 10 min | CSV générique (mapping de colonnes assisté) ; **NRG** : exports texte SymphoniePRO/Symphonie ; **Campbell TOA5** (`.dat`) ; **Windographer** (export texte) | Voir contrôle qualité ci-dessous. |
| Éoliennes | `.wtg` WAsP (via WindKit), CSV | D rotor, hauteur de moyeu, P(V) et Ct(V) monotones et cohérents (Ct ≤ ~1,1 ; P ≤ Pnom × 1,02), ρ_ref, démarrage/arrêt, hystérésis haut vent (arrêt à V_out, redémarrage à V_restart), table de déclassement en température (option). |
| Topographie | GeoTIFF, ou téléchargement automatique | **Copernicus DEM GLO-30** (AWS, sans compte) par défaut. SRTM : exige un compte NASA Earthdata, proposé seulement en option. |
| Rugosité | `.map` WAsP (WindKit) ; raster d'occupation du sol **ESA WorldCover** (10 m, AWS) ou CORINE (hors Maroc) | Table classe → z0 éditable (voir ci-dessous). |

> ⚠️ **NRG et Windographer** : les formats binaires natifs (`.rld`, `.rwd`, `.windographer`) sont propriétaires et non documentés. **Seuls leurs exports texte sont pris en charge.** Décision Q3 : l'historique est fourni en CSV, cette limite ne bloque donc pas.

**Contrôle qualité du mât** (drapeaux par valeur, jamais de suppression silencieuse) :

- plages physiques et valeurs figées (variance nulle sur N pas) ;
- **gel** : T < +2 °C et HR > 90 %, écart-type de vitesse nul ou girouette bloquée ;
- **ombrage du mât** : secteurs exclus automatiquement selon l'orientation des bras (±30° autour de l'axe du mât opposé), avec choix du capteur non ombré en cas de paire redondante ;
- pics isolés, cohérence entre hauteurs (cisaillement aberrant) ;
- données manquantes : taux de complétude par mois et par capteur, sans comblement automatique (option MCP ultérieure).

**Table z0 par défaut pour ESA WorldCover** (éditable) :

| Classe | z0 (m) |
|---|---|
| Couvert arboré | 0,8 |
| Arbustes | 0,1 |
| Prairie | 0,03 |
| Cultures | 0,05 |
| Bâti | 1,0 |
| Sol nu / végétation rare | 0,005 |
| Neige / glace | 0,001 |
| Eau | 0,0002 |
| Zones humides herbacées | 0,03 |
| Mangroves | 0,5 |
| Mousses / lichens | 0,01 |

### 5.2 B2 — Chaîne de calcul

#### Étape 1 — Calibration / MOS au mât

Méthodes cumulables, avec comparaison en validation croisée :

1. **Biais par secteur × saison** : correction additive ou multiplicative sur ws, 12 secteurs × 4 saisons, avec retrait vers le biais global (shrinkage) quand un secteur a peu de données.
2. **Quantile mapping** empirique par secteur, avec extrapolation linéaire aux extrêmes.
3. **LightGBM en régression quantile**, un modèle par quantile (0,1 ; 0,25 ; 0,5 ; 0,75 ; 0,9), plus un ré-ordonnancement pour éviter les croisements de quantiles. Variables d'entrée :
   - ws/wd de chaque modèle et de chaque point sélectionné ;
   - échéance ;
   - heure UTC, jour de l'année (sin/cos) ;
   - gradient T2m − T850 (indicateur de stabilité) ;
   - pression, HR.
4. **Sélection / pondération des modèles et des points** : poids inversement proportionnels au RMSE glissant par échéance, ou stacking (régression ridge contrainte ≥ 0).

**Validation** : validation croisée **par blocs temporels** (mois entiers) pour éviter les fuites liées à l'autocorrélation. Un historique **d'au moins 12 mois** est recommandé pour couvrir le cycle saisonnier.

#### Étape 2 — Extrapolation verticale

- Priorité à l'**exposant de cisaillement mesuré** α(secteur, heure-de-jour, saison) entre les deux hauteurs les plus hautes du mât.
- À défaut, **loi logarithmique** avec z0 local, issu de la carte de rugosité et pondéré par secteur en amont.
- Extrapolation bornée : un avertissement est émis si la hauteur de moyeu dépasse de plus de 50 % la hauteur de mesure la plus haute.

#### Étape 3 — Transposition mât → éoliennes (relief et rugosité)

Voir §5.3 pour les sources de speed-ups et le format d'import.

- Pour chaque position p (éoliennes et mât) et chaque direction de référence d, on dispose de :
  - `S(p, d)` : speed-up absolu (rapport à l'écoulement de référence du modèle) ;
  - `T(p, d)` : déviation (turning, en degrés).
- **Normalisation au mât** : `Speedup(p, d) = S(p, h_moyeu, d) / S(mât, h, d)` et `Turning(p, d) = T(p, d) − T(mât, d)`. Si le fichier importé est déjà relatif au mât, la ligne du mât doit valoir 1,0 et 0° (contrôle).
- **Direction** : la direction prévue et calibrée est celle *au mât*. On la convertit en direction de référence par inversion de `d_mât = d + T(mât, d)` (itération de point fixe, 2 ou 3 itérations suffisent).
- Injection dans **`py_wake.site.XRSite`** : dataset avec les variables `Speedup(i, wd)`, `Turning(i, wd)` et `TI(i, wd)` si elle est fournie. L'interpolation entre secteurs est assurée par XRSite (linéaire en direction, avec continuité à 360°).
- Les tables sont versionnées : chaque `energy_run` référence le jeu de speed-ups utilisé (hash du fichier).

#### Étape 4 — Sillages (PyWake)

| Paramètre | Défaut | Options |
|---|---|---|
| Déficit | **Bastankhah-Gaussian** (`BastankhahGaussianDeficit`) | TurbOPark (`TurboGaussianDeficit` / `Nygaard_2022`), NOJ / Jensen, Niayifar (`NiayifarGaussianDeficit`), Zong (`ZongGaussianDeficit`) |
| Superposition | Linéaire (`LinearSum`) | Somme quadratique (`SquaredSum`) |
| Turbulence ajoutée | **Crespo-Hernández** (`CrespoHernandez`) | Aucune |
| Blocage | Désactivé | `SelfSimilarityDeficit2020` |
| TI ambiante | TI mesurée au mât, par secteur et par classe de vitesse (écart-type/moyenne à 10 min, 90e percentile non utilisé en production) | Valeur fixe |
| Parcs voisins | Désactivé | Éoliennes voisines ajoutées comme type distinct, puissance exclue du total |

- **Performance** : une prévision de 14 jours à 10 min compte 2 016 pas ; pour 51 membres, cela fait environ 100 000 cas. Pour 400 MW, soit **~60 à 130 éoliennes** selon la machine (3 à 7 MW), le coût du calcul de sillage croît en n², et le mode direct devient lourd sur un serveur local. Deux modes sont prévus :
  - **direct** : `wfm(x, y, wd=…, ws=…, TI=…, time=True)`, exact, réservé à la validation et aux petits parcs ;
  - **table précalculée (LUT)**, par défaut : P(éolienne \| wd au pas de 1°, ws_ref au pas de 0,25 m/s, classe de TI), calculée une fois par configuration, puis interpolation. L'erreur d'interpolation LUT vs direct est mesurée et publiée dans le run (critère < 0,5 % sur l'énergie).
- **Carte de champ de sillage** : `flow_map` PyWake pour une direction et une vitesse choisies, rendue en image géoréférencée superposée à MapLibre.

#### Étape 5 — Correction de densité (IEC 61400-12-1)

- `ρ = p / (R_m · T)`, avec R_m corrigé de l'humidité (formule IEC, pression de vapeur saturante). T et p sont **ramenés à la hauteur de moyeu** : gradient standard de −6,5 K/km pour T, loi hydrostatique pour p.
- **Éoliennes à régulation pitch** : vitesse normalisée `V_n = V · (ρ / ρ_ref)^(1/3)`, puis lecture de P(V_n). Ct est traité de la même manière dans PyWake (via une entrée `Air_density` de `PowerCtFunctionList`, ou vitesse normalisée).
- Si le `.wtg` contient **plusieurs courbes par densité**, on interpole entre les courbes plutôt que d'appliquer la formule.

#### Étape 6 — Production brute et nette

Pour chaque pas et chaque éolienne, on calcule :

- `P_libre` (sans sillage) ;
- `P_brute` (avec sillage) ;
- `P_nette = P_brute × Π(1 − pertes)`, ou soustraction dans le cas des pertes dynamiques.

On obtient ensuite l'agrégation parc et l'énergie par intervalle.

### 5.3 Transposition mât → éoliennes : sources de speed-ups et format d'import

#### 5.3.1 Sources, par ordre de priorité

| Priorité | Source | Usage | Limite |
|---|---|---|---|
| 1 | **Speed-ups WAsP Engineering** calculés par l'équipe (licence disponible) | Voie par défaut dès qu'un projet a un modèle WEng | Modèle linéarisé : décollement non représenté sur pentes raides (RIX > 5 %). Le RIX est recalculé par l'application sur le MNT, et une alerte est émise par éolienne. |
| 2 | Autres logiciels (WindSim, Meteodyn, WAsP CFD) | Même format pivot | — |
| 3 | Grille de ressource WAsP (`.rsf` / `.wrg`) | Repli si seuls ces fichiers existent | Speed-up **approximé** par le rapport des vitesses moyennes par secteur (Weibull A, k), **sans turning**. Marqué « approximatif » dans le run et le rapport. |
| 4 | **WindNinja** (conservation de masse ; momentum en option) | Projets sans étude de ressource | Moins validé en évaluation énergétique ; pas de décollement (solveur de masse) ; solveur momentum lent. |

#### 5.3.2 Hauteurs : éviter le double comptage du cisaillement

Le fichier déclare la hauteur de chaque ligne. Deux configurations sont acceptées :

- **(A) — recommandée** : mât **et** éoliennes à la **hauteur de moyeu**. Le cisaillement mesuré au mât (étape 2) porte la vitesse du mât à la hauteur de moyeu ; WEng ne fait que la transposition horizontale. Cette configuration exploite au mieux la mesure.
- **(B)** : mât à la **hauteur de mesure**, éoliennes à la hauteur de moyeu. WEng fait à la fois l'extrapolation verticale et la transposition. **L'étape 2 est alors désactivée automatiquement** pour éviter de compter deux fois le cisaillement.

L'application détecte la configuration d'après les hauteurs et l'affiche avant validation.

#### 5.3.3 Format pivot Greenard (CSV ou XLSX)

Un gabarit est fourni : [`docs/templates/speedups_template.csv`](templates/speedups_template.csv).

**En-tête de métadonnées** : lignes `# clé: valeur` en CSV, ou onglet `metadata` en XLSX.

| Clé | Obligatoire | Exemple / valeurs |
|---|---|---|
| `format_version` | oui | `1` |
| `source_software` | oui | `WAsP Engineering 4.x` |
| `crs` | oui | `EPSG:32629` (coordonnées des lignes) |
| `speedup_reference` | oui | `model_reference` (rapport à l'écoulement de référence du modèle) ou `mast` (déjà normalisé au mât) |
| `mast_id` | oui | identifiant du mât présent dans les lignes |
| `direction_convention` | oui | `from_north_clockwise` (direction météo « d'où vient le vent », référence de l'écoulement amont) |
| `stability` | recommandé | `neutral` |
| `terrain_description` | recommandé | MNT, carte de rugosité, domaine et résolution utilisés |
| `author`, `date` | recommandé | traçabilité |

**Colonnes** (format long : une ligne par point × hauteur × secteur) :

| Colonne | Obligatoire | Unité | Description |
|---|---|---|---|
| `point_id` | oui | — | ID de l'éolienne (identique au layout) ou du mât |
| `point_type` | oui | — | `turbine` \| `mast` |
| `x`, `y` | oui | m (CRS déclaré) | Position utilisée dans WEng. Contrôle de cohérence avec le layout (tolérance 5 m). |
| `height_m` | oui | m a.g.l. | Hauteur du calcul |
| `sector_deg` | oui | ° | Direction centrale du secteur (0 = Nord) |
| `speedup` | oui | — | Rapport de vitesse (> 0) |
| `turning_deg` | oui | ° | Déviation, positive dans le sens horaire |
| `ti` | non | — | Intensité de turbulence modélisée (utilisée si la TI du mât n'est pas transposée) |
| `inflow_deg` | non | ° | Angle d'inclinaison de l'écoulement (utile pour les pertes de performance) |
| `z0_upstream_m` | non | m | Rugosité amont équivalente (diagnostic) |

**Secteurs** : ils doivent être régulièrement espacés et couvrir 360°. Le minimum est 12 secteurs ; **36 secteurs (10°) sont recommandés** en relief marqué, car le turning varie vite d'un secteur à l'autre. WEng calcule n'importe quelle liste de directions, donc le surcoût reste faible.

**Validation à l'import** (erreurs bloquantes en **gras**) :
- **toutes les éoliennes du layout et le mât sont présents** ;
- **grille secteurs × points complète** ;
- **hauteur de moyeu cohérente** (±1 m) ;
- speed-up entre 0,3 et 2,5 et turning entre −45° et +45° (avertissement au-delà) ;
- en mode `mast`, la ligne du mât vaut 1,0 et 0° ;
- écart de position avec le layout inférieur à 5 m.

#### 5.3.4 Exports natifs WAsP Engineering

Pour éviter une conversion manuelle, un **parseur de l'export natif WEng** (tableau de résultats par point et par direction) sera écrit au jalon 4, **à partir d'un exemple de fichier réel que vous me fournirez**. Je ne veux pas deviner les intitulés de colonnes d'une version de WEng que je ne peux pas vérifier. En attendant, le format pivot ci-dessus est la référence.

### 5.4 B3 — Pertes (taxonomie IEC 61400-15-2)

> **Nuance méthodologique** : IEC 61400-15-2 décrit les pertes **long terme** d'une évaluation énergétique (EYA). En prévision court terme, une perte forfaitaire long terme donne une *espérance*, pas la réalité du jour. Les pertes connues à l'avance (maintenance planifiée, bridage ONEE notifié, éolienne à l'arrêt) doivent donc pouvoir être saisies comme **calendrier d'indisponibilité**. Le moteur le prévoit.

Chaque perte a :

- un état `actif` ;
- un `mode` : `forfaitaire` (%) ou `dynamique` (fonction des variables prévues et du temps) ;
- une `portée` : éolienne ou parc ;
- une source bibliographique ou justification.

Les pertes sont appliquées **dans l'ordre de la cascade** (produit des facteurs), et la contribution de chacune est rapportée dans le waterfall.

| Catégorie IEC 61400-15-2 | Sous-catégorie | Mode par défaut | Valeur par défaut (indicative) |
|---|---|---|---|
| 1. Disponibilité | Éoliennes | Forfaitaire + calendrier | 97,0 % |
| | BOP (poste, câbles) | Forfaitaire | 99,5 % |
| | Réseau | Forfaitaire | 99,5 % |
| 2. Sillage | Parc interne | Dynamique (PyWake) | — |
| | Parcs voisins | Dynamique (PyWake), désactivé | — |
| | Futurs parcs | Inactif | — |
| 3. Performance des éoliennes | Écart à la courbe de puissance | Forfaitaire | 1,0 % |
| | Hystérésis haut vent | **Dynamique** : machine à états arrêt ≥ V_out, redémarrage < V_restart, appliquée sur la série et sur chaque membre | — |
| | Dérive / dégradation | Forfaitaire (fonction de l'âge) | 0,1 %/an |
| 4. Électriques | Réseau interne + transformateurs jusqu'au point de livraison | Forfaitaire, ou dynamique quadratique (pertes Joule ∝ P²) | 2,0 % |
| 5. Environnementales | Déclassement haute température | **Dynamique** : table constructeur P_max(T), T prévue au moyeu | Seuil générique 35 °C si aucune table n'est fournie |
| | Encrassement / érosion des pales (poussière, sable) | Forfaitaire | 1,0 % (0,5–2 % selon l'exposition ; les sites sahariens sont plutôt en haut de fourchette) |
| | Givrage | Dynamique (T < 0 °C, HR > 95 %), désactivé par défaut | Pertinent seulement en altitude (Atlas) |
| 6. Bridages | Réseau / curtailment ONEE | Calendrier ou plafond de puissance au point de livraison | 0 % (données fournies par l'exploitant) |
| | Gestion sectorielle | Dynamique (secteurs de direction × vitesse × éoliennes) | — |
| | Bruit | Dynamique (plage horaire locale × vitesse × mode de bridage) | — |
| | Faune (chiroptères, avifaune) | Dynamique : nuit, ws < seuil, T > seuil, mois actifs (ex. démarrage relevé à 5–6 m/s d'avril à octobre, de 30 min avant le coucher à l'aube) | Inactif |

**Limite liée au pas interpolé** : les pertes non linéaires (hystérésis, bridage sur seuil) calculées sur une série lissée à 10 min sont **sous-estimées**, puisque les dépassements de seuil courts disparaissent. Deux correctifs sont proposés :

- (a) application sur les membres d'ensemble, ce qui capte une partie de la variabilité ;
- (b) option de **perturbation stochastique** calibrée sur la variance 10 min du mât (désactivée par défaut, signalée dans le rapport).

### 5.5 B4 — Sorties

**Probabiliste** — deux voies combinables :

1. **Ensembles NWP** : la chaîne complète (MOS → PyWake → pertes) est appliquée **membre par membre**, puis les quantiles sont calculés sur la **puissance**. Calculer des quantiles de vent puis les convertir en puissance serait faux : la chaîne est non linéaire et dépend de la direction.
2. **Régression quantile** (LightGBM) directement sur la puissance du parc, avec en entrée la P50 déterministe et les statistiques d'ensemble (moyenne, écart-type).

La calibration des intervalles est **vérifiée** (histogramme PIT, diagramme de fiabilité) et **corrigée** si nécessaire, par recalibration quantile ou prédiction conforme (*conformal prediction*) sur une fenêtre glissante.

**Résultats** :

- par éolienne et pour le parc : kW, MWh, facteur de charge, P10/P25/P50/P75/P90 ;
- cascade des pertes ;
- carte heatmap de la production par éolienne ;
- champ de sillage.

**Évaluation a posteriori** (si données SCADA ou mât) :

- MAE, RMSE, biais (en absolu et en % de la puissance nominale) ;
- CRPS, pinball loss par quantile ;
- **skill score** par rapport à la persistance, et **par rapport à la climatologie** : au-delà de 6–12 h, la persistance devient une référence trop facile à battre ;
- toutes ces métriques sont ventilées par échéance.

**Exports** : CSV, XLSX et NetCDF, plus un **rapport PDF** qui contient :

- les hypothèses ;
- les versions ;
- la cascade des pertes ;
- les courbes P10–P90 ;
- la carte ;
- les scores (si disponibles) ;
- les limites.

---

## 6. Schéma de base de données

Les géométries sont en `geometry(…, 4326)`, avec le CRS de saisie d'origine conservé. Les séries volumineuses sont stockées dans des fichiers (`*_uri`).

```mermaid
erDiagram
  app_user ||--o{ project_member : ""
  project ||--o{ project_member : ""
  project ||--o{ site : contient
  data_source ||--o{ nwp_run : fournit
  project ||--o{ wind_farm : contient
  project ||--o{ met_mast : contient
  project ||--o{ terrain_layer : contient
  project ||--o{ loss_config : contient
  nwp_model ||--o{ grid_point : définit
  nwp_model ||--o{ nwp_run : produit
  site ||--o{ site_grid_point : "voisins"
  grid_point ||--o{ site_grid_point : ""
  nwp_run ||--o{ forecast_extract : ""
  site ||--o{ forecast_extract : ""
  wind_farm ||--o{ turbine : ""
  turbine_type ||--o{ turbine : ""
  met_mast ||--o{ mast_sensor : ""
  met_mast ||--o{ mast_dataset : ""
  wind_farm ||--o{ flow_result : ""
  project ||--o{ calibration_model : ""
  wind_farm ||--o{ energy_run : ""
  energy_run ||--o{ energy_run_input : "runs NWP utilisés"
  energy_run ||--o{ turbine_result : ""
  energy_run ||--o{ evaluation : ""
  task_log }o--|| energy_run : "optionnel"
```

| Table | Colonnes principales |
|---|---|
| `app_user` | id, email (unique), full_name, password_hash (argon2id), is_admin, is_active, locale (`fr` \| `en`), created_at, last_login_at |
| `project_member` | project_id, user_id, role (`owner` \| `engineer` \| `viewer`) |
| `auth_session` | id, user_id, refresh_token_hash, expires_at, revoked_at, user_agent, ip |
| `audit_log` | id, user_id, action, object_type, object_id, payload JSONB, at |
| `data_source` | code (`open_meteo`, `nomads`, `aws_gfs`, `ecmwf_opendata`, `dwd_opendata`, …), usage_licence (`open` \| `non_commercial` \| `contract`), cost (`free` \| `paid`), enabled, mode (`public` \| `api_key` \| `self_hosted`), credentials_ref (secret hors BDD), terms_url |
| `project` | id, created_by, name, description, display_tz (`UTC` \| `Africa/Casablanca`), default_crs, created_at |
| `site` | id, project_id, name, geom Point, input_crs, input_x, input_y, dem_elevation_m |
| `nwp_model` | code (`gfs`, `ifs`, `icon`, `icon_eu`, `gefs`, `ens`, `icon_eps`, `aifs`), grid_type (`regular` \| `icosahedral`), resolution, domain geom Polygon, native_steps JSONB (paliers d'échéances), max_lead_h, n_members, heights JSONB |
| `grid_point` | id, model_id, native_index (i,j ou cell_idx), geom Point, model_elevation_m, land_fraction ; contrainte UNIQUE(model_id, native_index) |
| `site_grid_point` | site_id, grid_point_id, rank, distance_m, azimuth_deg, dem_elevation_m, elevation_diff_m, contains_site bool, selected bool |
| `nwp_run` | id, model_id, init_time UTC, data_source_code, paid bool, retrieved_at, members, status, source_meta JSONB (URL, hash) ; UNIQUE(model_id, init_time, source) |
| `forecast_extract` | id, nwp_run_id, site_id, file_uri, variables JSONB, heights JSONB, t_start, t_end, target_step, interp_method, speed_method |
| `wind_farm` | id, project_id, name, boundary Polygon, is_neighbour bool |
| `turbine_type` | id, name, rotor_d_m, rated_kw, rho_ref, cut_in, cut_out, restart_ws, power_curve JSONB, ct_curve JSONB, density_curves JSONB, temp_derating JSONB, source_file_uri, file_hash |
| `turbine` | id, farm_id, label, geom Point, input_crs/x/y, hub_height_m, type_id, dem_elevation_m, rix_pct |
| `met_mast` | id, project_id, name, geom, elevation_m |
| `mast_sensor` | id, mast_id, kind, height_m, boom_dir_deg, unit, column_name |
| `mast_dataset` | id, mast_id, file_uri, format, t_start, t_end, qc_summary JSONB, file_hash |
| `terrain_layer` | id, project_id, kind (`dem` \| `roughness` \| `landcover`), source, file_uri, crs, resolution_m, z0_table JSONB |
| `flow_result` | id, farm_id, mast_id, model (`imported_weng` \| `imported_other` \| `wasp_rsf_approx` \| `windninja_mass` \| `windninja_momentum`), source_software, speedup_reference (`model_reference` \| `mast`), height_config (`A_hub` \| `B_measurement`), n_sectors, metadata JSONB, file_hash, validation_report JSONB, params JSONB, speedup_uri, dem_layer_id, rough_layer_id, cache_key |
| `calibration_model` | id, project_id, mast_id, method, training_start/end, features JSONB, cv_metrics JSONB, artifact_uri, lib_versions JSONB |
| `loss_config` | id, project_id, name, items JSONB (catégorie, sous-catégorie, actif, mode, valeur/paramètres, source), version |
| `energy_run` | id, created_by, farm_id, flow_result_id, calibration_model_id, loss_config_snapshot JSONB, wake_config JSONB, target_step, horizon_h, mode (`direct` \| `lut`), status, progress, software_versions JSONB, git_sha, result_uri, created_at |
| `energy_run_input` | energy_run_id, nwp_run_id, weight |
| `turbine_result` | energy_run_id, turbine_id, energy_free_mwh, energy_gross_mwh, energy_net_mwh, capacity_factor, losses JSONB, quantiles JSONB |
| `evaluation` | id, energy_run_id, obs_source, period, metrics JSONB (par échéance) |
| `task_log` | id, celery_id, kind, status, progress, message, started_at, finished_at |

**Traçabilité** : `energy_run` fige une **copie** (`snapshot`) de la configuration des pertes et du sillage, référence les runs NWP exacts par leur `init_time`, et enregistre `software_versions` (PyWake, WindKit, WindNinja, LightGBM, ecCodes, application).

---

## 7. API REST (esquisse)

```
POST /api/v1/geo/convert                      conversion multi-CRS
POST /api/v1/projects/{id}/sites              (+ /import CSV/KML)
GET  /api/v1/sites/{id}/grid-points?models=gfs,icon&n=4
PATCH /api/v1/sites/{id}/grid-points/{gp}     selected=true|false
POST /api/v1/sites/{id}/forecasts             → task_id (téléchargement)
GET  /api/v1/forecasts/{id}?step=10min&fmt=csv|xlsx|nc
POST /api/v1/farms/{id}/layout:import         validation + rapport
POST /api/v1/masts/{id}/data:import
POST /api/v1/turbine-types:import             .wtg / CSV
POST /api/v1/farms/{id}/flow                  → task_id (WindNinja)
POST /api/v1/farms/{id}/energy-runs           → task_id
GET  /api/v1/energy-runs/{id}                 résumé + waterfall
GET  /api/v1/energy-runs/{id}/export?fmt=csv|xlsx|nc|pdf
GET  /api/v1/tasks/{id}/events                SSE progression
```

---

## 8. Exigences transversales

- **Temps** : tout est stocké en UTC (`timestamptz`, xarray `datetime64[ns]` sans fuseau). L'affichage en `Africa/Casablanca` utilise **tzdata IANA** et il est **calculé côté serveur**, car la base de fuseaux d'un navigateur peut être ancienne. **Le Maroc est revenu à UTC+0 de façon permanente le 20/09/2026 à 02:00**. Avant cette date, il était en UTC+1, sauf pendant le Ramadan (UTC+0). Ce changement est intégré dans tzdata 2026d. L'image impose `tzdata>=2026d` et un test vérifie la conversion (jalon 2).
- **Traçabilité** : voir §6. Chaque export contient un bloc `provenance`.
- **i18n** : textes de l'interface, codes d'erreur et rapport PDF disponibles en FR et en EN.
- **Journalisation** : logs JSON structurés, avec id de tâche et de run.
- **Sécurité** : voir §8.1. Taille des fichiers importés limitée ; archives zip décompressées sans extraction de chemins arbitraires (protection contre le *zip slip*) ; parseurs XML (KML, `.wtg`) protégés contre les entités externes (`defusedxml`).

### 8.1 Authentification et autorisations (multi-utilisateurs dès le jalon 1)

- **Authentification intégrée**, sans service externe :
  - mots de passe hachés en **argon2id** ;
  - **jeton d'accès JWT** de courte durée (15 min) ;
  - **jeton de rafraîchissement** dans un cookie `HttpOnly; Secure; SameSite=Strict`, révocable et stocké haché en base (`auth_session`) ;
  - protection CSRF sur l'endpoint de rafraîchissement ;
  - limitation des tentatives de connexion.
- **Comptes** créés par un administrateur (pas d'inscription libre). Le premier administrateur est créé par une commande CLI à l'installation.
- **Rôles par projet** : `owner` (gère membres et configuration), `engineer` (imports, calculs, exports), `viewer` (lecture et exports). Les administrateurs d'instance gèrent en plus les utilisateurs et les sources de données (activation des sources payantes).
- Contrôle d'accès **appliqué côté API** (dépendance FastAPI sur chaque route), jamais seulement dans l'interface.
- **Journal d'audit** : connexions, imports, lancements de calcul, activation de sources payantes, exports.
- **Extension prévue** : connexion à l'annuaire de l'entreprise (LDAP / Active Directory, ou OIDC via Keycloak) sans refonte, car l'identité est isolée derrière une interface `AuthProvider`.
- **Documentation des hypothèses et limites** : un fichier `docs/LIMITS.md` est tenu à jour à chaque jalon (interpolation temporelle, représentativité d'une maille de 13 à 28 km en terrain complexe, domaine ICON-EU, qualité de WindNinja, etc.).

---

## 9. Stratégie de tests

`pytest` côté backend, `vitest` côté frontend, et CI GitHub Actions (lint `ruff`, `mypy`, tests).

| Domaine | Tests |
|---|---|
| Conversions | WGS84 ↔ UTM 28N/29N/30N (points de contrôle de référence), détection de zone aux frontières −12°/−6°, Lambert Merchich (valeurs de référence EPSG/IGN), parser DMS (formats variés, erreurs). |
| Points de grille réguliers | Sites synthétiques : sur un nœud, au centre d'une maille, au méridien 0 et à lon < 0 avec une grille 0–360 ; vérification que les 4 nœuds encadrent le site ; N = 9 et 16. |
| ICON icosaédrique | Mini-grille icosaédrique synthétique, **plus** un test sur le vrai fichier R3B07 (marqué `slow`, téléchargé en CI avec cache) : le plus proche centre KD-tree doit égaler la recherche en force brute sur 1 000 sites aléatoires ; la cellule contenant le site est vérifiée par test point-dans-triangle sphérique. |
| ICON-EU | Sites dans le domaine, dans la marge et hors domaine (ex. Dakhla, Guerguerat). |
| U/V | Cardinaux, aller-retour, ws = 0, interpolation à travers 360°/0° (le résultat ne doit pas passer par 180°), PCHIP sans valeurs négatives. |
| Temporel | Marquage natif/interpolé autour de la transition GFS 120 h ; rafales en escalier. |
| PyWake | **Cas de référence publié IEA Task 37 (case study 1, 16 éoliennes, Bastankhah simplifié)** : AEP comparée à la valeur de référence publiée ; **Horns Rev 1** : ratio de puissance le long d'une rangée à 270° comparé aux données publiées, avec la tolérance documentée. |
| Densité | Cas numériques IEC 61400-12-1. |
| Pertes | Hystérésis sur séries synthétiques ; produit des pertes = cascade. |
| Scores | CRPS d'ensemble comparé à une formule analytique (loi normale) ; pinball loss ; skill score. |

---

## 10. Plan de jalons

Chaque jalon se termine par une **démonstration** (scénario reproductible dans le README, avec captures d'écran) et une section « limites rencontrées / alternatives ».

| # | Jalon | Contenu | Démonstration |
|---|---|---|---|
| 1 | **Carte et points de grille** (livré) | Squelette Docker Compose, BDD et migrations, **authentification multi-utilisateurs et rôles par projet**, conversions CRS, saisie (formulaire, clic, CSV/KML), catalogue des modèles, recherche des points (régulière et ICON), altitude modèle vs DEM, masque terre/mer, carte MapLibre avec couches par modèle, i18n FR/EN. | Site près d'Essaouira (terrain côtier) et site près de Dakhla (ICON-EU désactivé). |
| 2 | **Téléchargement des prévisions** (livré) | (2a) Open-Meteo pour le prototypage ; (2b) GRIB natifs GFS / IFS / ICON / ICON-EU ; ensembles ; harmonisation temporelle ; exports ; séries temporelles, rose des vents, comparaison ; **archiveur Celery beat**. | Téléchargement multi-modèles sur 2 points, export NetCDF, pas de 10 min marqué « interpolé ». |
| 3 | **Import du parc et du mât** | Layout, `.wtg`/CSV, mât (CSV, TOA5, exports NRG/Windographer), contrôle qualité avec drapeaux, cisaillement et TI par secteur, MNT GLO-30, WorldCover et z0. | Parc de démonstration + mât synthétique avec gel et ombrage détectés. |
| 4 | **PyWake et terrain** | **Import des speed-ups (format pivot + parseur natif WEng d'après votre exemple)**, repli `.rsf`/`.wrg` et WindNinja, RIX, XRSite, modèles de sillage, LUT, densité, champ de sillage sur la carte, tests IEA37 et Horns Rev. | Production brute par éolienne sur une prévision réelle, carte de sillage à 270° / 8 m/s. |
| 5 | **Pertes et probabiliste** | Moteur de pertes, cascade, calendriers, chaîne par membre d'ensemble, quantiles, rapport PDF. | P10–P90 sur 14 jours, waterfall, PDF. |
| 6 | **Calibration ML et évaluation** | Biais secteur × saison, quantile mapping, LightGBM quantile, pondération des modèles, métriques et skill scores, recalibration des intervalles. | Calibration sur l'historique fourni, rapport de scores par échéance. |

---

## 11. Limites identifiées et journal des décisions

### Limites à connaître dès maintenant

1. **Aucun modèle ouvert n'est sous-horaire sur le Maroc** : les pas de 10 et 15 min sont toujours interpolés et ne contiennent aucune variabilité physique supplémentaire.
2. **Résolution vs terrain** : des mailles de 13 à 28 km ne résolvent ni l'Atlas, ni le Rif, ni les brises côtières. La calibration MOS au mât est indispensable ; sans mât, la prévision n'est pas « bancable ».
3. **Transposition** : elle repose sur les speed-ups WAsP Engineering importés, donc sur les limites d'un modèle linéarisé en terrain complexe (RIX). WindNinja reste le repli pour les projets sans étude WEng.
4. **Pas d'archive ICON ouverte** : la calibration ICON dépend d'Open-Meteo ou de notre propre archiveur, qui ne capitalise qu'à partir de sa mise en service.
5. **Formats binaires NRG et Windographer** : non lisibles, seuls les exports texte sont pris en charge.
6. **Merchich → WGS84** : précision métrique à décamétrique selon la transformation PROJ disponible.
7. **Pertes IEC 61400-15-2** : conçues pour du long terme, elles sont adaptées ici au court terme (calendriers, pertes dynamiques). C'est une adaptation méthodologique, pas une application normative stricte.

### Journal des décisions (réponses du 2026-09-22)

| # | Question | Décision | Impact sur l'architecture |
|---|---|---|---|
| Q1 | Usage commercial ? | **Usage interne non commercial** au départ ; possibilité de passer au commercial | `DEPLOYMENT_USAGE=internal`. Open-Meteo public est autorisé. Le passage au commercial se fait par configuration (Open-Meteo `api_key` ou `self_hosted`) (§4.3.5). |
| Q2 | Licence WAsP / speed-ups ? | **Licence WAsP disponible, speed-ups produits avec WAsP Engineering** | Import des speed-ups WEng comme voie principale ; WindNinja en repli (§5.3). **Format d'import spécifié en §5.3.3.** |
| Q3 | Historique de calibration | **≥ 12 mois, en CSV** | Priorité à l'import CSV générique avec correspondance de colonnes ; les parseurs NRG/Campbell passent au second plan. |
| Q4 | Taille des parcs | **59 à 400 MW** selon le projet | ~15 à 130 éoliennes. Mode LUT par défaut ; mode direct découpé en blocs de temps ; objectif mesuré au jalon 4 : une prévision de 14 j × 51 membres pour 400 MW en moins de 15 min sur le serveur recommandé. |
| Q5 | Authentification | **Multi-utilisateurs dès le départ** | Intégrée au jalon 1, rôles par projet, audit (§8.1). |
| Q6 | IFS 0,1° / services payants | **Gratuit au départ** ; l'utilisateur décide de commander ou non un service payant | Sources `paid` désactivées par défaut, activation par un administrateur puis choix explicite de l'utilisateur, tracés dans le run (§4.3.5). |
| Q7 | Déploiement | **Serveur local** | Docker Compose, HTTPS interne, proxy sortant, sauvegardes, 16 vCPU / 64 Go recommandés (§2.5). |

### Écarts constatés au jalon 1

| Sujet | Constat | Décision |
|---|---|---|
| Domaine ICON-EU | Limite sud à **29,5°N** (et non 23,5°N) : tout le sud à partir de Guelmim est hors domaine. | Catalogue corrigé ; géométrie relue dans le GRIB. |
| Catalogue des modèles | Défini dans le code (`app/nwp/catalog.py`), versionné avec lui, plutôt qu'en table `nwp_model`. | Les tables référencent le modèle par son code. |
| Merchich Sahara | PROJ ne dispose que d'une transformation « ballpark » (sans changement de datum) pour les zones Sahara Sud (26195) et une partie de Sahara Nord. | Avertissement `MERCHICH_BALLPARK` explicite, jamais de choix silencieux. |
| Wheels pip | ecCodes, rasterio et netCDF4 installés par pip embarquent des bibliothèques natives incompatibles entre elles (plantages). | Environnement conda-forge obligatoire (`backend/environment.yml`), en dev comme en image Docker. |
| Progression des tâches | Suivi par interrogation périodique (1 s) de `task_log`. | Le flux SSE est reporté au jalon 2 (téléchargements longs). |
| E-mails | Les validateurs stricts refusent les domaines internes (`.local`). | Validation souple. |

### Écarts constatés au jalon 2

| Sujet | Constat | Décision |
|---|---|---|
| Heure légale du Maroc | Retour permanent à **UTC+0 le 20/09/2026** (tzdata 2026d). La tzdata 2025b du système de base l'ignore encore. | Conversion côté serveur, `tzdata>=2026d` imposé. Les exports portent `time_utc` et, en option, `time_local` avec son décalage explicite. |
| Volumes AWS / ECMWF | Aucun découpage côté serveur : chaque champ est global. Volumes mesurés : GFS 240 h ≈ 1 Go, IFS 240 h ≈ 0,5 Go, ENS 50 membres 48 h (100 m seulement) ≈ 2,2 Go, ENS 360 h ≈ 11 Go, GEFS 384 h ≈ 8,5 Go. | Estimation préalable affichée et plafond par extrait (`GREENARD_MAX_DOWNLOAD_MB`, 3 Go par défaut). En mode automatique, les sources qui extraient au point passent d'abord : NOMADS (GFS, GEFS) et Open-Meteo (ENS). |
| ENS open data | Les fichiers « ef » ne contiennent que les **50 membres perturbés** : le membre de contrôle est absent. | Quantiles calculés sur 50 membres. |
| ICON-EPS | Nécessite la grille R3B06 et ses invariants, et ne peut pas être testé ici (DWD bloqué). | Reporté ; à ajouter dès que le DWD est joignable depuis le serveur. |
| Open-Meteo | Donne un pas horaire même pour les modèles 3-/6-horaires, et le run n'est pas toujours exposé. | Seules les échéances natives sont conservées. Le run est lu dans les métadonnées Open-Meteo, sinon déduit de la latence typique (information inscrite dans l'extrait). |
| Archives historiques | Open-Meteo « Historical Forecast » assemble plusieurs runs successifs : ce n'est pas la prévision d'un run unique. | Archives natives via AWS (GFS depuis 2021, IFS depuis 2023) et archivage automatique. Les API « Previous Runs » d'Open-Meteo seront évaluées au jalon 6. |
| ECMWF 0,1° | Catalogue temps réel ouvert (CC-BY 4.0) depuis le 01/10/2025. La résolution 9 km doit rejoindre le sous-ensemble gratuit « plus tard en 2026 », avec 2 h de latence. Les gros volumes peuvent entraîner des frais de service. | Source `ecmwf_hres_01` réservée, désactivée. Voir `docs/ECMWF_0p1.md`. |

### Ce dont j'aurai besoin plus tard

- **Jalon 3** : un extrait de CSV mât (quelques jours, anonymisé si besoin) et un `.wtg` représentatif.
- **Jalon 4** : un **export natif WAsP Engineering** (tableau de résultats par point et par direction), pour écrire son parseur.
- **Déploiement** : le nom d'hôte interne et le certificat, ou l'accord pour une CA interne générée par Caddy.

*Dès votre validation de cette v0.2, je démarre le jalon 1.*
