"""Deterministic gray8 surface rendering using the actual camera optics.

This is a small CPU ray/surface backend, not a game engine. A nearest-depth
buffer preserves partial silhouettes and occlusion even when a target's center
is outside the FOV or occluded. Capture eligibility remains logic-owned.
"""

from __future__ import annotations

from functools import lru_cache
from hashlib import blake2b

import numpy as np
from numpy.typing import NDArray

from chongming.contracts.scene import (
    BoxGeometry,
    CameraStateTruth,
    GeometrySpec,
    KinematicState,
    LogicalSceneSnapshot,
    SceneCameraDistortion,
    SceneCameraIntrinsics,
    SceneDistortionModel,
    SensorArtifactSpec,
    SphereGeometry,
    SurfaceAppearance,
    TerrainModel,
)
from chongming.rendering.geometry import NEAR, FloatArray, surface_hits


@lru_cache(maxsize=8)
def optical_rays(
    intrinsics: SceneCameraIntrinsics, distortion: SceneCameraDistortion
) -> FloatArray:
    """Invert the generating lens model at integer pixel centers; cache is bounded."""
    i = intrinsics
    if i.image_width_px <= 0 or i.image_height_px <= 0 or min(i.focal_x_px, i.focal_y_px) <= 0:
        raise ValueError("camera dimensions and focal lengths must be positive")
    rows, columns = np.indices((i.image_height_px, i.image_width_px), dtype=np.float64)
    yd = (rows.ravel() - i.principal_y_px) / i.focal_y_px
    xd = (columns.ravel() - i.principal_x_px - i.skew_px * yd) / i.focal_x_px
    x, y = xd.copy(), yd.copy()
    if distortion.model is SceneDistortionModel.BROWN_CONRADY:
        k1, k2, k3 = distortion.radial_k1_k2_k3
        p1, p2 = distortion.tangential_p1_p2
        for _ in range(30):
            r2 = x * x + y * y
            radial = 1 + r2 * (k1 + r2 * (k2 + r2 * k3))
            dx = 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
            dy = p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
            x, y = (xd - dx) / radial, (yd - dy) / radial
        r2 = x * x + y * y
        radial = 1 + r2 * (k1 + r2 * (k2 + r2 * k3))
        error_x = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x) - xd
        error_y = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y - yd
        if not np.all(np.isfinite(x + y)) or np.max(np.abs(error_x) + np.abs(error_y)) > 1e-7:
            raise ValueError("lens distortion cannot be inverted over this image")
    result = np.column_stack((x, y, np.ones_like(x)))
    result.flags.writeable = False
    return result


def luminance(appearance: SurfaceAppearance) -> float:
    return float(np.dot(appearance.linear_rgb, (0.2126, 0.7152, 0.0722)))


def image_region(
    camera: CameraStateTruth, pose: KinematicState, geometry: GeometrySpec
) -> tuple[int, int, int, int]:
    """Conservative perspective bounds; near-plane/lens cases use the whole image."""
    i = camera.actual_intrinsics
    full = (0, 0, i.image_width_px, i.image_height_px)
    if camera.actual_distortion.model is not SceneDistortionModel.NONE:
        return full
    if isinstance(geometry, SphereGeometry):
        low = np.full(3, -geometry.radius_m)
        high = -low
    elif isinstance(geometry, BoxGeometry):
        high = np.asarray(geometry.half_extents_body_m)
        low = -high
    else:
        if not geometry.vertices_body_m:
            return (0, 0, 0, 0)
        vertices = np.asarray(geometry.vertices_body_m)
        low, high = vertices.min(axis=0), vertices.max(axis=0)
    corners = np.asarray(
        [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
    )
    world = corners @ np.asarray(pose.rotation_enu_from_body).T + pose.position_enu_m
    optical = (world - camera.optical_state.position_enu_m) @ np.asarray(
        camera.optical_state.rotation_enu_from_body
    )
    if np.max(optical[:, 2]) <= NEAR:
        return (0, 0, 0, 0)
    if np.min(optical[:, 2]) <= NEAR:
        return full
    x, y = optical[:, 0] / optical[:, 2], optical[:, 1] / optical[:, 2]
    u = i.focal_x_px * x + i.skew_px * y + i.principal_x_px
    v = i.focal_y_px * y + i.principal_y_px
    return (
        max(0, min(i.image_width_px, int(np.floor(u.min())))),
        max(0, min(i.image_height_px, int(np.floor(v.min())))),
        max(0, min(i.image_width_px, int(np.ceil(u.max())) + 1)),
        max(0, min(i.image_height_px, int(np.ceil(v.max())) + 1)),
    )


def render_surfaces(
    scene: LogicalSceneSnapshot, camera: CameraStateTruth
) -> tuple[FloatArray, dict[str, tuple[float, float]]]:
    """Linear intensities plus in-image, unoccluded target raster footprint sizes."""
    i = camera.actual_intrinsics
    rays = (
        optical_rays(i, camera.actual_distortion)
        @ np.asarray(camera.optical_state.rotation_enu_from_body).T
    )
    origin = camera.optical_state.position_enu_m
    count = len(rays)
    intensity = np.full(
        count, float(np.dot(scene.illumination.sky_linear_rgb, (0.2126, 0.7152, 0.0722)))
    )
    depth = np.full(count, np.inf)
    sun = np.asarray(scene.illumination.sun_direction_enu_unit, dtype=np.float64)
    sun_length = float(np.linalg.norm(sun))
    if sun_length <= 0 or scene.illumination.illuminance_lux < 0:
        raise ValueError("sun direction must be nonzero and illuminance nonnegative")
    sun /= sun_length
    # A fixed reference response keeps lux meaningful without automatic exposure.
    exposure_gain = scene.illumination.illuminance_lux / 20_000.0

    if scene.terrain.model is not TerrainModel.FLAT:
        raise ValueError("software preview currently requires flat terrain")
    ground_depth = np.full(count, np.inf)
    np.divide(
        scene.terrain.base_elevation_m - origin[2],
        rays[:, 2],
        out=ground_depth,
        where=np.abs(rays[:, 2]) > 1e-12,
    )
    ground = ground_depth > NEAR
    ground &= np.isfinite(ground_depth)
    depth[ground] = ground_depth[ground]
    ground_shade = (0.3 + 0.7 * max(0.0, float(sun[2]))) * exposure_gain
    intensity[ground] = (
        luminance(scene.terrain.appearance)
        * scene.terrain.appearance.diffuse_reflectance
        * ground_shade
    )

    entities = (
        [
            (str(item.object_id), item.pose, item.geometry, item.appearance, False)
            for item in scene.world_objects
        ]
        + [
            (str(item.vegetation_id), item.base_pose, item.geometry, item.appearance, False)
            for item in scene.vegetation
        ]
        + [
            (str(item.truth_object_id), item.state, item.geometry, item.appearance, True)
            for item in scene.flying_objects
        ]
    )
    footprints: dict[str, tuple[float, float]] = {}
    for object_id, pose, geometry, appearance, is_target in entities:
        x0, y0, x1, y1 = image_region(camera, pose, geometry)
        if x0 >= x1 or y0 >= y1:
            continue
        region_rays = rays.reshape(i.image_height_px, i.image_width_px, 3)[y0:y1, x0:x1].reshape(
            -1, 3
        )
        distances, normals = surface_hits(origin, region_rays, pose, geometry)
        region_shape = (y1 - y0, x1 - x0)
        hits = np.isfinite(distances)
        if is_target and np.any(hits):
            rows, cols = np.nonzero(hits.reshape(region_shape))
            footprints[object_id] = (float(np.ptp(cols) + 1), float(np.ptp(rows) + 1))
        region_depth = depth.reshape(i.image_height_px, i.image_width_px)[y0:y1, x0:x1]
        region_intensity = intensity.reshape(i.image_height_px, i.image_width_px)[y0:y1, x0:x1]
        visible = hits & (distances < region_depth.ravel())
        if not np.any(visible):
            continue
        diffuse = np.maximum(0.0, normals[visible] @ sun)
        region_intensity[visible.reshape(region_shape)] = (
            luminance(appearance)
            * appearance.diffuse_reflectance
            * (0.3 + 0.7 * diffuse)
            * exposure_gain
        )
        region_depth[visible.reshape(region_shape)] = distances[visible]
    return intensity.reshape(i.image_height_px, i.image_width_px), footprints


def quantize(
    intensity: FloatArray,
    artifacts: SensorArtifactSpec,
    seed: int,
    stream: str,
) -> NDArray[np.uint8]:
    """Frame-local, named noise stream; adding another camera cannot shift it."""
    if artifacts.intensity_max <= artifacts.intensity_min:
        raise ValueError("intensity_max must be greater than intensity_min")
    if artifacts.sensor_noise_std_intensity < 0:
        raise ValueError("sensor noise standard deviation cannot be negative")
    if artifacts.sensor_noise_std_intensity:
        digest = blake2b(f"{seed}:{stream}".encode(), digest_size=16).digest()
        rng = np.random.Generator(np.random.PCG64(int.from_bytes(digest, "big")))
        intensity = intensity + rng.normal(
            0.0, artifacts.sensor_noise_std_intensity, intensity.shape
        )
    normalized = (intensity - artifacts.intensity_min) / (
        artifacts.intensity_max - artifacts.intensity_min
    )
    return np.asarray(np.rint(np.clip(normalized, 0, 1) * 255), dtype=np.uint8)
