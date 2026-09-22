# Démonstration — jalon 2 (téléchargement des prévisions)

Scénario joué dans un navigateur contre l'application réelle (API, PostgreSQL/PostGIS, Redis,
worker Celery), avec des données **réelles** du run 12Z du 22/09/2026 :

- **GFS** : AWS, 161 échéances jusqu'à 240 h, ~1 Go ;
- **ECMWF IFS HRES open data** : AWS, 65 échéances, ~0,5 Go ;
- **ECMWF ENS** : AWS, 50 membres, 48 h, vent à 100 m, ~2,2 Go.

Site : nœud terrestre 31,5°N / 9,75°W, près d'Essaouira. NOMADS, data.ecmwf.int, DWD et Open-Meteo
n'étaient pas joignables depuis l'environnement de démonstration : la bascule automatique vers la
source suivante est visible dans les écrans.

| # | Capture | Ce qui est montré |
|---|---|---|
| 1 | [01-estimation-sources](01-estimation-sources.png) | Estimation avant téléchargement, par modèle et par source candidate : run, échéances, volume, licence ; sources injoignables signalées |
| 2 | [02-progression-sse](02-progression-sse.png) | Progression en direct (flux SSE) |
| 3 | [03-resultats-1h](03-resultats-1h.png) | Résultats : source utilisée, tentative NOMADS en échec, séries multi-modèles au pas horaire |
| 4 | [04-10min-pchip-marqueurs-natifs](04-10min-pchip-marqueurs-natifs.png) | Pas de 10 min, spline PCHIP sur U/V : les marqueurs sont les échéances natives (GFS 3 h au-delà de 120 h, IFS 6 h au-delà de 144 h) ; le reste est interpolé |
| 5 | [05-direction-temperature](05-direction-temperature.png) | Direction (convention « vient de ») et température |
| 6 | [06-rose-comparaison](06-rose-comparaison.png) | Rose des vents (16 secteurs, 6 classes) et comparaison inter-modèles (biais, RMSD, corrélation par rapport à la moyenne multi-modèle) |
| 7 | [07-ensemble-ens-p10-p90](07-ensemble-ens-p10-p90.png) | ECMWF ENS, 50 membres : médiane, bande P10–P90, enveloppe min–max |
| 8 | [08-sources-de-donnees](08-sources-de-donnees.png) | Gouvernance des sources : licence, coût, activation par un administrateur |
| — | [exemple-export-gfs-ifs-10min.csv](exemple-export-gfs-ifs-10min.csv) | Export CSV réel : métadonnées en tête, colonne `time_flag`, unités |
