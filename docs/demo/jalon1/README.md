# Démonstration — jalon 1 (carte et points de grille)

Scénario joué de bout en bout dans un navigateur (Chromium, Playwright) contre l'API réelle,
PostgreSQL/PostGIS, Redis et un worker Celery. Données réelles : GFS (AWS, run du jour), ECMWF IFS
open data (miroir AWS), Copernicus DEM GLO-30. Les serveurs DWD (ICON, ICON-EU) et les tuiles
OpenStreetMap n'étaient pas joignables depuis l'environnement de démonstration : le fond de carte
« hors-ligne » (côtes Natural Earth) est utilisé et l'indisponibilité DWD est affichée telle quelle.

| # | Capture | Ce qui est montré |
|---|---|---|
| 1 | [01-login-erreur](01-login-erreur.png) | Authentification, message d'erreur traduit |
| 2 | [02-saisie-dms-conversions](02-saisie-dms-conversions.png) | Saisie DMS `31°30'45"N 9°46'12"W`, conversions permanentes WGS84 / DMS / UTM 29N (zone auto) / Lambert Nord Maroc (précision 7 m) |
| 3 | [03-essaouira-points-de-grille](03-essaouira-points-de-grille.png) | Site côtier d'Essaouira : 4 nœuds encadrants GFS, IFS, ICON-EU ; liens site → points ; avertissement DWD injoignable |
| 4 | [04-essaouira-tableau](04-essaouira-tableau.png) | Distance, azimut, altitude modèle vs MNT, écart au site, terre/mer avec alertes (3 des 4 nœuds GFS sont en mer), sélection des points |
| 5 | [05-saisie-utm28-lambert-ballpark](05-saisie-utm28-lambert-ballpark.png) | Saisie UTM 28N (Dakhla) ; Lambert Sahara Sud signalé comme transformation approximative |
| 6 | [06-dakhla-icon-eu-hors-domaine](06-dakhla-icon-eu-hors-domaine.png) | ICON-EU désactivé avec message explicite (23,7° < 29,5°N) ; points GFS/IFS calculés |
| 7 | [07-import-csv-erreurs](07-import-csv-erreurs.png) | Import CSV : vérification préalable, erreurs par ligne |
| 8 | [08-english-ui](08-english-ui.png) | Interface en anglais |

Pour rejouer : lancer l'API, le worker et le frontend (voir README racine), puis suivre les mêmes
étapes dans l'interface.
