# Démonstration — jalon 3 (parc, mât de mesure, terrain)

Scénario joué dans un navigateur contre l'application réelle (API, PostgreSQL/PostGIS, Redis, worker
Celery). Tout passe par l'interface, sauf la création du projet, faite par l'API.

**Données du parc et du mât SYNTHÉTIQUES** : aucun fichier réel n'était disponible. Les chiffres ne
décrivent aucun site réel.

- **Type d'éolienne** : courbe générique 4,2 MW, rotor 136 m, moyeu 112 m (`tests/farmgen.py`). Fichier
  `.wtg` WAsP avec deux densités.
- **Layout** : 15 éoliennes (3 × 5, 5 D × 7 D, soit 63 MW), en UTM 29N à l'est d'Essaouira.
- **Mât** : 12 mois au pas de 10 min au format Campbell TOA5, avec des défauts **injectés à des instants
  connus** (`tests/mastgen.py`) :
  - anémomètres à 80 m (bras N et S), 60 m et 40 m ;
  - girouette à 78 m ; T, HR et P à 2 m ;
  - logger à UTC+1, horodatage en fin d'intervalle.

  | Défaut injecté | Vérité | Détecté |
  |---|---|---|
  | Givrage de l'anémomètre à 60 m | 72 enregistrements | 72 (givrage + bloqué) |
  | Girouette bloquée | 18 enregistrements | 18 |
  | Pics à 40 m | 2 | 2 |
  | Trou de 2 jours | 288 enregistrements | 288 manquants, sur tous les capteurs |
  | Ombrage du mât | α = 0,14, facteur 0,82 derrière le bras | secteurs ±30° signalés ; composite ws@80 hors ombrage |
  | Cisaillement | α = 0,140 | α = 0,140 (60–80 m) |

**Terrain RÉEL** :
- MNT Copernicus GLO-30 et ESA WorldCover 2021 v200, téléchargés sur l'emprise du parc et du mât élargie
  de 5 km ;
- rugosité issue de la table classe → z0 par défaut.

| # | Capture | Ce qui est montré |
|---|---|---|
| 1 | [01-type-eolienne-wtg](01-type-eolienne-wtg.png) | Import `.wtg` : essai à blanc, caractéristiques lues, courbes de puissance et de Ct, vitesse de redémarrage complétée à la main |
| 2 | [02-layout-controle](02-layout-controle.png) | Import du layout CSV en UTM 29N : contrôle avant remplacement |
| 3 | [03-parc-carte](03-parc-carte.png) | Parc sur la carte, table des éoliennes : coordonnées saisies, WGS84, altitude MNT ; espacement minimal en diamètres |
| 4 | [04-mat-assistant-mappage](04-mat-assistant-mappage.png) | Assistant d'import TOA5 : format détecté, aperçu, fuseau du logger, convention d'horodatage, mappage proposé (hauteurs et bras lus dans les noms de colonnes) |
| 5 | [05-mat-qc-disponibilite](05-mat-qc-disponibilite.png) | Contrôle qualité par capteur (drapeaux) et disponibilité mensuelle |
| 6 | [06-mat-rose-serie](06-mat-rose-serie.png) | Rose des vents à 80 m, séries horaires des composites ws@h |
| 7 | [07-mat-serie-drapeaux](07-mat-serie-drapeaux.png) | Zoom de 10 jours autour du premier défaut du capteur choisi (givrage à 60 m) : croix = valeurs signalées |
| 8 | [08-mat-cisaillement-turbulence](08-mat-cisaillement-turbulence.png) | Cisaillement par secteur, heure et saison ; TI moyenne et représentative (P90) par classe de vitesse ; densité de l'air |
| 9 | [09-terrain-mnt-ombre](09-terrain-mnt-ombre.png) | MNT GLO-30 réel en relief ombré sous le parc |
| 10 | [10-terrain-rugosite-z0](10-terrain-rugosite-z0.png) | Rugosité z0 dérivée de WorldCover |
| 11 | [11-terrain-table-z0](11-terrain-table-z0.png) | Table classe → z0 modifiable, avec la part de chaque classe sur l'emprise |
| 12 | [12-mat-z0-secteurs](12-mat-z0-secteurs.png) | z0 amont par secteur au mât, une fois l'occupation du sol disponible |

## Rejouer

```bash
cd backend
python - <<'EOF'
import sys; sys.path.insert(0, "tests")
import farmgen, mastgen
open("GEN-4.2-136.wtg", "wb").write(farmgen.wtg_xml())
open("layout.csv", "wb").write(farmgen.layout_csv(n_rows=3, n_cols=5, x0=428500))
df, truth = mastgen.generate(days=365)
open("mast_toa5.dat", "w").write(mastgen.to_toa5(df))
EOF
```

Puis, dans un projet :
1. Onglet **Parc** : importer le `.wtg`, créer un parc, importer `layout.csv`.
2. Onglet **Mât** : créer le mât à 31,4840 / −9,7386, importer `mast_toa5.dat` avec le fuseau UTC+01:00 et la
   hauteur du capteur de pression (2 m).
3. Onglet **Terrain** : télécharger le MNT puis l'occupation du sol.
