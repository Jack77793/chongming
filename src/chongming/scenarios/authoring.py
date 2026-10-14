"""Compact, semantic scenario authoring format.

The authoring document is intentionally smaller than the runtime ScenarioSpec.
It describes scene intent (stations, targets and motion), while the compiler
derives optical matrices, reference landmarks and runtime-only configuration.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

from chongming.contracts import model as m
from chongming.contracts import scene as s
from chongming.contracts.scene import BehaviorId
from chongming.scenarios.demo import demo_spec, orbit_path

AUTHORING_FORMAT = "scenario-authoring-v2"
STANDARD_MAIN_RESOLUTION = (1920, 1080)
STANDARD_AUXILIARY_RESOLUTION = (640, 480)
STANDARD_MAIN_FOV_DEG = 60.0
STANDARD_AUXILIARY_FOV_DEG = 20.0


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _vec3(value: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{name} must contain three numbers")
    return tuple(_number(item, name) for item in value)  # type: ignore[return-value]


def _required(mapping: dict[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ValueError(f"authoring field {key!r} is required")
    return mapping[key]


def _geometry(value: dict[str, Any]) -> s.GeometrySpec:
    kind = _required(value, "type")
    if kind == "sphere":
        return s.SphereGeometry(_number(_required(value, "radius_m"), "radius_m"))
    if kind == "box":
        extents = _required(value, "half_extents_m")
        if not isinstance(extents, list) or len(extents) != 3:
            raise ValueError("half_extents_m must contain three numbers")
        return s.BoxGeometry(tuple(_number(x, "half_extents_m") for x in extents))  # type: ignore[arg-type]
    if kind == "aircraft":
        return s.TriangleMeshGeometry(
            ((0.0, 2.8, 0.0), (-0.35, -1.7, -0.25), (0.35, -1.7, -0.25), (0.0, -1.7, 0.4),
             (-3.0, -0.6, 0.0), (3.0, -0.6, 0.0), (-1.2, -1.8, 0.0), (1.2, -1.8, 0.0)),
            ((0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2), (0, 4, 1), (0, 2, 5), (1, 6, 3), (2, 3, 7)),
        )
    raise ValueError(f"unsupported authoring geometry type: {kind!r}")


def _terrain(value: Any, base: s.TerrainSpec) -> s.TerrainSpec:
    if value is None:
        return base
    if not isinstance(value, dict):
        raise TypeError("terrain must be an object")
    kind = value.get("type", "flat")
    if kind == "flat":
        return base
    if kind != "heightfield":
        raise ValueError(f"unsupported terrain type: {kind!r}")
    origin = value.get("origin_enu_m", [0.0, 0.0])
    spacing = _number(value.get("spacing_m", 10.0), "terrain.spacing_m")
    if spacing <= 0:
        raise ValueError("terrain.spacing_m must be positive")
    heights = value.get("heights_m")
    if not isinstance(origin, list) or len(origin) != 2 or not isinstance(heights, list):
        raise ValueError("heightfield requires origin_enu_m and heights_m")
    if any(not isinstance(row, list) for row in heights):
        raise ValueError("heightfield rows must be arrays")
    rows = heights
    if len(rows) < 2 or len(rows[0]) < 2 or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("heightfield requires a rectangular grid of at least 2x2")
    east, north = (_number(item, "terrain.origin_enu_m") for item in origin)
    # Mesh coordinates are local; visibility and rendering apply the terrain pose.
    vertices = tuple(
        (col * spacing, row * spacing, _number(height, "terrain.height"))
        for row, values in enumerate(rows)
        for col, height in enumerate(values)
    )
    if any(vertex[2] < 0 for vertex in vertices):
        raise ValueError("heightfield elevations cannot be below base elevation")
    columns = len(rows[0])
    triangles: list[tuple[int, int, int]] = []
    for row in range(len(rows) - 1):
        for col in range(columns - 1):
            index = row * columns + col
            triangles.extend(((index, index + 1, index + columns + 1), (index, index + columns + 1, index + columns)))
    geometry = s.TriangleMeshGeometry(vertices, tuple(triangles))
    return replace(
        base,
        model=s.TerrainModel.HEIGHTFIELD,
        maximum_relief_m=max(vertex[2] for vertex in vertices),
        heightfield_geometry=geometry,
        heightfield_origin_enu_m=(east, north),
    )


def _appearance(value: dict[str, Any] | None) -> s.SurfaceAppearance:
    if value is None:
        return s.SurfaceAppearance((0.16, 0.18, 0.21), 0.9)
    rgb = _required(value, "linear_rgb")
    if not isinstance(rgb, list) or len(rgb) != 3:
        raise ValueError("linear_rgb must contain three numbers")
    return s.SurfaceAppearance(
        tuple(_number(x, "linear_rgb") for x in rgb),  # type: ignore[arg-type]
        _number(value.get("diffuse_reflectance", 0.9), "diffuse_reflectance"),
    )


def _target(spec: s.FlyingObjectSpec, document: dict[str, Any]) -> s.FlyingObjectSpec:
    target_id = str(_required(document, "id"))
    geometry_value = document.get("geometry")
    geometry = spec.geometry if geometry_value is None else _geometry(geometry_value)
    appearance = _appearance(document.get("appearance"))
    motion = _required(document, "motion")
    if not isinstance(motion, dict):
        raise TypeError("motion must be an object")
    motion_type = _required(motion, "type")
    behavior: s.FlightBehaviorSpec | None = None
    if motion_type == "orbit":
        trajectory = orbit_path(
            _vec3(_required(motion, "center_enu_m"), "center_enu_m"),
            _number(_required(motion, "radius_m"), "radius_m"),
            _number(_required(motion, "period_s"), "period_s"),
            _number(motion.get("phase_rad", 0.0), "phase_rad"),
            _number(motion.get("vertical_amplitude_m", 2.0), "vertical_amplitude_m"),
        )
    elif motion_type == "constant_velocity":
        position = _vec3(_required(motion, "position_enu_m"), "position_enu_m")
        velocity = _vec3(_required(motion, "velocity_enu_mps"), "velocity_enu_mps")
        trajectory = s.TrajectorySpec(
            s.TrajectoryInterpolation.HOLD,
            (s.TrajectoryKnot(0, s.KinematicState(position, velocity, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), (0.0, 0.0, 0.0))),),
        )
        behavior = s.FlightBehaviorSpec(
            behavior_id=BehaviorId("constant-velocity"),
            implementation_version="1",
            parameters=(("vx", velocity[0]), ("vy", velocity[1]), ("vz", velocity[2])),
            random_stream=f"flight/{target_id}",
        )
    else:
        raise ValueError(f"unsupported motion type: {motion_type!r}")
    return replace(
        spec,
        truth_object_id=s.TruthObjectId(target_id),
        display_name=str(document.get("display_name", target_id)),
        trajectory=trajectory,
        behavior=behavior,
        geometry=geometry,
        appearance=appearance,
    )


def compile_authoring(document: dict[str, Any]) -> s.ScenarioSpec:
    """Compile a semantic authoring document into the strict runtime model."""
    if set(document) - {
        "format", "scenario_id", "random_seed", "duration_s", "fps", "stations", "targets", "terrain"
    }:
        unknown = set(document) - {
            "format", "scenario_id", "random_seed", "duration_s", "fps", "stations", "targets", "terrain"
        }
        raise ValueError(f"unknown authoring fields: {sorted(unknown)}")
    if document.get("format") != AUTHORING_FORMAT:
        raise ValueError(f"unsupported authoring format: {document.get('format')!r}")
    stations = document.get("stations", [{"id": "station-01", "position_enu_m": [-10, -22, 4]}])
    if not isinstance(stations, list) or not 1 <= len(stations) <= 4:
        raise ValueError("stations must contain between one and four entries")
    positions: list[tuple[float, float, float]] = []
    ids: list[str] = []
    for station in stations:
        if not isinstance(station, dict):
            raise TypeError("station entries must be objects")
        ids.append(str(_required(station, "id")))
        positions.append(_vec3(_required(station, "position_enu_m"), "position_enu_m"))
    if len(set(ids)) != len(ids):
        raise ValueError("station IDs must be unique")
    fps = int(document.get("fps", 30))
    if fps <= 0:
        raise ValueError("fps must be positive")
    spec = demo_spec(
        station_count=len(stations),
        fps=fps,
        station_positions=tuple(positions),
        station_ids=tuple(ids),
    )
    duration_s = _number(document.get("duration_s", 24.0), "duration_s")
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    targets = document.get("targets")
    if targets is not None:
        if not isinstance(targets, list):
            raise ValueError("targets must be an array")
        by_id = {str(item.truth_object_id): item for item in spec.scene_definition.flying_objects}
        compiled: list[s.FlyingObjectSpec] = []
        template = next(iter(by_id.values()))
        for item in targets:
            if not isinstance(item, dict):
                raise TypeError("target entries must be objects")
            target_id = str(_required(item, "id"))
            compiled.append(_target(by_id.get(target_id, template), item))
        spec = replace(
            spec,
            scene_definition=replace(spec.scene_definition, flying_objects=tuple(compiled)),
        )
    spec = replace(
        spec,
        scenario_id=str(document.get("scenario_id", "scenario")),
        config_digest=m.ConfigDigest(f"authoring-v2-{document.get('scenario_id', 'scenario')}"),
        random_seed=int(document.get("random_seed", 42)),
        timeline=replace(spec.timeline, end_time_ns=round(duration_s * 1e9) - 1),
        scene_definition=replace(
            spec.scene_definition,
            terrain=_terrain(document.get("terrain"), spec.scene_definition.terrain),
        ),
    )
    from chongming.scenarios.serialization import validate_scenario_spec

    validate_scenario_spec(spec)
    return spec


def load_authoring(path: Path) -> s.ScenarioSpec:
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid authoring JSON: {error}") from error
    if not isinstance(document, dict):
        raise TypeError("authoring JSON root must be an object")
    return compile_authoring(document)


def save_authoring(document: dict[str, Any], path: Path) -> None:
    if document.get("format") != AUTHORING_FORMAT:
        raise ValueError("document is not scenario-authoring-v2")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def demo_authoring(*, stations: int = 1, fps: int = 30, duration_s: float = 24.0) -> dict[str, Any]:
    """Return the compact review template used by the exporter."""
    names = ("flight-sphere", "flight-box", "flight-mesh")
    geometries = (
        {"type": "sphere", "radius_m": 1.25},
        {"type": "box", "half_extents_m": [1.5, 0.8, 0.6]},
        {"type": "aircraft"},
    )
    motions = (
        {"type": "orbit", "center_enu_m": [0, 62, 14], "radius_m": 22, "period_s": 24, "phase_rad": -0.8},
        {"type": "orbit", "center_enu_m": [0, 62, 22], "radius_m": 22, "period_s": 24, "phase_rad": 1.2},
        {"type": "orbit", "center_enu_m": [0, 62, 29], "radius_m": 22, "period_s": 24, "phase_rad": 2.7},
    )
    return {
        "format": AUTHORING_FORMAT,
        "scenario_id": "video-demo",
        "random_seed": 42,
        "duration_s": duration_s,
        "fps": fps,
        "stations": [
            {"id": f"station-{i + 1:02d}", "position_enu_m": [-10 + 12 * i, -22, 4]}
            for i in range(stations)
        ],
        "targets": [
            {"id": name, "display_name": label, "geometry": geometry, "motion": motion}
            for name, label, geometry, motion in zip(
                names, ("飞行球体", "飞行盒体", "简易飞行器"), geometries, motions, strict=True
            )
        ],
    }
