"""Compact, semantic scenario authoring format.

The authoring document is intentionally smaller than the runtime ScenarioSpec.
It describes scene intent (stations, targets and motion), while the compiler
derives optical matrices, reference landmarks and runtime-only configuration.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from chongming.contracts import model as m
from chongming.contracts import scene as s
from chongming.contracts.scene import BehaviorId
from chongming.scenarios.assets import read_verified_asset, resolve_external_asset
from chongming.scenarios.demo import (
    calibration_panel,
    demo_spec,
    hold,
    optical_rotation,
    orbit_path,
    state,
)

AUTHORING_FORMAT = "scenario-authoring-v2"
STANDARD_MAIN_RESOLUTION = (1920, 1080)
STANDARD_AUXILIARY_RESOLUTION = (640, 480)
STANDARD_MAIN_FOV_DEG = 60.0
STANDARD_AUXILIARY_FOV_DEG = 2.6  # horizontal, main is diagonal


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


def _fields(value: Any, allowed: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown {name} fields: {sorted(unknown)}")
    return value


def _integer(value: Any, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return int(value)


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _geometry(value: dict[str, Any]) -> s.GeometrySpec:
    kind = _required(value, "type")
    _fields(
        value,
        {"type"}
        | {"sphere": {"radius_m"}, "box": {"half_extents_m"}, "aircraft": set()}.get(kind, set()),
        "geometry",
    )
    if kind == "sphere":
        return s.SphereGeometry(_number(_required(value, "radius_m"), "radius_m"))
    if kind == "box":
        extents = _required(value, "half_extents_m")
        if not isinstance(extents, list) or len(extents) != 3:
            raise ValueError("half_extents_m must contain three numbers")
        return s.BoxGeometry(tuple(_number(x, "half_extents_m") for x in extents))  # type: ignore[arg-type]
    if kind == "aircraft":
        return s.TriangleMeshGeometry(
            (
                (0.0, 2.8, 0.0),
                (-0.35, -1.7, -0.25),
                (0.35, -1.7, -0.25),
                (0.0, -1.7, 0.4),
                (-3.0, -0.6, 0.0),
                (3.0, -0.6, 0.0),
                (-1.2, -1.8, 0.0),
                (1.2, -1.8, 0.0),
            ),
            (
                (0, 1, 2),
                (0, 3, 1),
                (0, 2, 3),
                (1, 3, 2),
                (0, 4, 1),
                (0, 2, 5),
                (1, 6, 3),
                (2, 3, 7),
            ),
        )
    raise ValueError(f"unsupported authoring geometry type: {kind!r}")


def _terrain(value: Any, base: s.TerrainSpec, asset_root: Path | None) -> s.TerrainSpec:
    if value is None:
        return base
    if not isinstance(value, dict):
        raise TypeError("terrain must be an object")
    kind = value.get("type", "flat")
    _fields(
        value,
        {"type"} if kind == "flat" else {"type", "origin_enu_m", "spacing_m", "heights_m", "asset"},
        "terrain",
    )
    if kind == "flat":
        return base
    if kind != "heightfield":
        raise ValueError(f"unsupported terrain type: {kind!r}")
    origin = value.get("origin_enu_m", [0.0, 0.0])
    spacing = _number(value.get("spacing_m", 10.0), "terrain.spacing_m")
    if spacing <= 0:
        raise ValueError("terrain.spacing_m must be positive")
    if ("heights_m" in value) == ("asset" in value):
        raise ValueError("heightfield requires exactly one of heights_m or asset")
    asset_uri, asset_digest = None, None
    if "asset" in value:
        asset = _fields(value["asset"], {"uri", "sha256"}, "terrain.asset")
        asset_uri = _identifier(_required(asset, "uri"), "terrain.asset.uri")
        asset_digest = _identifier(_required(asset, "sha256"), "terrain.asset.sha256")
        if asset_root is None:
            raise ValueError("compiling external terrain requires asset_root")
        content = read_verified_asset(resolve_external_asset(asset_uri, asset_root), asset_digest)
        try:
            grid = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError(f"invalid heightfield asset JSON: {error}") from error
        grid = _fields(grid, {"heights_m"}, "heightfield asset")
        heights = _required(grid, "heights_m")
    else:
        heights = value["heights_m"]
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
            triangles.extend(
                (
                    (index, index + 1, index + columns + 1),
                    (index, index + columns + 1, index + columns),
                )
            )
    geometry = s.TriangleMeshGeometry(vertices, tuple(triangles))
    return replace(
        base,
        model=s.TerrainModel.HEIGHTFIELD,
        maximum_relief_m=max(vertex[2] for vertex in vertices),
        heightfield_geometry=geometry,
        heightfield_origin_enu_m=(east, north),
        heightfield_asset_uri=asset_uri,
        heightfield_asset_digest=asset_digest,
    )


def _appearance(value: dict[str, Any] | None) -> s.SurfaceAppearance:
    if value is None:
        return s.SurfaceAppearance((0.16, 0.18, 0.21), 0.9)
    _fields(value, {"linear_rgb", "diffuse_reflectance"}, "appearance")
    rgb = _required(value, "linear_rgb")
    if not isinstance(rgb, list) or len(rgb) != 3:
        raise ValueError("linear_rgb must contain three numbers")
    return s.SurfaceAppearance(
        tuple(_number(x, "linear_rgb") for x in rgb),  # type: ignore[arg-type]
        _number(value.get("diffuse_reflectance", 0.9), "diffuse_reflectance"),
    )


def _target(spec: s.FlyingObjectSpec, document: dict[str, Any]) -> s.FlyingObjectSpec:
    _fields(document, {"id", "display_name", "geometry", "appearance", "motion"}, "target")
    target_id = _identifier(_required(document, "id"), "target.id")
    geometry_value = document.get("geometry")
    geometry = spec.geometry if geometry_value is None else _geometry(geometry_value)
    appearance = _appearance(document.get("appearance"))
    motion = _required(document, "motion")
    if not isinstance(motion, dict):
        raise TypeError("motion must be an object")
    motion_type = _required(motion, "type")
    _fields(
        motion,
        {"type"}
        | {
            "orbit": {"center_enu_m", "radius_m", "period_s", "phase_rad", "vertical_amplitude_m"},
            "constant_velocity": {"position_enu_m", "velocity_enu_mps"},
        }.get(motion_type, set()),
        "motion",
    )
    behavior: s.FlightBehaviorSpec | None = None
    if motion_type == "orbit":
        trajectory = orbit_path(
            _vec3(_required(motion, "center_enu_m"), "center_enu_m"),
            _number(_required(motion, "radius_m"), "radius_m"),
            _number(_required(motion, "period_s"), "period_s"),
            _number(motion.get("phase_rad", 0.0), "phase_rad"),
            _number(motion.get("vertical_amplitude_m", 2.0), "vertical_amplitude_m"),
        )
        center = _vec3(motion["center_enu_m"], "center_enu_m")
        radius = _number(motion["radius_m"], "radius_m")
        behavior = s.FlightBehaviorSpec(
            BehaviorId("orbit"),
            "1",
            (
                ("center_e", center[0]),
                ("center_n", center[1]),
                ("center_u", center[2]),
                ("radius_e", radius),
                ("radius_n", radius),
                ("period_s", _number(motion["period_s"], "period_s")),
                ("phase_rad", _number(motion.get("phase_rad", 0), "phase_rad")),
                (
                    "vertical_amplitude_m",
                    _number(motion.get("vertical_amplitude_m", 2), "vertical_amplitude_m"),
                ),
            ),
            f"flight/{target_id}",
        )
        trajectory = replace(
            trajectory, interpolation=s.TrajectoryInterpolation.HOLD, knots=trajectory.knots[:1]
        )
    elif motion_type == "constant_velocity":
        position = _vec3(_required(motion, "position_enu_m"), "position_enu_m")
        velocity = _vec3(_required(motion, "velocity_enu_mps"), "velocity_enu_mps")
        trajectory = s.TrajectorySpec(
            s.TrajectoryInterpolation.HOLD,
            (
                s.TrajectoryKnot(
                    0,
                    s.KinematicState(
                        position,
                        velocity,
                        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
                        (0.0, 0.0, 0.0),
                    ),
                ),
            ),
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


def compile_authoring(
    document: dict[str, Any], *, asset_root: Path | None = None
) -> s.ScenarioSpec:
    """Compile a semantic authoring document into the strict runtime model."""
    if set(document) - {
        "format",
        "scenario_id",
        "random_seed",
        "duration_s",
        "fps",
        "stations",
        "targets",
        "terrain",
        "world_objects",
        "search",
    }:
        unknown = set(document) - {
            "format",
            "scenario_id",
            "random_seed",
            "duration_s",
            "fps",
            "stations",
            "targets",
            "terrain",
            "world_objects",
            "search",
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
        _fields(
            station,
            {
                "id",
                "position_enu_m",
                "main_yaw_deg",
                "main_elevation_deg",
                "auxiliary_yaw_deg",
                "fps",
                "start_s",
            },
            "station",
        )
        ids.append(_identifier(_required(station, "id"), "station.id"))
        positions.append(_vec3(_required(station, "position_enu_m"), "position_enu_m"))
    if len(set(ids)) != len(ids):
        raise ValueError("station IDs must be unique")
    fps = _integer(document.get("fps", 30), "fps")
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
    cameras = []
    configs = []
    panels: list[s.WorldObjectSpec] = []
    landmarks: list[s.ReferenceLandmarkSpec] = []
    for station, config in zip(stations, spec.station_configs, strict=True):
        rate = _integer(station.get("fps", fps), "station.fps")
        if not 1 <= rate <= 60:
            raise ValueError("station.fps must be between 1 and 60")
        start_s = _number(station.get("start_s", 0), "station.start_s")
        if start_s < 0:
            raise ValueError("station.start_s cannot be negative")
        rotations = {}
        for camera in spec.scene_definition.cameras:
            if camera.station_id != config.station_id:
                continue
            pose = camera.optical_pose_trajectory.knots[0].state
            if camera.camera_role is m.CameraRole.MAIN:
                yaw = math.degrees(
                    math.atan2(pose.rotation_enu_from_body[0][2], pose.rotation_enu_from_body[1][2])
                )
                rotation = optical_rotation(
                    math.radians(_number(station.get("main_yaw_deg", yaw), "main_yaw_deg")),
                    math.radians(
                        _number(station.get("main_elevation_deg", 9), "main_elevation_deg")
                    ),
                )
            else:
                yaw = math.degrees(
                    math.atan2(pose.rotation_enu_from_body[0][2], pose.rotation_enu_from_body[1][2])
                )
                rotation = optical_rotation(
                    math.radians(
                        _number(station.get("auxiliary_yaw_deg", yaw), "auxiliary_yaw_deg")
                    ),
                    0,
                )
            rotations[camera.camera_role] = rotation
            cameras.append(
                replace(
                    camera,
                    optical_pose_trajectory=hold(replace(pose, rotation_enu_from_body=rotation)),
                    metadata=replace(camera.metadata, nominal_frame_rate_hz=float(rate)),
                    exposure=replace(
                        camera.exposure,
                        frame_period_ns=1_000_000_000 // rate,
                        first_exposure_start_ns=round(start_s * 1e9),
                    ),
                )
            )
        main, aux = rotations[m.CameraRole.MAIN], rotations[m.CameraRole.AUXILIARY]
        panel_objects, panel_landmarks = calibration_panel(
            config.station_id,
            next(
                c
                for c in cameras
                if c.station_id == config.station_id and c.camera_role is m.CameraRole.AUXILIARY
            )
            .optical_pose_trajectory.knots[0]
            .state,
        )
        panels.extend(panel_objects)
        landmarks.extend(panel_landmarks)
        relative = tuple(
            tuple(sum(aux[k][i] * main[k][j] for k in range(3)) for j in range(3)) for i in range(3)
        )
        assert isinstance(config.reference_anchor, m.SurveyedReference)
        compiled_config = replace(
            config,
            reference_anchor=replace(
                config.reference_anchor,
                landmarks=tuple(
                    replace(
                        old, landmark_id=str(new.landmark_id), position_enu_m=new.position_enu_m
                    )
                    for old, new in zip(
                        config.reference_anchor.landmarks, panel_landmarks, strict=True
                    )
                ),
            ),
            baseline_orientation_enu_from_main=main,
            fixed_rotation_aux_from_main=relative,  # type: ignore[arg-type]
            full_rigid_transform_aux_from_main=m.RigidTransform(
                rotation_a_from_b=relative,  # type: ignore[arg-type]
                translation_a_m=(-0.2 * aux[0][0], -0.2 * aux[0][1], -0.2 * aux[0][2]),
            ),
        )
        digest = m.ConfigDigest(
            hashlib.sha256(
                json.dumps(asdict(compiled_config), sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
        )
        configs.append(
            replace(
                compiled_config,
                config_digest=digest,
                installation_uncertainty=replace(
                    compiled_config.installation_uncertainty,
                    state_ref=replace(
                        compiled_config.installation_uncertainty.state_ref, config_digest=digest
                    ),
                ),
            )
        )
    spec = replace(
        spec,
        station_configs=tuple(configs),
        scene_definition=replace(
            spec.scene_definition,
            cameras=tuple(cameras),
            world_objects=tuple(
                o
                for o in spec.scene_definition.world_objects
                if o.kind is not s.WorldObjectKind.FIXED_REFERENCE
            )
            + tuple(panels),
            reference_landmarks=tuple(landmarks),
        ),
    )
    if "world_objects" in document:
        objects = document["world_objects"]
        if not isinstance(objects, list):
            raise ValueError("world_objects must be an array")
        compiled_objects = []
        for item in objects:
            item = _fields(
                item,
                {
                    "id",
                    "display_name",
                    "position_enu_m",
                    "geometry",
                    "appearance",
                    "occludes_sight_lines",
                },
                "world_object",
            )
            object_id = _identifier(_required(item, "id"), "world_object.id")
            occludes = item.get("occludes_sight_lines", True)
            if type(occludes) is not bool:
                raise ValueError("occludes_sight_lines must be boolean")
            compiled_objects.append(
                s.WorldObjectSpec(
                    s.WorldObjectId(object_id),
                    str(item.get("display_name", object_id)),
                    s.WorldObjectKind.OBSTACLE,
                    "authored",
                    state(_vec3(_required(item, "position_enu_m"), "position_enu_m")),
                    _geometry(_required(item, "geometry")),
                    _appearance(item.get("appearance")),
                    occludes,
                )
            )
        spec = replace(
            spec,
            scene_definition=replace(
                spec.scene_definition, world_objects=tuple(compiled_objects) + tuple(panels)
            ),
        )
    if "search" in document:
        search = _fields(document["search"], {"min_enu_m", "max_enu_m", "step_enu_m"}, "search")
        spec = replace(
            spec,
            voxel_search=replace(
                spec.voxel_search,
                min_enu_m=_vec3(_required(search, "min_enu_m"), "search.min_enu_m"),
                max_enu_m=_vec3(_required(search, "max_enu_m"), "search.max_enu_m"),
                step_enu_m=_vec3(_required(search, "step_enu_m"), "search.step_enu_m"),
            ),
        )
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
        scenario_id=_identifier(document.get("scenario_id", "scenario"), "scenario_id"),
        config_digest=m.ConfigDigest(
            hashlib.sha256(
                json.dumps(document, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
        ),
        random_seed=_integer(document.get("random_seed", s.SCENE_RANDOM_SEED), "random_seed"),
        timeline=replace(spec.timeline, end_time_ns=round(duration_s * 1e9) - 1),
        scene_definition=replace(
            spec.scene_definition,
            terrain=_terrain(document.get("terrain"), spec.scene_definition.terrain, asset_root),
        ),
    )
    spec = replace(
        spec,
        virtual_observations=replace(
            spec.virtual_observations,
            calibration_intervals=tuple(
                replace(interval, end_time_ns=spec.timeline.end_time_ns + 1)
                for interval in spec.virtual_observations.calibration_intervals
            ),
        ),
    )
    from chongming.scenarios.serialization import validate_scenario_spec

    validate_scenario_spec(spec)
    return spec


def load_authoring(path: Path, *, asset_root: Path | None = None) -> s.ScenarioSpec:
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid authoring JSON: {error}") from error
    if not isinstance(document, dict):
        raise TypeError("authoring JSON root must be an object")
    return compile_authoring(
        document, asset_root=asset_root if asset_root is not None else path.parent
    )


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
        {
            "type": "orbit",
            "center_enu_m": [0, 62, 14],
            "radius_m": 22,
            "period_s": 24,
            "phase_rad": -0.8,
        },
        {
            "type": "orbit",
            "center_enu_m": [0, 62, 22],
            "radius_m": 22,
            "period_s": 24,
            "phase_rad": 1.2,
        },
        {
            "type": "orbit",
            "center_enu_m": [0, 62, 29],
            "radius_m": 22,
            "period_s": 24,
            "phase_rad": 2.7,
        },
    )
    return {
        "format": AUTHORING_FORMAT,
        "scenario_id": "video-demo",
        "random_seed": s.SCENE_RANDOM_SEED,
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
