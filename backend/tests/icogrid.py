"""Générateur de grille icosaédrique synthétique (subdivision récursive), pour les tests.

Même topologie que les grilles ICON (triangles sur la sphère obtenus par bissection des
arêtes de l'icosaèdre) ; centres de cellule = centroïdes projetés sur la sphère.
"""

import numpy as np


def icosphere(level: int):
    t = (1 + 5**0.5) / 2
    v = np.array(
        [
            [-1, t, 0],
            [1, t, 0],
            [-1, -t, 0],
            [1, -t, 0],
            [0, -1, t],
            [0, 1, t],
            [0, -1, -t],
            [0, 1, -t],
            [t, 0, -1],
            [t, 0, 1],
            [-t, 0, -1],
            [-t, 0, 1],
        ],
        dtype=float,
    )
    v /= np.linalg.norm(v, axis=1)[:, None]
    f = np.array(
        [
            [0, 11, 5],
            [0, 5, 1],
            [0, 1, 7],
            [0, 7, 10],
            [0, 10, 11],
            [1, 5, 9],
            [5, 11, 4],
            [11, 10, 2],
            [10, 7, 6],
            [7, 1, 8],
            [3, 9, 4],
            [3, 4, 2],
            [3, 2, 6],
            [3, 6, 8],
            [3, 8, 9],
            [4, 9, 5],
            [2, 4, 11],
            [6, 2, 10],
            [8, 6, 7],
            [9, 8, 1],
        ]
    )
    verts = list(v)
    for _ in range(level):
        cache: dict[tuple[int, int], int] = {}

        def mid(a, b, cache=cache):
            key = (min(a, b), max(a, b))
            if key not in cache:
                m = verts[a] + verts[b]
                verts.append(m / np.linalg.norm(m))
                cache[key] = len(verts) - 1
            return cache[key]

        nf = []
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [[a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]]
        f = np.array(nf)
    return np.array(verts), f


def synthetic_grid(level: int):
    """Retourne (clat, clon, vertex_of_cell (3, n) base 1, vlat, vlon) en radians, format DWD."""
    verts, faces = icosphere(level)
    cent = verts[faces].mean(axis=1)
    cent /= np.linalg.norm(cent, axis=1)[:, None]
    clat, clon = np.arcsin(cent[:, 2]), np.arctan2(cent[:, 1], cent[:, 0])
    vlat, vlon = np.arcsin(verts[:, 2]), np.arctan2(verts[:, 1], verts[:, 0])
    return clat, clon, (faces + 1).T, vlat, vlon
