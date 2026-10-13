"""Shared ray/surface intersections for logical visibility and image depth.

Ray directions need not be normalized: returned t satisfies origin + t * direction.
No camera, experiment clock, or window is owned by this module.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from chongming.contracts.model import Vec3M
from chongming.contracts.scene import (
    BoxGeometry,
    GeometrySpec,
    KinematicState,
    SphereGeometry,
)

FloatArray = NDArray[np.float64]
NEAR = 1e-6


def surface_hits(
    origin: Vec3M,
    directions: FloatArray,
    pose: KinematicState,
    geometry: GeometrySpec,
) -> tuple[FloatArray, FloatArray]:
    """Return nearest positive depths and ENU normals; misses have infinite depth."""
    rotation = np.asarray(pose.rotation_enu_from_body, dtype=np.float64)
    local_origin = (np.asarray(origin) - pose.position_enu_m) @ rotation
    rays = directions @ rotation
    count = len(rays)
    normals = np.zeros((count, 3), dtype=np.float64)
    depth = np.full(count, np.inf)
    if isinstance(geometry, SphereGeometry):
        if geometry.radius_m <= 0:
            raise ValueError("sphere radius must be positive")
        a = np.einsum("ij,ij->i", rays, rays)
        b = rays @ local_origin
        c = float(local_origin @ local_origin) - geometry.radius_m**2
        discriminant = b * b - a * c
        root = np.sqrt(np.maximum(discriminant, 0.0))
        near = (-b - root) / a
        far = (-b + root) / a
        chosen = np.where(near > NEAR, near, far)
        hit = (discriminant >= 0) & (chosen > NEAR)
        depth[hit] = chosen[hit]
        normals[hit] = (local_origin + chosen[hit, None] * rays[hit]) / geometry.radius_m
    elif isinstance(geometry, BoxGeometry):
        half = np.asarray(geometry.half_extents_body_m)
        if np.any(half <= 0):
            raise ValueError("box half extents must be positive")
        near_axis = np.full_like(rays, -np.inf)
        far_axis = np.full_like(rays, np.inf)
        parallel = np.abs(rays) < 1e-12
        np.divide(-half - local_origin, rays, out=near_axis, where=~parallel)
        np.divide(half - local_origin, rays, out=far_axis, where=~parallel)
        outside_parallel = np.any(parallel & (np.abs(local_origin) > half), axis=1)
        entry_axis = np.minimum(near_axis, far_axis)
        exit_axis = np.maximum(near_axis, far_axis)
        near = np.max(entry_axis, axis=1)
        far = np.min(exit_axis, axis=1)
        chosen = np.where(near > NEAR, near, far)
        hit = ~outside_parallel & (far >= near) & (chosen > NEAR)
        depth[hit] = chosen[hit]
        points = local_origin + chosen[hit, None] * rays[hit]
        axes = np.argmax(np.abs(points / half), axis=1)
        indices = np.flatnonzero(hit)
        normals[indices, axes] = np.sign(points[np.arange(len(points)), axes])
    else:
        vertices = np.asarray(geometry.vertices_body_m, dtype=np.float64)
        for triangle in geometry.triangle_vertex_indices:
            if min(triangle) < 0 or max(triangle) >= len(vertices):
                raise ValueError("mesh triangle references an absent vertex")
            v0, v1, v2 = vertices[list(triangle)]
            edge1, edge2 = v1 - v0, v2 - v0
            normal = np.cross(edge1, edge2)
            length = float(np.linalg.norm(normal))
            if length < 1e-12:
                continue
            p = np.cross(rays, edge2)
            determinant = p @ edge1
            inverse = np.zeros(count)
            np.divide(1.0, determinant, out=inverse, where=np.abs(determinant) > 1e-12)
            offset = local_origin - v0
            u = (p @ offset) * inverse
            q = np.cross(offset, edge1)
            v = (rays @ q) * inverse
            distance = float(edge2 @ q) * inverse
            hit = (
                (np.abs(determinant) > 1e-12)
                & (u >= -1e-10)
                & (v >= -1e-10)
                & (u + v <= 1.0 + 1e-10)
                & (distance > NEAR)
                & (distance < depth)
            )
            depth[hit] = distance[hit]
            normals[hit] = normal / length
            # Meshes are double sided; shade the surface facing the viewer.
            reverse = hit & ((rays @ normal) > 0)
            normals[reverse] *= -1
    return depth, normals @ rotation.T


def contains_point(origin: Vec3M, pose: KinematicState, geometry: GeometrySpec) -> bool:
    """Closed solid containment for sphere/box camera-inside-blocker cases."""
    point = (np.asarray(origin) - pose.position_enu_m) @ np.asarray(pose.rotation_enu_from_body)
    if isinstance(geometry, SphereGeometry):
        return bool(point @ point < geometry.radius_m**2)
    if isinstance(geometry, BoxGeometry):
        return bool(np.all(np.abs(point) < geometry.half_extents_body_m))
    return False
