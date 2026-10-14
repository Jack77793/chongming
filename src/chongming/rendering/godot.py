"""Godot 4 renderer for the virtual-scene image path.

Python remains the owner of experiment time, visibility and privileged truth.
This module only serializes an immutable scene snapshot, sends it to a long
lived Godot worker and returns tightly packed RGB8 pixels.  The deliberately
small framed protocol is the boundary for later Godot scene/material work.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import struct
import subprocess
import threading
import time
import uuid
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
    TriangleMeshGeometry,
)

_ROOT = Path(__file__).resolve().parents[3]
_PROJECT = Path(__file__).with_name("godot")
_REQUEST_HEADER = struct.Struct("<I")
_RESPONSE_HEADER = struct.Struct("<QQ")
_MAX_REQUEST = 256 * 1024 * 1024


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
        payload["vertices"] = [[float(value) for value in vertex] for vertex in geometry.vertices_body_m]
        payload["triangles"] = [list(triangle) for triangle in geometry.triangle_vertex_indices]
    else:  # pragma: no cover - GeometrySpec is a closed union
        raise TypeError(f"unsupported geometry: {type(geometry).__name__}")
    return payload


def _entities(scene: LogicalSceneSnapshot) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    terrain = scene.terrain
    if terrain.heightfield_geometry is not None and terrain.heightfield_origin_enu_m is not None:
        east, north = terrain.heightfield_origin_enu_m
        pose = KinematicState(
            position_enu_m=(east, north, terrain.base_elevation_m),
            velocity_enu_mps=(0.0, 0.0, 0.0),
            rotation_enu_from_body=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            angular_velocity_body_radps=(0.0, 0.0, 0.0),
        )
        entities.append(_entity(2, pose.position_enu_m, pose, terrain.heightfield_geometry, terrain.appearance))
    for world_item in scene.world_objects:
        entities.append(_entity(_geometry_type(world_item.geometry), world_item.pose.position_enu_m, world_item.pose, world_item.geometry, world_item.appearance))
    for vegetation_item in scene.vegetation:
        entities.append(_entity(_geometry_type(vegetation_item.geometry), vegetation_item.base_pose.position_enu_m, vegetation_item.base_pose, vegetation_item.geometry, vegetation_item.appearance))
    for flying_item in scene.flying_objects:
        entities.append(_entity(_geometry_type(flying_item.geometry), flying_item.state.position_enu_m, flying_item.state, flying_item.geometry, flying_item.appearance))
    return entities


def _geometry_type(geometry: GeometrySpec) -> int:
    return 0 if isinstance(geometry, SphereGeometry) else 1 if isinstance(geometry, BoxGeometry) else 2


def _request(scene: LogicalSceneSnapshot, camera: CameraStateTruth, entities: list[dict[str, Any]]) -> dict[str, Any]:
    if camera.actual_distortion.model.value != "none":
        raise ValueError("Godot renderer currently requires a distortion-free camera")
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
        "entities": entities,
    }


def projected_footprints(scene: LogicalSceneSnapshot, camera: CameraStateTruth) -> dict[str, tuple[float, float]]:
    """Return projection bounds for truth/monitoring; pixels come from Godot."""
    i = camera.actual_intrinsics
    camera_from_enu = tuple(zip(*camera.optical_state.rotation_enu_from_body))
    result: dict[str, tuple[float, float]] = {}
    for item in scene.flying_objects:
        if isinstance(item.geometry, SphereGeometry):
            r = item.geometry.radius_m
            local = [(x * r, y * r, z * r) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
        elif isinstance(item.geometry, BoxGeometry):
            hx, hy, hz = item.geometry.half_extents_body_m
            local = [(x * hx, y * hy, z * hz) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]
        else:
            local = list(item.geometry.vertices_body_m)
        projected: list[tuple[float, float]] = []
        for point in local:
            world = tuple(sum(item.state.rotation_enu_from_body[row][k] * point[k] for k in range(3)) + item.state.position_enu_m[row] for row in range(3))
            displacement = tuple(world[k] - camera.optical_state.position_enu_m[k] for k in range(3))
            camera_point = tuple(sum(camera_from_enu[row][k] * displacement[k] for k in range(3)) for row in range(3))
            if camera_point[2] > 1e-6:
                x, y = camera_point[0] / camera_point[2], camera_point[1] / camera_point[2]
                projected.append((i.focal_x_px * x + i.skew_px * y + i.principal_x_px, i.focal_y_px * y + i.principal_y_px))
        if projected:
            xs, ys = zip(*projected)
            width = max(0.0, min(float(i.image_width_px), max(xs)) - max(0.0, min(float(i.image_width_px), min(xs))))
            height = max(0.0, min(float(i.image_height_px), max(ys)) - max(0.0, min(float(i.image_height_px), min(ys))))
            if width > 0 and height > 0:
                result[str(item.truth_object_id)] = (width, height)
    return result


def quantize_rgb8(intensity: NDArray[np.float64], intensity_min: float, intensity_max: float, noise_std: float, seed: int, stream: str) -> bytes:
    if intensity_max <= intensity_min:
        raise ValueError("intensity_max must be greater than intensity_min")
    if noise_std < 0:
        raise ValueError("sensor noise standard deviation cannot be negative")
    values = intensity
    if noise_std:
        digest = __import__("hashlib").blake2b(f"{seed}:{stream}".encode(), digest_size=16).digest()
        values = values + np.random.Generator(np.random.PCG64(int.from_bytes(digest, "big"))).normal(0.0, noise_std, values.shape)
    return np.asarray(np.rint(np.clip((values - intensity_min) / (intensity_max - intensity_min), 0.0, 1.0) * 255.0), dtype=np.uint8).tobytes()


def _read_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise RuntimeError("Godot renderer closed its socket")
        data.extend(chunk)
    return bytes(data)


def _read_line(sock: socket.socket, limit: int = 256) -> bytes:
    data = bytearray()
    while len(data) < limit:
        chunk = sock.recv(1)
        if not chunk:
            raise RuntimeError("Godot renderer closed its handshake")
        data.extend(chunk)
        if chunk == b"\n":
            return bytes(data)
    raise RuntimeError("Godot renderer handshake is too long")


def _godot_executable() -> str:
    configured = os.environ.get("CHONGMING_GODOT")
    if configured:
        return configured
    for name in ("godot4", "godot"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("Godot 4 executable not found; set CHONGMING_GODOT or install godot4")


class GodotRenderer:
    """One persistent Godot process and one serialized TCP request stream."""

    def __init__(self, startup_timeout: float = 8.0) -> None:
        executable = _godot_executable()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        token = uuid.uuid4().hex
        self._process: subprocess.Popen[bytes] | None = subprocess.Popen(
            [executable, "--headless", "--path", str(_PROJECT), "--", "--port", str(port), "--token", token],
            cwd=_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._entity_scene: LogicalSceneSnapshot | None = None
        self._entity_payload: list[dict[str, Any]] | None = None
        deadline = time.monotonic() + startup_timeout
        last_error = ""
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                detail = self._process.stderr.read().decode(errors="replace").strip() if self._process.stderr else ""
                self.close()
                raise RuntimeError(f"Godot renderer exited during startup: {detail or 'unknown error'}")
            try:
                self._socket = socket.create_connection(("127.0.0.1", port), timeout=0.25)
                break
            except OSError as error:
                last_error = str(error)
                time.sleep(0.03)
        if self._socket is None:
            self.close()
            raise RuntimeError(f"Godot renderer did not become ready: {last_error}")
        self._socket.settimeout(max(0.1, deadline - time.monotonic()))
        greeting = _read_line(self._socket).decode("utf-8", errors="replace").strip()
        if greeting != f"READY {token}":
            self.close()
            raise RuntimeError(f"Godot renderer handshake failed: {greeting!r}")
        self._socket.settimeout(None)

    def close(self) -> None:
        sock = getattr(self, "_socket", None)
        self._socket = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        process = getattr(self, "_process", None)
        if process is not None:
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

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown order varies
        self.close()

    def render(self, scene: LogicalSceneSnapshot, camera: CameraStateTruth) -> tuple[bytes, float]:
        expected = camera.actual_intrinsics.image_width_px * camera.actual_intrinsics.image_height_px * 3
        with self._lock:
            sock = self._socket
            if sock is None:
                raise RuntimeError("Godot renderer is closed")
            if self._entity_scene is not scene:
                self._entity_scene, self._entity_payload = scene, _entities(scene)
            payload = json.dumps(_request(scene, camera, self._entity_payload or []), separators=(",", ":")).encode()
            if len(payload) >= 2**32 or len(payload) > _MAX_REQUEST:
                raise ValueError("Godot render request is too large")
            sock.sendall(_REQUEST_HEADER.pack(len(payload)) + payload)
            pixel_length, render_ns = _RESPONSE_HEADER.unpack(_read_exact(sock, _RESPONSE_HEADER.size))
            if pixel_length != expected:
                raise RuntimeError(f"Godot renderer returned {pixel_length} bytes, expected {expected}")
            return _read_exact(sock, pixel_length), render_ns / 1_000_000.0


_default_renderer: GodotRenderer | None = None
_default_lock = threading.Lock()


def close_default_renderer() -> None:
    global _default_renderer
    with _default_lock:
        if _default_renderer is not None:
            _default_renderer.close()
            _default_renderer = None


atexit.register(close_default_renderer)


def default_renderer() -> GodotRenderer:
    global _default_renderer
    with _default_lock:
        if _default_renderer is None:
            _default_renderer = GodotRenderer()
        return _default_renderer


def render_rgb8(scene: LogicalSceneSnapshot, camera: CameraStateTruth) -> tuple[bytes, float]:
    return default_renderer().render(scene, camera)
