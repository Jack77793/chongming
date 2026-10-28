"""Conservative CPU BVH for finite scene blockers; exact hit rules stay elsewhere."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from chongming.contracts.model import Vec3M
from chongming.contracts.scene import (
    BoxGeometry,
    CylinderGeometry,
    SphereGeometry,
    TriangleMeshGeometry,
)
from chongming.rendering.geometry import resolve_geometry

Bounds = tuple[Vec3M, Vec3M]


def segment_overlaps(origin: Vec3M, target: Vec3M, bounds: Bounds) -> bool:
    """Inclusive slab test with padding: tangent and terminal hits must survive."""
    entry, exit_ = 0.0, 1.0
    for axis in range(3):
        lo, hi = bounds[0][axis] - 1e-7, bounds[1][axis] + 1e-7
        delta = target[axis] - origin[axis]
        if abs(delta) < 1e-15:
            if origin[axis] < lo or origin[axis] > hi:
                return False
        else:
            a, b = (lo - origin[axis]) / delta, (hi - origin[axis]) / delta
            entry, exit_ = max(entry, min(a, b)), min(exit_, max(a, b))
            if entry > exit_:
                return False
    return True


def entity_bounds(entity: object) -> Bounds | None:
    pose = getattr(entity, "pose", getattr(entity, "base_pose", None))
    geometry = getattr(entity, "geometry", None)
    if pose is None:
        return None
    if geometry is not None:
        geometry = resolve_geometry(geometry)
    if isinstance(geometry, SphereGeometry):
        minimum: Vec3M = (geometry.radius_m * -1.0,) * 3
        maximum: Vec3M = (geometry.radius_m,) * 3
    elif isinstance(geometry, BoxGeometry):
        maximum = geometry.half_extents_body_m
        minimum = tuple(-v for v in maximum)  # type: ignore[assignment]
    elif isinstance(geometry, CylinderGeometry):
        minimum = (-geometry.radius_m, -geometry.radius_m, 0.0)
        maximum = (geometry.radius_m, geometry.radius_m, geometry.height_m)
    elif isinstance(geometry, TriangleMeshGeometry) and geometry.vertices_body_m:
        minimum = tuple(min(p[k] for p in geometry.vertices_body_m) for k in range(3))  # type: ignore[assignment]
        maximum = tuple(max(p[k] for p in geometry.vertices_body_m) for k in range(3))  # type: ignore[assignment]
    else:
        return None
    corners = [
        tuple(
            pose.position_enu_m[k]
            + sum(pose.rotation_enu_from_body[k][j] * point[j] for j in range(3))
            for k in range(3)
        )
        for point in product(*zip(minimum, maximum))
    ]
    lo = tuple(min(p[k] for p in corners) for k in range(3))
    hi = tuple(max(p[k] for p in corners) for k in range(3))
    return (lo, hi)


@dataclass(frozen=True)
class _Node:
    bounds: Bounds
    indices: tuple[int, ...] = ()
    children: tuple[_Node, ...] = ()


class OcclusionIndex:
    """Retains immutable entities so identity reuse cannot leave stale bounds.

    Unsupported/unbounded entities always pass through. Candidate order matches
    input order, preserving stable ordering of equal-distance occlusions.
    """

    def __init__(self, entities: tuple[object, ...]) -> None:
        self.entities = entities
        bounded = []
        self.unbounded = []
        for index, entity in enumerate(entities):
            if not getattr(entity, "occludes_sight_lines", False):
                continue
            bounds = entity_bounds(entity)
            if bounds is None:
                self.unbounded.append(index)
            else:
                bounded.append((index, bounds))

        def build(items: list[tuple[int, Bounds]]) -> _Node | None:
            if not items:
                return None
            lo: Vec3M = tuple(min(b[0][k] for _, b in items) for k in range(3))  # type: ignore[assignment]
            hi: Vec3M = tuple(max(b[1][k] for _, b in items) for k in range(3))  # type: ignore[assignment]
            if len(items) <= 8:
                return _Node((lo, hi), tuple(i for i, _ in items))
            axis = max(range(3), key=lambda k: hi[k] - lo[k])
            items.sort(key=lambda item: item[1][0][axis] + item[1][1][axis])
            middle = len(items) // 2
            children = tuple(n for n in (build(items[:middle]), build(items[middle:])) if n)
            return _Node((lo, hi), children=children)

        self.root = build(bounded)
        self.bounds = dict(bounded)

    def matches(self, entities: tuple[object, ...]) -> bool:
        return len(entities) == len(self.entities) and all(
            a is b for a, b in zip(entities, self.entities)
        )

    def candidates(self, origin: Vec3M, target: Vec3M) -> tuple[object, ...]:
        found = list(self.unbounded)
        pending = [self.root] if self.root is not None else []
        while pending:
            node = pending.pop()
            if not segment_overlaps(origin, target, node.bounds):
                continue
            pending.extend(node.children)
            found.extend(
                i for i in node.indices if segment_overlaps(origin, target, self.bounds[i])
            )
        return tuple(self.entities[i] for i in sorted(found))
