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
