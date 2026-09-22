# Greenard — Prévision météo & production éolienne

Application web de prévision météorologique (modèles NWP ouverts : GFS, ECMWF IFS, ICON, ICON-EU)
et de prévision de production éolienne (PyWake, pertes IEC 61400-15-2, probabiliste).

**Statut : phase de conception.** La note d'architecture est en attente de validation :
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Le code sera livré par jalons (voir §10 de la note) :

1. Carte et points de grille
2. Téléchargement des prévisions
3. Import du parc et du mât
4. PyWake et terrain
5. Pertes et probabiliste
6. Calibration ML et évaluation
