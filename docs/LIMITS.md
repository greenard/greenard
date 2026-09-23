# Hypothèses et limites

Document tenu à jour à chaque jalon (§8 de la note d'architecture).

## Jalon 1 — Carte et points de grille

### Coordonnées
- **Merchich → WGS84** : transformation PROJ « Merchich to WGS 84 (1) », 3 paramètres, précision
  nominale **7 m**, disponible sur le Nord et le Sud Maroc. Pour **Sahara Sud (EPSG:26195)** et une
  partie de **Sahara Nord (26194)**, PROJ ne propose qu'une transformation « ballpark » sans
  changement de datum : l'erreur peut atteindre **plusieurs centaines de mètres**. L'application
  affiche alors l'avertissement `MERCHICH_BALLPARK`. Pour un layout d'éoliennes dans ces zones,
  fournir des coordonnées en UTM/WGS84 ou des paramètres de transformation locaux.
- **EPSG:26193 (Merchich / Sahara)** est déprécié : accepté en saisie, jamais proposé en sortie.
- Zones UTM : le Maroc couvre **28N, 29N et 30N**. La zone est détectée automatiquement ; une
  saisie dans une zone voisine reste possible (avertissement si le point sort de la zone d'emploi).

### Points de grille
- **Grilles régulières** : la géométrie est relue dans les en-têtes GRIB (GFS 0–360°, IFS open
  data 180°→179,75°, ICON-EU −23,5°→62,5°). Les « 4 nœuds encadrants » forment la maille qui
  contient le site ; « N plus proches » (4, 9, 16) trie par distance géodésique (ellipsoïde WGS84).
- **ICON global** : recherche par KD-tree sur les centres de cellules (`CLAT`/`CLON`), sans hypothèse
  de régularité. La **cellule contenant le site** n'est déterminée que si le fichier de grille
  complet (`icon_grid_0026_R03B07_G.nc`, sommets des triangles) est fourni via
  `GREENARD_ICON_GRID_FILE` ; sinon seuls les plus proches centres sont listés.
- **ICON-EU** : domaine 29,5°N–70,5°N. Un site situé à moins de 5 mailles (~35 km) du bord est
  refusé (conditions aux limites). Tout le sud du Maroc à partir d'environ 29,8°N est hors domaine.
- **Ensembles** (GEFS, ENS) : la recherche de points utilise la grille du modèle déterministe
  correspondant ; téléchargement au jalon 2. ICON-EPS (grille R3B06) sera ajouté au jalon 2.
- GFS et IFS partagent les mêmes nœuds 0,25° : sur la carte, leurs points se superposent (utiliser
  les interrupteurs de couche).

### Altitudes et terre/mer
- **Altitude modèle** : GFS `HGT:surface`, IFS `z/g0`, ICON `HSURF`. L'IFS présente de légères
  valeurs négatives en mer (oscillations spectrales), sans conséquence.
- **Altitude réelle** : Copernicus DEM GLO-30 (valeur au nœud et moyenne sur la maille, lecture
  décimée ~120×120 px). Les tuiles absentes (mer) comptent pour 0 m.
- **Écart d'altitude** = altitude modèle − altitude MNT **du site** ; alerte au-delà de 100 m
  (`GREENARD_ELEVATION_ALERT_M`). C'est l'écart pertinent pour juger si le point représente le site ;
  l'altitude MNT du nœud et la moyenne de maille sont affichées à titre de diagnostic.
- **Terre/mer** : GFS `LAND` (0/1), IFS `lsm` et ICON `FR_LAND` (fractions). Un point est « mer » si
  la fraction de terre est < 0,5 ; alerte si le site est à terre (altitude MNT > 0) et le point en mer.

### Environnement de développement
- Le bac à sable de développement n'a pas accès à `opendata.dwd.de` (ni à Docker Hub, ni aux tuiles
  OpenStreetMap) : le code ICON / ICON-EU a été testé sur une grille icosaédrique synthétique et sur
  des GRIB générés localement, **pas sur les fichiers DWD réels**. À valider sur le serveur local
  (`pytest -m network`). Les images Docker n'ont pas pu être construites ici ; les mêmes étapes
  (environnement conda-forge, `pip install .`, `npm run build`) ont été exécutées hors conteneur.

## Jalon 2 — Téléchargement des prévisions

### Pas de temps et interpolation
- Aucun modèle ouvert n'est **sous-horaire** sur le Maroc. Les pas de 10 et 15 min sont toujours
  **interpolés** (drapeau `interpolated`), de même que le pas horaire au-delà de 120 h pour GFS, de 78 h
  pour ICON et, pour l'IFS, sur tout l'horizon (3 h puis 6 h).
- L'interpolation porte sur U/V (direction) et, par défaut, sur la vitesse **scalaire**. Elle ne crée
  **aucune variabilité physique** sous-horaire : une série à 10 min est lisse, sa variance est très
  inférieure à celle d'une mesure réelle. À prendre en compte pour les pertes non linéaires du jalon 5.
- Agrégation vers un pas plus long : période (t − Δ, t] (vitesse moyenne scalaire, direction de la
  moyenne vectorielle, rafale maximale). Le premier horodatage d'une série garde sa valeur instantanée.
- Rafales :
  - GFS `GUST` et GEFS : rafale **instantanée**, interpolée ;
  - IFS `10fg`, ICON `VMAX_10M` et Open-Meteo : **maximum sur la période précédente**, jamais interpolé
    (valeur en escalier).

### Hauteurs disponibles
| Modèle | Vent natif | Remarques |
|---|---|---|
| GFS | 10, 20, 30, 40, 50, 80, 100 m | 120, 150 et 180 m indisponibles : signalés dans `missing_variables`. |
| IFS / ENS open data | 10, 100 m | Pas de 200 m dans l'open data. |
| ICON / ICON-EU | 10 m | Hauteurs supérieures **dérivées** des niveaux modèle (interpolation en ln z à l'aide de HHL), marquées `derived_heights_m`. Nombre de niveaux ICON-EU (74) à confirmer sur les fichiers réels. |
| GEFS 0,25° | 10 m | Fichiers `pgrb2sp25`. |

### Volumes et sources
- AWS et ECMWF ne découpent pas les fichiers côté serveur : chaque champ téléchargé est **global**.
  Volumes mesurés le 22/09/2026 : GFS 240 h ≈ 1 Go ; IFS 240 h ≈ 0,5 Go ; ENS 48 h (100 m, 50 membres)
  ≈ 2,2 Go ; ENS 360 h ≈ 11 Go ; GEFS 384 h ≈ 8,5 Go. Au-delà du plafond (3 Go par défaut), la source
  est écartée (`DOWNLOAD_TOO_LARGE`) et la suivante est essayée.
- **NOMADS** (découpage serveur) est la source à privilégier pour GFS et GEFS, **Open-Meteo** pour
  les ensembles. Ni l'un ni l'autre n'était joignable depuis l'environnement de développement : leurs
  adaptateurs sont couverts par des tests hors ligne (réponses simulées) et restent **à valider sur le
  serveur** (`pytest -m network`). Même chose pour DWD (ICON, ICON-EU).
- ENS open data : 50 membres perturbés, **sans membre de contrôle**.
- Open-Meteo : seules les échéances natives sont conservées. Si les métadonnées du run sont
  inaccessibles, le run est **déduit** de la latence typique du modèle, ce qui peut décaler les
  échéances d'un cycle.
- **ICON-EPS** n'est pas encore disponible (grille R3B06 à intégrer).

### Archives
- GFS : AWS depuis 2021 ; IFS : miroir AWS depuis 2023 ; ICON : DWD ~24 h seulement. L'**archivage
  automatique** (horaire, par projet) constitue l'historique à partir de sa mise en service.

### Heure locale
- Le Maroc est revenu à **UTC+0** le 20/09/2026 (tzdata 2026d). L'heure locale est calculée côté
  serveur. Seules les dates de création affichées dans l'interface utilisent la base de fuseaux du
  navigateur.

### IFS 9 km (Open-Meteo)
- Modèle `ifs_9km` sur la grille native O1280, servi uniquement par Open-Meteo (API publique non
  commerciale en mode `public`). Altitude modèle et terre/mer non affichées pour cette grille.
- Correctif : le calendrier des échéances natives utilisé pour filtrer Open-Meteo sautait d'un pas
  aux transitions (ex. GFS 121 h au lieu de 123 h, IFS 147 h au lieu de 150 h) ; corrigé et testé.

## Jalon 3 — Parc, mât et terrain

### Types d'éoliennes et layout
- `.wtg` : WAsP (XML `WindTurbineGenerator`) avec plusieurs tables de densité ; puissances en W converties en kW.
  Les champs absents du fichier (vitesse de redémarrage, hauteurs de moyeu, parfois la densité de référence) sont
  à compléter dans le formulaire ; un essai à blanc affiche les contrôles avant l'enregistrement.
- Pas de fichier constructeur réel dans l'environnement de développement : les tests et la démonstration utilisent
  une courbe **générique synthétique** (4,2 MW, 136 m). Un `.wtg` réel reste nécessaire pour valider le parseur.
- Contrôles d'espacement sur la distance horizontale entre moyeux : erreur sous 1 D, avertissement sous 2 D. Pas
  encore de contrôle elliptique (ex. 3 D × 5 D selon la direction dominante) : prévu au jalon 4, quand la rose du
  site sera disponible dans le calcul de sillage.
- Shapefile : archive zip avec `.prj` obligatoire. Sans `.prj`, refus explicite (`LAYOUT_SHP_NO_PRJ`), jamais de
  SCR supposé.

### Mât de mesure
- Formats **texte** uniquement : Campbell TOA5, export texte NRG SymphoniePRO, export Windographer, CSV générique.
  Les fichiers binaires propriétaires (`.rld` NRG, `.dat` binaire Campbell TOB1…) sont refusés avec
  `MAST_BINARY_FORMAT` : les exporter en texte depuis le logiciel du fabricant. Alternative possible : intégrer
  `nrgpy` (conversion `.rld` via l'API NRG, nécessite un compte NRG) — non fait sans votre accord.
- Mappage des colonnes : proposé à partir des noms (conventions NRG et génériques), toujours modifiable. La hauteur
  d'un capteur qui n'est pas dans le nom (pression, température) doit être saisie.
- Fuseau : UTC, décalage fixe ou zone IANA. Avec une zone IANA, les instants ambigus du changement d'heure sont
  marqués manquants plutôt que devinés ; un logger reste presque toujours sur un décalage fixe.
- Contrôle qualité automatique, sans validation manuelle pour l'instant : manquant, hors plage, valeur bloquée,
  givrage (T < 2 °C, HR > 85 % si mesurée, et capteur figé ou écart-type nul), ombrage du mât (±30° derrière le
  bras), pics (écart à la médiane glissante > max(5 MAD, 3 m/s)), cisaillement incohérent. Les seuils sont dans
  `app/mast/qc.py`. **Alternative** : un écran d'invalidation manuelle par période (jalon 6, avec la calibration).
- Cisaillement α calculé sur les moyennes de groupe (vitesses des deux hauteurs valides en même temps), par secteur,
  heure et saison. TI représentative = moyenne + 1,28 σ par classe de vitesse (IEC 61400-1 éd. 4).
- **Données de démonstration synthétiques** (profil Weibull, α = 0,14, défauts injectés à des instants connus) :
  les chiffres affichés dans la démo ne décrivent aucun site réel.

### Terrain
- MNT Copernicus GLO-30 (AWS, licence Copernicus libre) et ESA WorldCover 2021 v200 (10 m, CC-BY 4.0), téléchargés
  sur l'emprise des éoliennes, mâts et sites élargie d'une marge (5 km par défaut). Emprise vide → refus
  `TERRAIN_NO_EXTENT`.
- La rugosité z0 vient d'une **table classe → z0 éditable** (valeurs par défaut prudentes, à ajuster par projet).
  WorldCover date de 2021 : les changements récents (défrichement, nouvelles constructions) n'y figurent pas.
- z0 amont par secteur : moyenne géométrique pondérée par 1/distance dans un rayon de 3 km. C'est une approche
  simple, à visée de contrôle : ce n'est pas le modèle de changement de rugosité de WAsP, qui reste la référence
  pour les speed-ups (jalon 4).
- Carte WAsP `.map` : lue et **affichée** (lignes de rugosité et courbes de niveau) ; elle n'est pas encore
  rasterisée ni utilisée dans un calcul. Le format ne contient pas de SCR : il est demandé à l'import.
- MNT importé (GeoTIFF) reprojeté en EPSG:4326 ; un GeoTIFF sans SCR est refusé (`TERRAIN_TIFF_NO_CRS`).

