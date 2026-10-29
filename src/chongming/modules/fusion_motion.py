"""Constant-velocity exposure alignment; original admitted rays remain immutable."""

from __future__ import annotations

from typing import cast

import numpy as np
from numpy.typing import NDArray

from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.uncertainty import covariance_factor

Array = NDArray[np.float64]


def midpoint(ray: f.AdmittedRay) -> int:
    capture = ray.event.capture_time
    return (capture.exposure_start_ns + capture.exposure_end_ns) // 2


def alignment_reference(rays: tuple[f.AdmittedRay, ...]) -> int:
    """Align to the earliest exposure, never extrapolate past current evidence."""
    return min(midpoint(ray) for ray in rays)


def midpoint_span(rays: tuple[f.AdmittedRay, ...]) -> int:
    times = [midpoint(ray) for ray in rays]
    return max(times) - min(times)


def origins_at(rays: tuple[f.AdmittedRay, ...], reference_ns: int, velocity: m.Vec3) -> Array:
    dt = np.asarray([(reference_ns - midpoint(ray)) / 1e9 for ray in rays], dtype=np.float64)
    origins = np.asarray([ray.event.ray_origin_enu_m for ray in rays], dtype=np.float64)
    return origins + dt[:, None] * np.asarray(velocity, dtype=np.float64)


def matrix3(value: Array) -> m.Matrix3x3:
    return cast(m.Matrix3x3, tuple(tuple(float(item) for item in row) for row in value))


def validate_policy(policy: f.GeometryPolicy) -> None:
    if policy.maximum_unaligned_span_ns < 0:
        raise ValueError("unaligned exposure span must be nonnegative")
    if policy.motion_alignment_model is f.MotionAlignmentModel.CONSERVATIVE_SPEED_BOUND:
        raise NotImplementedError(
            "speed-bound localization is not implemented; use a narrow time gate"
        )


def validate_predictions(
    window: f.SealedObservationWindow,
    world_frame: m.WorldFrameId,
    predictions: tuple[f.TrackPrediction, ...],
) -> None:
    for prediction in predictions:
        if (
            prediction.center_session_id != window.key.center_session_id
            or prediction.clock_domain_id != window.key.capture_clock_domain_id
            or prediction.world_frame_id != world_frame
            or np.asarray(prediction.position_enu_m).shape != (3,)
            or np.asarray(prediction.velocity_enu_mps).shape != (3,)
            or not np.isfinite((prediction.position_enu_m, prediction.velocity_enu_mps)).all()
        ):
            raise ValueError("prediction session, frame, clock or position mismatch")
        if prediction.conditional_state_covariance_enu is not None:
            covariance_factor(
                prediction.conditional_state_covariance_enu, 6, "prediction covariance"
            )
    if len({prediction.track_id for prediction in predictions}) != len(predictions):
        raise ValueError("duplicate track prediction")
