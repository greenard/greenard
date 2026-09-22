# Accès ECMWF IFS à 0,1° (9 km) — état au 22/09/2026

Rapport demandé à la décision Q6 : rester gratuit et laisser l'utilisateur décider s'il commande un
service payant.

## Situation

- **Depuis le 1er octobre 2025**, l'ECMWF a ouvert son catalogue temps réel complet sous licence
  **CC-BY 4.0**, sans coût de données. La **livraison** de gros volumes peut toutefois entraîner des
  **frais de service** couvrant la distribution.
- Le sous-ensemble **gratuit et en libre téléchargement** (data.ecmwf.int, miroirs cloud dont AWS)
  reste à **0,25°** aujourd'hui. L'ECMWF annonce son extension aux prévisions **9 km** « plus tard en 2026 »,
  avec **2 h de latence** à cause du volume.
- **Open-Meteo** diffuse déjà l'IFS à 9 km (données ouvertes CC-BY), extraites au point. Son API
  publique reste **non commerciale**, ce qui convient à l'usage interne actuel (Q1).

## Options

| Option | Coût | Délai | Remarques |
|---|---|---|---|
| Rester à 0,25° (actuel) | gratuit | disponible | Implémenté (AWS, data.ecmwf.int, Open-Meteo `ecmwf_ifs025`). |
| Open-Meteo IFS 9 km | gratuit (non commercial) ; abonnement pour un usage commercial | ~1 jour de développement | La recherche des points sur la grille native (octaédrique O1280) doit être ajoutée. En attendant, Open-Meteo renvoie la cellule native la plus proche. |
| Sous-ensemble ouvert 9 km de l'ECMWF | gratuit | quand l'ECMWF le publiera | Même adaptateur que l'open data 0,25°, avec une grille différente et des fichiers plus lourds. |
| Service ECMWF (livraison sur contrat) | frais de service | commande et contrat | Source `ecmwf_hres_01` déjà prévue : désactivée, activation par un administrateur, acceptation explicite à chaque demande. |

**Décision (22/09/2026)** : IFS 9 km via Open-Meteo **implémenté** (modèle `ifs_9km`). Bascule prévue
vers le flux ouvert ECMWF 9 km dès sa publication ; aucun service payant commandé.

### Mise en œuvre
- Grille native **O1280** (Gauss réduite octaédrique, 6 599 680 points) : recherche des 4 points
  encadrants (2 sur la rangée nord, 2 sur la rangée sud) ou des N plus proches, index natif GRIB.
- Téléchargement via Open-Meteo `models=ecmwf_ifs`, `cell_selection=nearest`, sans correction
  d'altitude ; échéances natives conservées : horaire jusqu'à 90 h, 3-horaire jusqu'à 144 h, puis
  6-horaire (calendrier à confirmer sur les runs 06/18).
- **Limite** : altitude du modèle et masque terre/mer non disponibles pour cette grille dans
  l'application (champs invariants O1280 non publiés en open data à ce jour).

Sources : annonce ECMWF « ECMWF makes its entire Real-time Catalogue open to all » (2025), page
dataset « IFS Medium-range Control forecast (set I) », documentation Open-Meteo « ECMWF API ».
