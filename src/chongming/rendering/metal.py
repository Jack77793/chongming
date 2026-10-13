"""Persistent Apple Metal GPU renderer used by the scene imager.

The browser is deliberately not part of this path.  Python serializes the
immutable logical scene, a long lived Swift process dispatches one Metal
compute grid per frame, and only the packed RGB8 pixels are returned here.
There is no software pixel renderer fallback: a machine without Metal fails
explicitly so performance regressions cannot be hidden.
"""

from __future__ import annotations

import atexit
import json
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from chongming.contracts.scene import (
    BoxGeometry,
    CameraStateTruth,
    GeometrySpec,
    KinematicState,
    LogicalSceneSnapshot,
    SphereGeometry,
    TerrainModel,
    TriangleMeshGeometry,
)

_SOURCE = Path(__file__).with_name("metal_renderer.swift")
_ROOT = Path(__file__).resolve().parents[3]
_BINARY = _ROOT / ".codex-cache" / "chongming-metal-renderer"
_HEADER = struct.Struct("<I")
_RESPONSE_HEADER = struct.Struct("<QQ")


def _rotation(state: KinematicState) -> list[float]:
    return [float(value) for row in state.rotation_enu_from_body for value in row]


def _entity(
    entity_type: int,
    position: tuple[float, float, float],
    state: KinematicState,
    geometry: GeometrySpec,
    appearance: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": entity_type,
        "position": [float(value) for value in position],
        "rotation": _rotation(state),
        "color": [float(value) for value in appearance.linear_rgb],
        "reflectance": float(appearance.diffuse_reflectance),
        "parameters": [],
        "vertices": None,
        "triangles": None,
    }
    if isinstance(geometry, SphereGeometry):
        payload["parameters"] = [float(geometry.radius_m)]
    elif isinstance(geometry, BoxGeometry):
        payload["parameters"] = [float(value) for value in geometry.half_extents_body_m]
    elif isinstance(geometry, TriangleMeshGeometry):
        payload["vertices"] = [
            [float(value) for value in vertex] for vertex in geometry.vertices_body_m
        ]
        payload["triangles"] = [list(triangle) for triangle in geometry.triangle_vertex_indices]
    else:  # pragma: no cover - GeometrySpec is a closed union
        raise TypeError(f"unsupported geometry: {type(geometry).__name__}")
    return payload


def _entities(scene: LogicalSceneSnapshot) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for world_item in scene.world_objects:
        entities.append(
            _entity(
                1
                if isinstance(world_item.geometry, BoxGeometry)
                else 0
                if isinstance(world_item.geometry, SphereGeometry)
                else 2,
                world_item.pose.position_enu_m,
                world_item.pose,
                world_item.geometry,
                world_item.appearance,
            )
        )
    for vegetation_item in scene.vegetation:
        entities.append(
            _entity(
                1
                if isinstance(vegetation_item.geometry, BoxGeometry)
                else 0
                if isinstance(vegetation_item.geometry, SphereGeometry)
                else 2,
                vegetation_item.base_pose.position_enu_m,
                vegetation_item.base_pose,
                vegetation_item.geometry,
                vegetation_item.appearance,
            )
        )
    for flying_item in scene.flying_objects:
        entities.append(
            _entity(
                1
                if isinstance(flying_item.geometry, BoxGeometry)
                else 0
                if isinstance(flying_item.geometry, SphereGeometry)
                else 2,
                flying_item.state.position_enu_m,
                flying_item.state,
                flying_item.geometry,
                flying_item.appearance,
            )
        )
    return entities


def projected_footprints(
    scene: LogicalSceneSnapshot, camera: CameraStateTruth
) -> dict[str, tuple[float, float]]:
    """Return projected object bounds for privileged preview metadata.

    This is a small geometry calculation, not a pixel renderer.  The image
    itself always comes from Metal; bounds are only used to size development
    overlays and frame truth.
    """

    i = camera.actual_intrinsics
    camera_from_enu = tuple(zip(*camera.optical_state.rotation_enu_from_body))
    result: dict[str, tuple[float, float]] = {}
    for item in scene.flying_objects:
        geometry = item.geometry
        if isinstance(geometry, SphereGeometry):
            half = geometry.radius_m
            local_vertices = [
                (x * half, y * half, z * half)
                for x in (-1.0, 1.0)
                for y in (-1.0, 1.0)
                for z in (-1.0, 1.0)
            ]
        elif isinstance(geometry, BoxGeometry):
            hx, hy, hz = geometry.half_extents_body_m
            local_vertices = [
                (x * hx, y * hy, z * hz)
                for x in (-1.0, 1.0)
                for y in (-1.0, 1.0)
                for z in (-1.0, 1.0)
            ]
        else:
            local_vertices = list(geometry.vertices_body_m)
        world_vertices = []
        for local in local_vertices:
            world = tuple(
                sum(item.state.rotation_enu_from_body[row][k] * local[k] for k in range(3))
                + item.state.position_enu_m[row]
                for row in range(3)
            )
            displacement = tuple(
                world[k] - camera.optical_state.position_enu_m[k] for k in range(3)
            )
            point = tuple(
                sum(camera_from_enu[row][k] * displacement[k] for k in range(3)) for row in range(3)
            )
            if point[2] > 1e-6:
                y = point[1] / point[2]
                x = point[0] / point[2]
                world_vertices.append(
                    (
                        i.focal_x_px * x + i.skew_px * y + i.principal_x_px,
                        i.focal_y_px * y + i.principal_y_px,
                    )
                )
        if world_vertices:
            xs, ys = zip(*world_vertices)
            width = max(
                0.0,
                min(float(i.image_width_px), max(xs))
                - max(0.0, min(float(i.image_width_px), min(xs))),
            )
            height = max(
                0.0,
                min(float(i.image_height_px), max(ys))
                - max(0.0, min(float(i.image_height_px), min(ys))),
            )
            if width > 0 and height > 0:
                result[str(item.truth_object_id)] = (width, height)
    return result


def _request(
    scene: LogicalSceneSnapshot,
    camera: CameraStateTruth,
    entities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if camera.actual_distortion.model.value != "none":
        raise ValueError("Metal renderer currently requires a distortion-free camera")
    if scene.terrain.model is not TerrainModel.FLAT:
        raise ValueError("Metal renderer currently requires flat terrain")
    i = camera.actual_intrinsics
    if min(i.image_width_px, i.image_height_px, i.focal_x_px, i.focal_y_px) <= 0:
        raise ValueError("camera dimensions and focal lengths must be positive")
    return {
        "width": i.image_width_px,
        "height": i.image_height_px,
        "focal": [i.focal_x_px, i.focal_y_px, i.principal_x_px, i.principal_y_px],
        "skew": i.skew_px,
        "origin": list(camera.optical_state.position_enu_m),
        "rotation": _rotation(camera.optical_state),
        "sky": list(scene.illumination.sky_linear_rgb),
        "sun": list(scene.illumination.sun_direction_enu_unit),
        "illuminance": scene.illumination.illuminance_lux,
        "terrainElevation": scene.terrain.base_elevation_m,
        "entities": _entities(scene) if entities is None else entities,
    }


class MetalRenderer:
    """One persistent Swift process and one serialized request stream."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("GPU scene rendering requires Apple Metal (macOS)")
        swiftc = "/usr/bin/swiftc" if Path("/usr/bin/swiftc").exists() else "swiftc"
        if not _SOURCE.exists():
            raise RuntimeError(f"Metal renderer source is missing: {_SOURCE}")
        _BINARY.parent.mkdir(parents=True, exist_ok=True)
        if not _BINARY.exists() or _SOURCE.stat().st_mtime_ns > _BINARY.stat().st_mtime_ns:
            result = subprocess.run(
                [
                    swiftc,
                    "-O",
                    str(_SOURCE),
                    "-o",
                    str(_BINARY),
                    "-framework",
                    "Metal",
                    "-framework",
                    "Foundation",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode:
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(f"failed to compile Metal renderer: {detail}")
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = subprocess.Popen(
            [str(_BINARY)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # The parent handles Ctrl+C, then closes stdin after its producer
            # has stopped. Do not interrupt an in-flight GPU response halfway.
            start_new_session=True,
        )
        # All cameras in one logical sample share the same world geometry. Keep
        # the JSON-ready entity list for that sample and replace it when the
        # immutable scene object advances. This avoids rebuilding static mesh
        # vertices once per camera without retaining an unbounded history.
        self._entity_scene: LogicalSceneSnapshot | None = None
        self._entity_payload: list[dict[str, Any]] | None = None

    def close(self) -> None:
        if getattr(self, "_process", None) is None:
            return
        with self._lock:
            process = self._process
            if process is None:
                return
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            self._process = None
            self._entity_scene = None
            self._entity_payload = None

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown order varies
        self.close()

    def render(self, scene: LogicalSceneSnapshot, camera: CameraStateTruth) -> tuple[bytes, float]:
        expected = (
            camera.actual_intrinsics.image_width_px * camera.actual_intrinsics.image_height_px
        )
        with self._lock:
            process = self._process
            if process is None or process.stdin is None or process.stdout is None:
                raise RuntimeError("Metal renderer is closed")
            if self._entity_scene is not scene:
                self._entity_scene = scene
                self._entity_payload = _entities(scene)
            request = json.dumps(
                _request(scene, camera, self._entity_payload), separators=(",", ":")
            ).encode("utf-8")
            if len(request) >= 2**32:
                raise ValueError("Metal render request is too large")
            if process.poll() is not None:
                stderr = process.stderr
                error = stderr.read().decode("utf-8", errors="replace").strip() if stderr else ""
                raise RuntimeError(f"Metal renderer exited: {error or 'unknown error'}")
            process.stdin.write(_HEADER.pack(len(request)))
            process.stdin.write(request)
            process.stdin.flush()
            header = _read_exact(process.stdout, _RESPONSE_HEADER.size)
            pixel_length, gpu_ns = _RESPONSE_HEADER.unpack(header)
            if pixel_length != expected * 3:
                raise RuntimeError(
                    f"Metal renderer returned {pixel_length} bytes, expected {expected * 3}"
                )
            raw = _read_exact(process.stdout, pixel_length)
        return raw, gpu_ns / 1_000_000.0


def _read_exact(stream: Any, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise RuntimeError("Metal renderer closed its output stream")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


_default_renderer: MetalRenderer | None = None
_default_lock = threading.Lock()


def close_default_renderer() -> None:
    global _default_renderer
    with _default_lock:
        if _default_renderer is not None:
            _default_renderer.close()
            _default_renderer = None


atexit.register(close_default_renderer)


def default_renderer() -> MetalRenderer:
    global _default_renderer
    with _default_lock:
        if _default_renderer is None:
            _default_renderer = MetalRenderer()
        return _default_renderer


def render_rgb8(scene: LogicalSceneSnapshot, camera: CameraStateTruth) -> tuple[bytes, float]:
    """Render one camera using the persistent Metal GPU process."""

    return default_renderer().render(scene, camera)


def quantize_rgb8(
    intensity: NDArray[np.float64],
    intensity_min: float,
    intensity_max: float,
    noise_std: float,
    seed: int,
    stream: str,
) -> bytes:
    """Apply sensor range/noise after GPU rendering, preserving three channels."""

    if intensity_max <= intensity_min:
        raise ValueError("intensity_max must be greater than intensity_min")
    if noise_std < 0:
        raise ValueError("sensor noise standard deviation cannot be negative")
    values = intensity
    if noise_std:
        import hashlib

        digest = hashlib.blake2b(f"{seed}:{stream}".encode(), digest_size=16).digest()
        rng = np.random.Generator(np.random.PCG64(int.from_bytes(digest, "big")))
        values = values + rng.normal(0.0, noise_std, values.shape)
    normalized = (values - intensity_min) / (intensity_max - intensity_min)
    return np.asarray(np.rint(np.clip(normalized, 0.0, 1.0) * 255.0), dtype=np.uint8).tobytes()
