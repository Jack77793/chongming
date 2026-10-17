"""Small deterministic procedural vegetation assets; no external asset license."""

from __future__ import annotations

import math

from chongming.contracts.scene import TriangleMeshGeometry


def tree_geometry(
    height: float, crown_radius: float
) -> tuple[TriangleMeshGeometry, TriangleMeshGeometry]:
    """Build an inexpensive octagonal trunk and three-tier conifer canopy."""
    sides = 8
    trunk_radius = max(0.12, crown_radius * 0.12)
    trunk_vertices = [
        (trunk_radius * math.cos(2 * math.pi * i / sides),
         trunk_radius * math.sin(2 * math.pi * i / sides), z)
        for z in (0.0, height * 0.58)
        for i in range(sides)
    ]
    trunk_triangles: list[tuple[int, int, int]] = []
    for i in range(sides):
        j = (i + 1) % sides
        trunk_triangles.extend(((i, j, sides + j), (i, sides + j, sides + i)))

    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    base_z = height * 0.42
    tier_height = height * 0.25
    for tier in range(3):
        bottom_z = base_z + tier * tier_height * 0.58
        top_z = bottom_z + tier_height
        radius = crown_radius * (1.0 - tier * 0.22)
        first = len(vertices)
        vertices.extend(
            (radius * math.cos(2 * math.pi * i / sides),
             radius * math.sin(2 * math.pi * i / sides), bottom_z)
            for i in range(sides)
        )
        tip = len(vertices)
        vertices.append((0.0, 0.0, top_z))
        triangles.extend(
            (first + i, first + (i + 1) % sides, tip) for i in range(sides)
        )
    canopy = [(x, y, z - base_z) for x, y, z in vertices]
    return (
        TriangleMeshGeometry(tuple(trunk_vertices), tuple(trunk_triangles)),
        TriangleMeshGeometry(tuple(canopy), tuple(triangles)),
    )
