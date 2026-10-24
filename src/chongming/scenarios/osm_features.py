"""Offline OSM roads, tree points and polygon surfaces; no renderer ownership."""

from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, replace

from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize

from chongming.contracts import scene as s

TAGS = frozenset(
    {
        "highway",
        "width",
        "lanes",
        "natural",
        "landuse",
        "leisure",
        "water",
        "waterway",
        "leaf_type",
        "leaf_cycle",
        "diameter_crown",
    }
)
ROAD_WIDTHS = {
    "motorway": 12.0,
    "trunk": 10.0,
    "primary": 8.0,
    "secondary": 7.0,
    "tertiary": 6.0,
    "residential": 5.0,
    "unclassified": 5.0,
    "service": 3.0,
    "living_street": 4.0,
    "pedestrian": 3.0,
    "track": 2.5,
    "footway": 1.5,
    "path": 1.5,
    "cycleway": 2.0,
    "steps": 1.5,
}


def category(tags: dict[str, str]) -> str | None:
    if tags.get("natural") == "water" or tags.get("waterway") == "riverbank":
        return "water"
    if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
        return "forest"
    if (
        tags.get("landuse") in {"grass", "meadow", "recreation_ground", "village_green"}
        or tags.get("natural") == "grassland"
        or tags.get("leisure") in {"park", "garden"}
    ):
        return "grass"
    return None


def prepare_features(
    content: bytes, config: s.OSMImportConfig
) -> tuple[
    tuple[s.OSMRoad, ...],
    tuple[s.OSMPointFeature, ...],
    tuple[s.OSMArea, ...],
    tuple[s.OSMDiagnostic, ...],
    bytes,
]:
    from chongming.scenarios.osm import TAGS as BUILDING_TAGS
    from chongming.scenarios.osm import _elements, _id, _metres, _tags, canonical_ring, project_enu

    ways = {}
    relations = {}
    tree_ids = set()
    for element in _elements(content):
        if element.tag == "bounds":
            continue
        identity = _id(element.attrib["id"])
        tags = {k: v for k, v in _tags(element).items() if k in (BUILDING_TAGS | TAGS)}
        if element.tag == "way":
            ways[identity] = (tuple(_id(n.attrib["ref"]) for n in element.findall("nd")), tags)
        elif element.tag == "relation" and category(tags) and tags.get("type") == "multipolygon":
            relations[identity] = (
                tuple(
                    (m.get("type"), _id(m.attrib["ref"]), m.get("role", ""))
                    for m in element.findall("member")
                ),
                tags,
            )
        elif element.tag == "node" and tags.get("natural") == "tree":
            tree_ids.add(identity)
    required_ways = {i for i, (_, t) in ways.items() if category(t) or "highway" in t}
    required_ways.update(
        ref for members, _ in relations.values() for kind, ref, _ in members if kind == "way"
    )
    ways = {i: v for i, v in ways.items() if i in required_ways}
    required_nodes = tree_ids | {ref for refs, _ in ways.values() for ref in refs}
    nodes = {}
    node_tags = {}
    for element in _elements(content):
        if element.tag == "node" and int(element.attrib["id"]) in required_nodes:
            identity = _id(element.attrib["id"])
            if identity in nodes:
                raise ValueError("duplicate referenced OSM node")
            point = (float(element.attrib["lon"]), float(element.attrib["lat"]))
            project_enu(*point, config.anchor)
            nodes[identity] = point
            node_tags[identity] = {
                k: v for k, v in _tags(element).items() if k in (BUILDING_TAGS | TAGS)
            }
    west, south, east, north = config.bounds_wgs84
    selection = box(west, south, east, north)
    roads = []
    points = []
    areas = []
    diagnostics = []
    member_ids = {
        ref for members, _ in relations.values() for kind, ref, _ in members if kind == "way"
    }

    def add_area(identity: str, polygon: Polygon, tags: dict[str, str]) -> None:
        if not polygon.is_valid or polygon.area <= 0:
            raise ValueError("invalid_area_polygon")
        clipped = polygon.intersection(selection)
        polygons = (
            [clipped] if clipped.geom_type == "Polygon" else list(getattr(clipped, "geoms", ()))
        )
        rings_list = []
        for p in polygons:
            if not isinstance(p, Polygon) or p.area == 0:
                continue
            outer = canonical_ring(
                tuple(project_enu(x, y, config.anchor) for x, y in list(p.exterior.coords)[:-1])
            )
            holes = sorted(
                canonical_ring(
                    tuple(project_enu(x, y, config.anchor) for x, y in list(h.coords)[:-1])
                )
                for h in p.interiors
            )
            rings_list.append((outer, *holes))
        for index, rings in enumerate(sorted(rings_list)):
            areas.append(
                s.OSMArea(
                    f"{identity}/{index}", rings, str(category(tags)), tuple(sorted(tags.items()))
                )
            )

    for identity, (refs, tags) in sorted(ways.items()):
        try:
            if any(ref not in nodes for ref in refs):
                raise ValueError("missing_node")
            coords = [nodes[ref] for ref in refs]
            road_class = tags.get("highway", "")
            if road_class:
                default = ROAD_WIDTHS.get(road_class.removesuffix("_link"))
                if default is None or tags.get("area") == "yes":
                    raise ValueError("unsupported_road_class")
                if (
                    tags.get("bridge", "no") != "no"
                    or tags.get("tunnel", "no") != "no"
                    or tags.get("layer", "0") != "0"
                ):
                    raise ValueError("unsupported_raised_or_underground_road")
                if len(set(coords)) < 2:
                    raise ValueError("degenerate_road")
                # Keep one stable way ID; split segments are represented in the mesh builder.
                line = LineString(coords)
                if not line.intersects(selection):
                    continue
                width = _metres(tags.get("width"))
                if width is None or not 0.3 <= width <= 40:
                    width = default
                    diagnostics.append(s.OSMDiagnostic(f"way/{identity}", "default_road_width"))
                roads.append(
                    s.OSMRoad(
                        identity,
                        tuple(project_enu(*p, config.anchor) for p in coords),
                        width,
                        road_class,
                        tuple(sorted(tags.items())),
                    )
                )
            if category(tags) and identity not in member_ids:
                if len(refs) < 4 or refs[0] != refs[-1]:
                    raise ValueError("unclosed_area")
                add_area(f"way/{identity}", Polygon(coords), tags)
        except ValueError as error:
            diagnostics.append(s.OSMDiagnostic(f"way/{identity}", str(error)))
    for identity, (members, tags) in sorted(relations.items()):
        start_count = len(areas)
        try:
            if any(
                kind != "way" or role not in {"outer", "inner", ""} or ref not in ways
                for kind, ref, role in members
            ):
                raise ValueError("unsupported_area_member")
            boundaries: dict[str, list[LineString]] = {"outer": [], "inner": []}
            for _, ref, role in members:
                refs = ways[ref][0]
                if len(refs) < 2 or any(n not in nodes for n in refs):
                    raise ValueError("missing_area_node")
                boundaries[role or "outer"].append(LineString([nodes[n] for n in refs]))
            # polygonize joins split OSM ways without filling unclosed boundaries.
            outer = list(polygonize(boundaries["outer"]))
            inner = list(polygonize(boundaries["inner"]))
            if (
                not outer
                or sum(p.length for p in outer) + sum(p.length for p in inner)
                < sum(l.length for lines in boundaries.values() for l in lines) - 1e-10
            ):
                raise ValueError("unclosed_area_relation")
            if any(sum(p.contains(h) for p in outer) != 1 for h in inner):
                raise ValueError("orphan_area_hole")
            for index, p in enumerate(sorted(outer, key=lambda p: p.normalize().wkt)):
                holes = [list(h.exterior.coords) for h in inner if p.contains(h)]
                add_area(f"relation/{identity}/{index}", Polygon(p.exterior.coords, holes), tags)
        except ValueError as error:
            del areas[start_count:]
            diagnostics.append(s.OSMDiagnostic(f"relation/{identity}", str(error)))
    for identity in sorted(tree_ids):
        lon, lat = nodes[identity]
        if not selection.covers(Point(lon, lat)):
            continue
        tags = node_tags[identity]
        height = _metres(tags.get("height"))
        crown = _metres(tags.get("diameter_crown"))
        if height is None or not 1 <= height <= 60:
            height = 10.0
            diagnostics.append(s.OSMDiagnostic(f"node/{identity}", "default_tree_height"))
        radius = crown / 2 if crown is not None and 1 <= crown <= 40 else height * 0.25
        points.append(
            s.OSMPointFeature(
                identity,
                project_enu(lon, lat, config.anchor),
                "tree",
                height,
                radius,
                tuple(sorted(tags.items())),
            )
        )
    cleaned = ET.Element("osm", version="0.6")
    for identity, (lon, lat) in sorted(nodes.items()):
        node = ET.SubElement(cleaned, "node", id=str(identity), lon=str(lon), lat=str(lat))
        for k, v in sorted(node_tags[identity].items()):
            ET.SubElement(node, "tag", k=k, v=v)
    for identity, (refs, tags) in sorted(ways.items()):
        way = ET.SubElement(cleaned, "way", id=str(identity))
        for ref in refs:
            ET.SubElement(way, "nd", ref=str(ref))
        for k, v in sorted(tags.items()):
            ET.SubElement(way, "tag", k=k, v=v)
    for identity, (members, tags) in sorted(relations.items()):
        rel = ET.SubElement(cleaned, "relation", id=str(identity))
        for kind, ref, role in members:
            ET.SubElement(rel, "member", type=str(kind), ref=str(ref), role=role)
        for k, v in sorted(tags.items()):
            ET.SubElement(rel, "tag", k=k, v=v)
    return (
        tuple(roads),
        tuple(points),
        tuple(sorted(areas, key=lambda a: a.element_id)),
        tuple(diagnostics),
        ET.tostring(cleaned),
    )


def surface_mesh(
    rings: tuple[tuple[tuple[float, float], ...], ...],
    height: Callable[[float, float], float],
    grid: s.OSMElevation | None = None,
) -> s.TriangleMeshGeometry:
    polygon = Polygon(rings[0], rings[1:])
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError("invalid_area_polygon")
    patches = [polygon]
    if grid is not None:
        # Cut surfaces at terrain triangle edges so roads and land cover follow
        # the very same piecewise planar ground, including long forest outlines.
        patches = []
        ox, oy = grid.origin_enu_m
        spacing = grid.spacing_m
        left, bottom, right, top = polygon.bounds
        for y in range(
            max(0, math.floor((bottom - oy) / spacing)),
            min(len(grid.heights_m) - 1, math.ceil((top - oy) / spacing)),
        ):
            for x in range(
                max(0, math.floor((left - ox) / spacing)),
                min(len(grid.heights_m[0]) - 1, math.ceil((right - ox) / spacing)),
            ):
                a, b = (
                    (ox + x * spacing, oy + y * spacing),
                    (ox + (x + 1) * spacing, oy + y * spacing),
                )
                c, d = (a[0], a[1] + spacing), (b[0], b[1] + spacing)
                for cell in (Polygon([a, b, d]), Polygon([a, d, c])):
                    part = polygon.intersection(cell)
                    if isinstance(part, Polygon) and part.area > 1e-10:
                        patches.append(part)
                    elif hasattr(part, "geoms"):
                        patches.extend(
                            p for p in part.geoms if isinstance(p, Polygon) and p.area > 1e-10
                        )
    triangles = []
    for triangle in (t for p in patches for t in constrained_delaunay_triangles(p).geoms):
        assert isinstance(triangle, Polygon)
        coords = tuple((float(x), float(y)) for x, y in list(triangle.exterior.coords)[:3])
        a, b, c = coords
        if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) < 0:
            coords = (a, c, b)
        start = min(range(3), key=coords.__getitem__)
        triangles.append(coords[start:] + coords[:start])
    vertices = sorted({p for t in triangles for p in t})
    indices = {p: i for i, p in enumerate(vertices)}
    return s.TriangleMeshGeometry(
        tuple((x, y, height(x, y)) for x, y in vertices),
        tuple((indices[a], indices[b], indices[c]) for a, b, c in sorted(triangles)),
    )


def feature_objects(imported: s.OSMImport) -> tuple[s.WorldObjectSpec, ...]:
    from chongming.scenarios.osm import project_enu
    from chongming.scenarios.osm_elevation import elevation_at

    pose = s.KinematicState(
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        (0.0, 0.0, 0.0),
    )
    objects = []
    colors = {
        "road": (0.14, 0.14, 0.14),
        "forest": (0.09, 0.19, 0.07),
        "grass": (0.22, 0.36, 0.12),
        "water": (0.06, 0.22, 0.32),
    }
    west, south, east, north = imported.config.bounds_wgs84
    selection = Polygon(
        [
            project_enu(x, y, imported.config.anchor)
            for x, y in ((west, south), (east, south), (east, north), (west, north))
        ]
    )
    for road in imported.roads:
        shape = (
            LineString(road.centerline_enu_m)
            .buffer(road.width_m / 2, join_style="bevel")
            .intersection(selection)
        )
        polygons = [shape] if shape.geom_type == "Polygon" else list(getattr(shape, "geoms", ()))
        for index, p in enumerate(sorted(polygons, key=lambda p: p.wkt)):
            if not isinstance(p, Polygon) or p.area <= 0:
                continue
            rings = tuple(
                tuple((float(x), float(y)) for x, y in list(r.coords)[:-1])
                for r in (p.exterior, *p.interiors)
            )
            mesh = surface_mesh(
                rings, lambda x, y: elevation_at(imported, x, y) + 0.08, imported.elevation
            )
            objects.append(
                s.WorldObjectSpec(
                    s.WorldObjectId(f"osm/road/{road.osm_way_id}/{index}"),
                    f"OSM road {road.osm_way_id}",
                    s.WorldObjectKind.FIXED_REFERENCE,
                    "osm-road",
                    pose,
                    mesh,
                    s.SurfaceAppearance(colors["road"], 0.9),
                    False,
                )
            )
    for area in imported.areas:
        # Land cover, including water, is draped over the coarse DEM. Inferring a
        # horizontal water level without bathymetry can bury the lake in terrain.
        offset = 0.04 if area.category == "water" else 0.03

        def height(x: float, y: float, offset: float = offset) -> float:
            return elevation_at(imported, x, y) + offset

        mesh = surface_mesh(
            area.rings_enu_m,
            height,
            imported.elevation,
        )
        objects.append(
            s.WorldObjectSpec(
                s.WorldObjectId(f"osm/area/{area.element_id}"),
                f"OSM {area.category} {area.element_id}",
                s.WorldObjectKind.FIXED_REFERENCE,
                f"osm-{area.category}",
                pose,
                mesh,
                s.SurfaceAppearance(colors[area.category], 0.9),
                False,
            )
        )
    return tuple(objects)


def forest_points(imported: s.OSMImport) -> tuple[s.OSMPointFeature, ...]:
    """Sparse deterministic stands; forest masks, holes and exclusion masks are authoritative.

    This is a bounded visual/occlusion approximation, not an inferred real tree census.
    Generated negative IDs cannot collide with positive OSM node IDs.
    """
    from shapely.ops import unary_union

    spacing, budget = imported.config.forest_spacing_m, imported.config.maximum_forest_trees
    if not budget:
        return ()
    forests = [
        Polygon(a.rings_enu_m[0], a.rings_enu_m[1:])
        for a in imported.areas
        if a.category == "forest"
    ]
    if not forests:
        return ()
    forest = unary_union(forests)
    exclusions = [Polygon(b.footprint_enu_m) for b in imported.buildings]
    exclusions.extend(
        Polygon(a.rings_enu_m[0], a.rings_enu_m[1:])
        for a in imported.areas
        if a.category == "water"
    )
    exclusions.extend(
        LineString(r.centerline_enu_m).buffer(r.width_m / 2 + 2) for r in imported.roads
    )
    exclusions.extend(Point(*p.position_enu_m).buffer(p.radius_m + 2) for p in imported.points)
    excluded = unary_union(exclusions)
    left, bottom, right, top = forest.bounds
    nx, ny = math.ceil((right - left) / spacing) + 2, math.ceil((top - bottom) / spacing) + 2
    if nx * ny > 250_000:
        raise ValueError("forest candidate budget exceeded; increase forest_spacing_m")
    candidates = []
    for ix in range(math.floor(left / spacing), math.ceil(right / spacing)):
        for iy in range(math.floor(bottom / spacing), math.ceil(top / spacing)):
            digest = hashlib.sha256(f"osm-forest-42/{ix}/{iy}".encode()).digest()
            x = (ix + 0.2 + 0.6 * int.from_bytes(digest[:4]) / 2**32) * spacing
            y = (iy + 0.2 + 0.6 * int.from_bytes(digest[4:8]) / 2**32) * spacing
            point = Point(x, y)
            if forest.contains(point) and not excluded.covers(point):
                candidates.append((digest, x, y))
    result = []
    for digest, x, y in sorted(candidates)[:budget]:
        height = 9.0 + digest[8] / 255 * 5
        result.append(
            s.OSMPointFeature(
                -int.from_bytes(digest[:8]),
                (x, y),
                "tree",
                height,
                height * 0.25,
                (("natural", "tree"),),
            )
        )
    return tuple(result)


def _unit(digest: bytes, offset: int) -> float:
    return int.from_bytes(digest[offset : offset + 4], "big") / 2**32


@dataclass(frozen=True)
class _PlantCandidate:
    priority: float
    tiebreak: bytes
    x: float
    y: float
    ix: int
    iy: int
    pass_index: int


class _PlacementIndex:
    """Small deterministic spatial hash used to keep generated trunks apart."""

    def __init__(self, cell_size: float = 8.0) -> None:
        self.cell_size = cell_size
        self.cells: dict[tuple[int, int], list[tuple[float, float]]] = {}

    def add(self, x: float, y: float) -> None:
        key = (math.floor(x / self.cell_size), math.floor(y / self.cell_size))
        self.cells.setdefault(key, []).append((x, y))

    def conflicts(self, x: float, y: float, clearance: float) -> bool:
        reach = max(1, math.ceil(clearance / self.cell_size))
        cx, cy = math.floor(x / self.cell_size), math.floor(y / self.cell_size)
        for ix in range(cx - reach, cx + reach + 1):
            for iy in range(cy - reach, cy + reach + 1):
                for px, py in self.cells.get((ix, iy), ()):
                    if math.hypot(px - x, py - y) < clearance:
                        return True
        return False


def _area_factor(seed: int, element_id: str) -> float:
    digest = hashlib.sha256(f"osm-vegetation/{seed}/area/{element_id}".encode()).digest()
    return 0.75 + 0.5 * _unit(digest, 0)


def _density_target(mask: BaseGeometry, density: float, factor: float, per_area_budget: int) -> int:
    return min(per_area_budget, round(density * mask.area / 10_000.0 * factor))


def _plant_candidates(
    mask: BaseGeometry,
    density: float,
    *,
    seed: int,
    token: str,
    clustering: float,
    pass_index: int = 0,
) -> list[_PlantCandidate]:
    """Jittered-grid candidates sorted into deterministic vegetation clusters.

    Sorting by a blend of fine and coarse hashes first fills locations inside
    coherent clusters; the remainder of the sorted list acts as clearings and
    slope/exclusion fallbacks. No wall clock or global RNG is involved.
    """
    if density <= 0 or mask.is_empty:
        return []
    spacing = max(3.0, min(50.0, math.sqrt(10_000.0 / density) * 0.72))
    left, bottom, right, top = mask.bounds
    x0, x1 = math.floor(left / spacing), math.ceil(right / spacing)
    y0, y1 = math.floor(bottom / spacing), math.ceil(top / spacing)
    if (x1 - x0) * (y1 - y0) > 250_000:
        raise ValueError("OSM vegetation candidate budget exceeded; reduce the selected area")
    cluster = max(1, round(45.0 / spacing))
    prefix = f"osm-vegetation/{seed}/{token}/pass/{pass_index}"
    candidates: list[_PlantCandidate] = []
    for ix in range(x0, x1):
        for iy in range(y0, y1):
            digest = hashlib.sha256(f"{prefix}/{ix}/{iy}".encode()).digest()
            x = (ix + 0.2 + 0.6 * _unit(digest, 0)) * spacing
            y = (iy + 0.2 + 0.6 * _unit(digest, 4)) * spacing
            if not mask.covers(Point(x, y)):
                continue
            coarse = hashlib.sha256(
                f"{prefix}/cluster/{ix // cluster}/{iy // cluster}".encode()
            ).digest()
            priority = (1.0 - clustering) * _unit(digest, 8) + clustering * _unit(coarse, 0)
            candidates.append(_PlantCandidate(priority, digest[12:16], x, y, ix, iy, pass_index))
    candidates.sort(key=lambda item: (item.priority, item.tiebreak))
    return candidates


def _hard_surface_exclusions(
    imported: s.OSMImport,
    *,
    building_margin: float,
    road_margin: float,
    water_margin: float,
) -> BaseGeometry | None:
    from shapely.ops import unary_union

    exclusions: list[BaseGeometry] = [
        Polygon(building.footprint_enu_m).buffer(building_margin) for building in imported.buildings
    ]
    exclusions.extend(
        LineString(road.centerline_enu_m).buffer(road.width_m / 2 + road_margin)
        for road in imported.roads
    )
    exclusions.extend(
        Polygon(area.rings_enu_m[0], area.rings_enu_m[1:]).buffer(water_margin)
        for area in imported.areas
        if area.category == "water"
    )
    return unary_union(exclusions) if exclusions else None


def _with_point_exclusions(
    excluded: BaseGeometry | None,
    points: tuple[s.OSMPointFeature, ...],
    margin: float,
) -> BaseGeometry | None:
    from shapely.ops import unary_union

    buffers = [Point(*point.position_enu_m).buffer(point.radius_m + margin) for point in points]
    if not buffers:
        return excluded
    combined = unary_union(buffers)
    return combined if excluded is None else unary_union((excluded, combined))


def _upright_pose(
    imported: s.OSMImport, x: float, y: float, base_radius: float
) -> s.KinematicState | None:
    """Place an upright plant on locally flat ground; reject DEM creases.

    The coarse DEM is piecewise planar. Rejecting a steep local gradient or a
    large elevation step under the bole prevents trunks from floating or being
    buried at a terrain break. Grass patches use the stricter ``_grass_pose``.
    """
    from chongming.scenarios.osm_elevation import elevation_at

    z = elevation_at(imported, x, y)
    tolerance = max(0.35, base_radius * 0.75)
    for dx, dy in (
        (base_radius, 0.0),
        (-base_radius, 0.0),
        (0.0, base_radius),
        (0.0, -base_radius),
    ):
        if abs(elevation_at(imported, x + dx, y + dy) - z) > tolerance:
            return None
    dx = (elevation_at(imported, x + 1.0, y) - elevation_at(imported, x - 1.0, y)) / 2.0
    dy = (elevation_at(imported, x, y + 1.0) - elevation_at(imported, x, y - 1.0)) / 2.0
    if math.hypot(dx, dy) > 1.0:
        return None
    return s.KinematicState(
        (x, y, z),
        (0.0, 0.0, 0.0),
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        (0.0, 0.0, 0.0),
    )


def _tree_specs(
    tree_id: str,
    display_name: str,
    pose: s.KinematicState,
    height: float,
    crown_radius: float,
    canopy_color: tuple[float, float, float],
) -> tuple[s.VegetationSpec, ...]:
    """One semantic tree composed of the existing trunk and canopy visuals."""
    x, y, z = pose.position_enu_m
    trunk_height = height * 0.4
    trunk_radius = min(0.45, max(0.12, crown_radius * 0.1))
    canopy_height = height * 0.7
    specs: list[s.VegetationSpec] = []
    for part, offset, part_height, part_radius, color in (
        (
            s.TreePart.TRUNK,
            0.0,
            trunk_height,
            trunk_radius,
            (0.22, 0.12, 0.05),
        ),
        (
            s.TreePart.CANOPY,
            height * 0.3,
            canopy_height,
            crown_radius,
            canopy_color,
        ),
    ):
        specs.append(
            s.VegetationSpec(
                s.VegetationId(f"{tree_id}/{part.value}"),
                f"{display_name} {part.value}",
                replace(pose, position_enu_m=(x, y, z + offset)),
                s.CylinderGeometry(part_radius, part_height),
                s.SurfaceAppearance(color, 0.9),
                s.VegetationMotionModel.STATIC,
                True,
                s.TreeVisualSpec(part, part_height, part_radius),
            )
        )
    return tuple(specs)


def _trees_inside(mask: BaseGeometry, points: tuple[s.OSMPointFeature, ...]) -> int:
    return sum(1 for point in points if mask.covers(Point(*point.position_enu_m)))


def tree_objects(
    imported: s.OSMImport, vegetation: s.OSMVegetationConfig | None = None
) -> tuple[s.VegetationSpec, ...]:
    """Deterministic trees, shrubs and grass patches on OSM forest/grass areas.

    OSM tree points and the legacy ``forest_points`` stand remain canonical.
    Procedural additions are bounded by per-area/total budgets and use only
    semantic visuals (two specs per tree, one per shrub, one per grass patch).
    """
    from chongming.scenarios.osm import validate_vegetation_config

    config = vegetation if vegetation is not None else s.OSMVegetationConfig()
    validate_vegetation_config(config)

    objects: list[s.VegetationSpec] = []
    base_trees = forest_points(imported)
    canonical_trees = (*imported.points, *base_trees)
    for point in canonical_trees:
        x, y = point.position_enu_m
        pose = _upright_pose(imported, x, y, 0.6)
        if pose is None:
            from chongming.scenarios.osm_elevation import elevation_at

            pose = s.KinematicState(
                (x, y, elevation_at(imported, x, y)),
                (0.0, 0.0, 0.0),
                ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
                (0.0, 0.0, 0.0),
            )
        objects.extend(
            _tree_specs(
                f"osm/tree/{point.osm_node_id}",
                f"OSM tree {point.osm_node_id}",
                pose,
                point.height_m,
                point.radius_m,
                (0.10, 0.28, 0.06),
            )
        )

    tree_index = _PlacementIndex()
    for point in canonical_trees:
        tree_index.add(*point.position_enu_m)
    shrub_index = _PlacementIndex()

    tree_excluded = _hard_surface_exclusions(
        imported, building_margin=0.8, road_margin=1.0, water_margin=1.0
    )
    tree_excluded = _with_point_exclusions(tree_excluded, canonical_trees, margin=1.0)
    shrub_excluded = _hard_surface_exclusions(
        imported, building_margin=0.6, road_margin=0.6, water_margin=0.8
    )
    grass_excluded = _hard_surface_exclusions(
        imported, building_margin=1.0, road_margin=1.5, water_margin=1.5
    )

    generated_trees = 0
    generated_shrubs = 0
    generated_grass = 0

    def add_tree(area: s.OSMArea, candidate: _PlantCandidate, remaining: int) -> bool:
        nonlocal generated_trees
        if remaining <= 0 or generated_trees >= config.maximum_trees_total:
            return False
        if tree_index.conflicts(candidate.x, candidate.y, 2.0):
            return False
        if tree_excluded is not None and tree_excluded.covers(Point(candidate.x, candidate.y)):
            return False
        pose = _upright_pose(imported, candidate.x, candidate.y, 0.6)
        if pose is None:
            return False
        digest = hashlib.sha256(
            f"osm-vegetation/{config.seed}/{area.element_id}/tree/{candidate.pass_index}/{candidate.ix}/{candidate.iy}".encode()
        ).digest()
        height = config.tree_height_m * (
            1.0 + config.tree_height_jitter * (2.0 * _unit(digest, 0) - 1.0)
        )
        crown = config.tree_crown_radius_m * (
            1.0 + config.tree_crown_radius_jitter * (2.0 * _unit(digest, 4) - 1.0)
        )
        tree_index.add(candidate.x, candidate.y)
        canopy_color = (0.09, 0.25, 0.05) if area.category == "forest" else (0.11, 0.29, 0.07)
        objects.extend(
            _tree_specs(
                f"osm/tree/{area.element_id}/{candidate.pass_index}/{candidate.ix}/{candidate.iy}",
                f"OSM {area.category} tree {area.element_id}",
                pose,
                max(1.0, height),
                max(0.2, crown),
                canopy_color,
            )
        )
        generated_trees += 1
        return True

    def add_shrub(area: s.OSMArea, candidate: _PlantCandidate, remaining: int) -> bool:
        nonlocal generated_shrubs
        if remaining <= 0 or generated_shrubs >= config.maximum_shrubs_total:
            return False
        if shrub_index.conflicts(candidate.x, candidate.y, 1.0):
            return False
        if tree_index.conflicts(candidate.x, candidate.y, 0.9):
            return False
        if shrub_excluded is not None and shrub_excluded.covers(Point(candidate.x, candidate.y)):
            return False
        pose = _upright_pose(imported, candidate.x, candidate.y, 0.8)
        if pose is None:
            return False
        digest = hashlib.sha256(
            f"osm-vegetation/{config.seed}/{area.element_id}/shrub/{candidate.pass_index}/{candidate.ix}/{candidate.iy}".encode()
        ).digest()
        height = config.shrub_height_m * (
            1.0 + config.shrub_height_jitter * (2.0 * _unit(digest, 0) - 1.0)
        )
        radius = config.shrub_radius_m * (
            1.0 + config.shrub_radius_jitter * (2.0 * _unit(digest, 4) - 1.0)
        )
        height, radius = max(0.1, height), max(0.05, radius)
        shrub_index.add(candidate.x, candidate.y)
        color = (0.08, 0.22, 0.05) if area.category == "forest" else (0.11, 0.27, 0.065)
        objects.append(
            s.VegetationSpec(
                s.VegetationId(
                    f"osm/shrub/{area.element_id}/{candidate.pass_index}/{candidate.ix}/{candidate.iy}"
                ),
                f"OSM {area.category} shrub {area.element_id}",
                replace(
                    pose, position_enu_m=(candidate.x, candidate.y, pose.position_enu_m[2] + 0.02)
                ),
                s.CylinderGeometry(radius, height),
                s.SurfaceAppearance(color, 0.85),
                s.VegetationMotionModel.STATIC,
                True,
                s.ShrubVisualSpec(height, radius),
            )
        )
        generated_shrubs += 1
        return True

    def add_grass(area: s.OSMArea, candidate: _PlantCandidate, remaining: int) -> bool:
        nonlocal generated_grass
        if remaining <= 0 or generated_grass >= config.maximum_grass_patches_total:
            return False
        digest = hashlib.sha256(
            f"osm-vegetation/{config.seed}/{area.element_id}/grass/{candidate.pass_index}/{candidate.ix}/{candidate.iy}".encode()
        ).digest()
        width = config.grass_patch_size_m * (
            1.0 + config.grass_patch_size_jitter * (2.0 * _unit(digest, 0) - 1.0)
        )
        depth = config.grass_patch_size_m * (
            1.0 + config.grass_patch_size_jitter * (2.0 * _unit(digest, 4) - 1.0)
        )
        width, depth = max(0.5, width), max(0.5, depth)
        blade_height = config.grass_blade_height_m * (
            1.0 + config.grass_blade_height_jitter * (2.0 * _unit(digest, 8) - 1.0)
        )
        blade_height = max(0.02, blade_height)
        grass_pose = _grass_pose(imported, candidate.x, candidate.y, max(width, depth))
        if grass_pose is None:
            return False
        rotation = grass_pose.rotation_enu_from_body
        footprint = Polygon(
            [
                (
                    candidate.x + rotation[0][0] * a + rotation[0][1] * b,
                    candidate.y + rotation[1][0] * a + rotation[1][1] * b,
                )
                for a, b in (
                    (-width / 2, -depth / 2),
                    (width / 2, -depth / 2),
                    (width / 2, depth / 2),
                    (-width / 2, depth / 2),
                )
            ]
        )
        if not mask.covers(footprint):
            return False
        if grass_excluded is not None and grass_excluded.intersects(footprint):
            return False
        objects.append(
            s.VegetationSpec(
                s.VegetationId(
                    f"osm/grass/{area.element_id}/{candidate.pass_index}/{candidate.ix}/{candidate.iy}"
                ),
                f"OSM {area.category} grass {area.element_id}",
                grass_pose,
                s.CylinderGeometry(math.hypot(width, depth) * 0.5, blade_height),
                s.SurfaceAppearance(
                    (0.12, 0.28, 0.06) if area.category == "forest" else (0.16, 0.32, 0.075),
                    0.82,
                ),
                s.VegetationMotionModel.STATIC,
                False,
                s.GrassVisualSpec(width, depth, blade_height, config.grass_density_per_m2),
            )
        )
        generated_grass += 1
        return True

    areas_by_category = {
        "forest": [area for area in imported.areas if area.category == "forest"],
        "grass": [area for area in imported.areas if area.category == "grass"],
    }
    masks = {
        area.element_id: Polygon(area.rings_enu_m[0], area.rings_enu_m[1:])
        for area in imported.areas
        if area.category in areas_by_category
    }

    # Park trees first so a low total budget can never leave parks treeless.
    for category in ("grass", "forest"):
        for area in areas_by_category[category]:
            mask = masks[area.element_id]
            factor = _area_factor(config.seed, area.element_id)
            if category == "grass":
                density = config.grass_tree_density_per_ha
                clustering = config.grass_clustering
                existing = _trees_inside(mask, imported.points)
            else:
                density = config.forest_tree_density_per_ha
                clustering = config.forest_clustering
                existing = _trees_inside(mask, canonical_trees)
            target = _density_target(mask, density, factor, config.maximum_trees_per_area)
            remaining = max(0, target - existing)
            if not remaining:
                continue
            candidates = _plant_candidates(
                mask,
                density,
                seed=config.seed,
                token=f"{area.element_id}/tree",
                clustering=clustering,
            )
            for candidate in candidates:
                if add_tree(area, candidate, remaining):
                    remaining -= 1
                    if not remaining:
                        break

    for category in ("forest", "grass"):
        density = (
            config.forest_shrub_density_per_ha
            if category == "forest"
            else config.grass_shrub_density_per_ha
        )
        for area in areas_by_category[category]:
            mask = masks[area.element_id]
            target = _density_target(
                mask,
                density,
                _area_factor(config.seed, area.element_id),
                config.maximum_shrubs_per_area,
            )
            if not target:
                continue
            candidates = _plant_candidates(
                mask,
                density,
                seed=config.seed,
                token=f"{area.element_id}/shrub",
                clustering=0.6,
            )
            remaining = target
            for candidate in candidates:
                if add_shrub(area, candidate, remaining):
                    remaining -= 1
                    if not remaining:
                        break

    for category in ("forest", "grass"):
        density = (
            config.forest_grass_density_per_ha
            if category == "forest"
            else config.grass_grass_density_per_ha
        )
        for area in areas_by_category[category]:
            mask = masks[area.element_id]
            target = _density_target(
                mask,
                density,
                _area_factor(config.seed, area.element_id),
                config.maximum_grass_patches_per_area,
            )
            if not target:
                continue
            remaining = min(target, config.maximum_grass_patches_total - generated_grass)
            # Terrain creases, narrow polygons and hard surfaces reject many
            # 12 m patches. Additional deterministic grid passes fill the target
            # without forcing a larger per-pass candidate list.
            for attempt in range(12):
                if remaining <= 0:
                    break
                candidates = _plant_candidates(
                    mask,
                    density,
                    seed=config.seed,
                    token=f"{area.element_id}/grass",
                    clustering=0.55,
                    pass_index=attempt,
                )
                for candidate in candidates:
                    if add_grass(area, candidate, remaining):
                        remaining -= 1
                        if not remaining:
                            break

    return tuple(objects)


def _grass_pose(imported: s.OSMImport, x: float, y: float, width: float) -> s.KinematicState | None:
    """Fit one tiny grass patch to the local DEM plane; skip terrain creases.

    The coarse DEM is piecewise planar. Reject a patch crossing a sharp edge
    instead of putting a flat bed of blades above/below the ground. These are
    visual samples only; the authoritative ground remains the terrain mesh.
    """
    from chongming.scenarios.osm_elevation import elevation_at

    z = elevation_at(imported, x, y)
    dx = (elevation_at(imported, x + 0.1, y) - elevation_at(imported, x - 0.1, y)) / 0.2
    dy = (elevation_at(imported, x, y + 0.1) - elevation_at(imported, x, y - 0.1)) / 0.2
    nx, nz = math.sqrt(1 + dx * dx), math.sqrt(1 + dx * dx + dy * dy)
    axis_x = (1 / nx, 0.0, dx / nx)
    normal = (-dx / nz, -dy / nz, 1 / nz)
    axis_y = (
        normal[1] * axis_x[2],
        normal[2] * axis_x[0] - normal[0] * axis_x[2],
        -normal[1] * axis_x[0],
    )
    rotation: tuple[
        tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]
    ] = (
        (axis_x[0], axis_y[0], normal[0]),
        (axis_x[1], axis_y[1], normal[1]),
        (axis_x[2], axis_y[2], normal[2]),
    )
    for a in (-width / 2, 0.0, width / 2):
        for b in (-width / 2, 0.0, width / 2):
            px = x + axis_x[0] * a + axis_y[0] * b
            py = y + axis_x[1] * a + axis_y[1] * b
            pz = z + axis_x[2] * a + axis_y[2] * b
            if abs(pz - elevation_at(imported, px, py)) > 0.05:
                return None
    return s.KinematicState((x, y, z + 0.05), (0.0, 0.0, 0.0), rotation, (0.0, 0.0, 0.0))


def validate_features(imported: s.OSMImport) -> None:
    from chongming.scenarios.osm import canonical_ring

    for road in imported.roads:
        if road.osm_way_id <= 0 or any(
            not math.isfinite(v) for p in road.centerline_enu_m for v in p
        ):
            raise ValueError("invalid OSM road coordinates/ID")
        if len(set(road.centerline_enu_m)) < 2:
            raise ValueError("degenerate OSM road")
    for point in imported.points:
        if point.osm_node_id <= 0 or any(
            not math.isfinite(v) for v in (*point.position_enu_m, point.height_m, point.radius_m)
        ):
            raise ValueError("invalid OSM tree coordinates/ID")
    for area in imported.areas:
        if any(canonical_ring(ring) != ring for ring in area.rings_enu_m):
            raise ValueError("noncanonical OSM area rings")
        polygon = Polygon(area.rings_enu_m[0], area.rings_enu_m[1:])
        if not polygon.is_valid or polygon.area <= 0:
            raise ValueError("invalid OSM area polygon")
