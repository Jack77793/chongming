"""Joint virtual-scene and auxiliary-camera calibration evaluation.

The runner deliberately keeps privileged camera truth on the evaluation side of
the boundary.  OpenCV receives only the ordinary auxiliary ``CameraFrame``;
truth is used after the estimate has been produced to measure angular error,
installation-relative error, translation perturbation and rotation-only
parallax.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from chongming.calibration import (
    CalibrationHistoryStore,
    CalibrationStateMachine,
    OpenCVCalibrationEstimator,
    OpenCVReferenceRecognizer,
)
from chongming.contracts.device import (
    CalibrationAttempt,
    CalibrationPolicy,
)
from chongming.contracts.model import (
    CameraRole,
    ProcessingTime,
    SessionId,
    StationConfig,
    StationId,
)
from chongming.contracts.scene import ScenarioSpec, ScenarioStep
from chongming.modules.device import EventSequencerModule


def _rotation_angle(left: Any, right: Any) -> float:
    """Return the angle in radians between two finite 3x3 rotations."""
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    if a.shape != (3, 3) or b.shape != (3, 3):
        raise ValueError("rotation must be a 3x3 matrix")
    trace = float(np.trace(a.T @ b))
    cosine = max(-1.0, min(1.0, (trace - 1.0) / 2.0))
    return math.acos(cosine)


def _vector_delta(actual: Any, nominal: Any) -> list[float]:
    result = np.asarray(actual, dtype=np.float64) - np.asarray(nominal, dtype=np.float64)
    if result.shape != (3,) or not np.all(np.isfinite(result)):
        raise ValueError("camera position must be a finite 3-vector")
    return [round(float(value), 9) for value in result]


def default_calibration_policy() -> CalibrationPolicy:
    """Conservative development policy for the low-rate virtual test stream."""
    return CalibrationPolicy(
        update_period_ns=0,
        stale_after_ns=2_000_000_000,
        recovery_success_count=1,
        minimum_reference_count=4,
        maximum_reprojection_rmse_px=2.0,
        maximum_rotation_only_parallax_px=None,
        estimator_id="opencv-checkerboard-joint-evaluation",
    )


@dataclass
class _StationPipeline:
    session_id: SessionId
    config: StationConfig
    recognizer: OpenCVReferenceRecognizer
    estimator: OpenCVCalibrationEstimator
    states: CalibrationStateMachine


class JointCalibrationRunner:
    """Run calibration on ordinary frames and compare it with scene truth."""

    def __init__(
        self,
        spec: ScenarioSpec,
        *,
        policy: CalibrationPolicy | None = None,
    ) -> None:
        self.spec = spec
        self.policy = policy or default_calibration_policy()
        self.configs = {config.station_id: config for config in spec.station_configs}
        self._pipelines: dict[StationId, _StationPipeline] = {}

    def _pipeline(self, station_id: StationId, session_id: SessionId) -> _StationPipeline:
        current = self._pipelines.get(station_id)
        if current is not None and current.session_id == session_id:
            return current
        config = self.configs.get(station_id)
        if config is None:
            raise ValueError(f"missing station configuration: {station_id}")
        history = CalibrationHistoryStore(
            station_id,
            session_id,
            station_config_digest=config.config_digest,
        )
        sequencer = EventSequencerModule(
            station_id=station_id,
            station_session_id=session_id,
            schema_version="joint-calibration-v1",
        )
        pipeline = _StationPipeline(
            session_id=session_id,
            config=config,
            recognizer=OpenCVReferenceRecognizer(),
            estimator=OpenCVCalibrationEstimator(self.policy),
            states=CalibrationStateMachine(
                self.policy,
                history,
                sequencer,
                station_id=station_id,
                station_session_id=session_id,
            ),
        )
        self._pipelines[station_id] = pipeline
        return pipeline

    def process_step(self, step: ScenarioStep) -> tuple[dict[str, Any], ...]:
        """Process rendered auxiliary frames from one discrete scenario step."""
        truth_cameras = {
            (camera.station_id, camera.camera_role): camera
            for camera in step.truth.logical_scene.cameras
        }
        results: list[dict[str, Any]] = []
        for frame in step.video.frames:
            if frame.camera_role is not CameraRole.AUXILIARY:
                continue
            pipeline = self._pipeline(frame.station_id, frame.station_session_id)
            midpoint = (
                frame.capture_time.exposure_start_ns + frame.capture_time.exposure_end_ns
            ) // 2
            now = ProcessingTime(
                clock_domain_id=frame.capture_time.clock_domain_id, time_ns=midpoint
            )
            started = time.perf_counter_ns()
            cpu_started = time.process_time_ns()
            recognition_started = time.perf_counter_ns()
            references = pipeline.recognizer.recognize(frame, pipeline.config)
            recognition_ms = (time.perf_counter_ns() - recognition_started) / 1_000_000.0
            estimation_started = time.perf_counter_ns()
            attempt: CalibrationAttempt = pipeline.estimator.estimate(
                references, pipeline.config, now
            )
            estimation_ms = (time.perf_counter_ns() - estimation_started) / 1_000_000.0
            state_started = time.perf_counter_ns()
            update = pipeline.states.consider(attempt, pipeline.config)
            state_ms = (time.perf_counter_ns() - state_started) / 1_000_000.0
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
            cpu_elapsed_ms = (time.process_time_ns() - cpu_started) / 1_000_000.0
            main_truth = truth_cameras.get((frame.station_id, CameraRole.MAIN))
            aux_truth = truth_cameras.get((frame.station_id, CameraRole.AUXILIARY))
            if main_truth is None or aux_truth is None:
                raise ValueError(f"missing main/auxiliary truth for station {frame.station_id}")

            estimate = attempt.estimate
            quality = estimate.quality if estimate is not None else None
            nominal_aux_position = None
            if pipeline.config.full_rigid_transform_aux_from_main is not None:
                baseline_main = np.asarray(
                    pipeline.config.baseline_orientation_enu_from_main, dtype=np.float64
                )
                fixed_aux_from_main = np.asarray(
                    pipeline.config.fixed_rotation_aux_from_main, dtype=np.float64
                )
                baseline_aux = baseline_main @ fixed_aux_from_main.T
                nominal_aux_position = np.asarray(
                    pipeline.config.main_camera_position_enu_m, dtype=np.float64
                ) - baseline_aux @ np.asarray(
                    pipeline.config.full_rigid_transform_aux_from_main.translation_a_m,
                    dtype=np.float64,
                )
            result: dict[str, Any] = {
                "station_id": str(frame.station_id),
                "session_id": str(frame.station_session_id),
                "frame_id": str(frame.frame_id),
                "capture_midpoint_ns": midpoint,
                "recognition_status": references.processing_status.value,
                "recognition_reason_codes": list(references.reason_codes),
                "corner_count": len(references.observations),
                "calibration_status": attempt.status.value,
                "reason_codes": list(attempt.reason_codes),
                "health": update.state_event.state.health.value,
                "reprojection_rmse_px": (
                    round(float(quality.reprojection_rmse_px), 6)
                    if quality is not None and quality.reprojection_rmse_px is not None
                    else None
                ),
                "rotation_only_parallax_px": (
                    round(float(quality.rotation_only_parallax_px), 6)
                    if quality is not None and quality.rotation_only_parallax_px is not None
                    else None
                ),
                "rotation_error_deg": None,
                "relative_installation_error_deg": round(
                    math.degrees(
                        _rotation_angle(
                            np.asarray(aux_truth.optical_state.rotation_enu_from_body).T
                            @ np.asarray(main_truth.optical_state.rotation_enu_from_body),
                            np.asarray(pipeline.config.fixed_rotation_aux_from_main),
                        )
                    ),
                    6,
                ),
                "main_position_offset_enu_m": _vector_delta(
                    main_truth.optical_state.position_enu_m,
                    pipeline.config.main_camera_position_enu_m,
                ),
                "auxiliary_position_offset_enu_m": _vector_delta(
                    aux_truth.optical_state.position_enu_m,
                    nominal_aux_position
                    if nominal_aux_position is not None
                    else aux_truth.optical_state.position_enu_m,
                ),
                "processing_ms": round(elapsed_ms, 3),
                "processing_cpu_ms": round(cpu_elapsed_ms, 3),
                "recognition_ms": round(recognition_ms, 3),
                "estimation_ms": round(estimation_ms, 3),
                "state_ms": round(state_ms, 3),
            }
            if estimate is not None:
                result["rotation_error_deg"] = round(
                    math.degrees(
                        _rotation_angle(
                            estimate.rotation_enu_from_main,
                            main_truth.optical_state.rotation_enu_from_body,
                        )
                    ),
                    6,
                )
            results.append(result)
        return tuple(results)


__all__ = ["JointCalibrationRunner", "default_calibration_policy"]
