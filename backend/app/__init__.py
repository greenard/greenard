# Avec les wheels pip, pyproj doit être chargé avant eccodes (bibliothèques partagées embarquées
# incompatibles selon l'ordre de chargement) ; sans effet avec l'image conda-forge.
import pyproj  # noqa: F401
