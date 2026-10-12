"""Minimal typed objects for the stage-2 architecture skeleton.

These objects describe ownership and units. They intentionally do not perform
projection, calibration, association, voxel voting, or tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

Vec2Px = tuple[float, float]
Vec3M = tuple[float, float, float]
Rotation3x3 = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]

StationId = NewType("StationId", str)
SessionId = NewType("SessionId", str)
FrameId = NewType("FrameId", str)
TrackId = NewType("TrackId", str)
WorldFrameId = NewType("WorldFrameId", str)
ConfigDigest = NewType("ConfigDigest", str)
CalibrationSolutionId = NewType("CalibrationSolutionId", str)
EventId = tuple[StationId, SessionId, int]


class CameraRole(StrEnum):
    MAIN = "main"
    AUXILIARY = "auxiliary"


class CalibrationHealth(StrEnum):
    INITIALIZING = "initializing"
    VALID = "valid"
    FROZEN = "frozen"
    STALE = "stale"
    RECOVERING = "recovering"
    INVALID = "invalid"


class CalibrationUsage(StrEnum):
    VALID = "valid"
    FROZEN = "frozen"
    STALE = "stale"


class FusionStatus(StrEnum):
    LOCATED = "located"
    GEOMETRY_DEGENERATE = "geometry_degenerate"
    ASSOCIATION_AMBIGUOUS = "association_ambiguous"
    INSUFFICIENT_STATIONS = "insufficient_stations"
    NO_CANDIDATE_IN_VOLUME = "no_candidate_in_volume"
    REJECTED_INPUT = "rejected_input"


@dataclass(frozen=True)
class CaptureTime:
    """Exposure timing on the shared experiment clock; values are nanoseconds."""

    clock_domain_id: str
    exposure_start_ns: int
    exposure_end_ns: int
    clock_uncertainty_ns: int
    rolling_shutter_model: str | None


@dataclass(frozen=True)
class EventEnvelope:
    schema_version: str
    event_type: str
    station_id: StationId
    station_session_id: SessionId
    sequence_no: int
    station_config_digest: ConfigDigest


@dataclass(frozen=True)
class CameraFrame:
    station_id: StationId
    camera_role: CameraRole
    frame_id: FrameId
    capture_time: CaptureTime
    image: object
    camera_config_ref: ConfigDigest


@dataclass(frozen=True)
class Observation2D:
    station_id: StationId
    main_frame_id: FrameId
    capture_time: CaptureTime
    centroid_px: Vec2Px
    detection_score: float
    local_track_id: TrackId | None
    track_quality: float | None


@dataclass(frozen=True)
class DetectionBatch:
    main_frame_id: FrameId
    processing_status: str
    observations: tuple[Observation2D, ...]


@dataclass(frozen=True)
class StationConfig:
    station_id: StationId
    config_version: str
    config_digest: ConfigDigest
    world_frame_id: WorldFrameId
    geodetic_anchor_ref: str
    main_camera_position_enu_m: Vec3M
    baseline_orientation_enu_from_main: Rotation3x3
    main_camera_intrinsics_ref: str
    main_camera_distortion_ref: str
    auxiliary_camera_intrinsics_ref: str
    auxiliary_camera_distortion_ref: str
    fixed_rotation_aux_from_main: Rotation3x3
    reference_anchor_kind: str
    reference_anchor_ref: str
    reference_scene_version: str
    installation_uncertainty_ref: str


@dataclass(frozen=True)
class CalibrationState:
    station_id: StationId
    station_session_id: SessionId
    solution_id: CalibrationSolutionId | None
    station_config_digest: ConfigDigest
    health: CalibrationHealth
    reference_capture_time: CaptureTime | None
    accepted_time_ns: int | None
    last_trusted_time_ns: int | None
    main_world_rotation: Rotation3x3 | None
    correction_covariance_rad2: Rotation3x3 | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class RayUncertainty:
    model: str
    tangent_u_enu_unit: Vec3M
    tangent_v_enu_unit: Vec3M
    independent_angular_covariance_rad2: tuple[tuple[float, float], tuple[float, float]]
    independent_origin_covariance_m2: tuple[tuple[float, ...], ...]
    shared_installation_state_ref: str | None
    shared_calibration_solution_ref: CalibrationSolutionId | None


@dataclass(frozen=True)
class RayEvent:
    envelope: EventEnvelope
    frame_id: FrameId
    local_track_id: TrackId | None
    capture_time: CaptureTime
    world_frame_id: WorldFrameId
    calibration_solution_id: CalibrationSolutionId
    calibration_usage: CalibrationUsage
    ray_origin_enu_m: Vec3M
    ray_direction_enu_unit: Vec3M
    uncertainty: RayUncertainty
    detection_score: float
    detection_score_definition: str
    track_quality: float | None
    track_quality_definition: str | None
    quality_flags: tuple[str, ...]


@dataclass(frozen=True)
class DiagnosticEvent:
    envelope: EventEnvelope
    related_frame_id: FrameId | None
    capture_time: CaptureTime | None
    category: str
    reason_codes: tuple[str, ...]
    related_config_or_calibration_ref: str | None


@dataclass(frozen=True)
class VoxelSearchSpec:
    world_frame_id: WorldFrameId
    min_enu_m: Vec3M
    max_enu_m: Vec3M
    step_enu_m: Vec3M
    forward_range_m: tuple[float, float] | None


@dataclass(frozen=True)
class FusionMeasurement:
    center_session_id: SessionId
    measurement_id: str
    reference_time_ns: int
    support_exposure_span_ns: int
    status: FusionStatus
    position_enu_m: Vec3M | None
    conditional_position_covariance_enu_m2: tuple[tuple[float, ...], ...] | None
    supporting_ray_event_ids: tuple[EventId, ...]
    supporting_station_ids: tuple[StationId, ...]
    residuals_m: tuple[float, ...]
    geometry_condition: float | None
    minimum_intersection_angle_rad: float | None
    association_hypothesis_id: str | None


@dataclass(frozen=True)
class TrackEvent:
    center_session_id: SessionId
    track_id: TrackId
    event_time_ns: int
    lifecycle: str
    position_enu_m: Vec3M | None
    velocity_enu_mps: Vec3M | None
    conditional_state_covariance: tuple[tuple[float, ...], ...] | None
    source_measurement_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    random_seed: int
    world_frame_id: WorldFrameId
    voxel_search: VoxelSearchSpec
    station_configs: tuple[StationConfig, ...]
    scene_definition_ref: str


@dataclass(frozen=True)
class StationFrameSet:
    main_frame: CameraFrame
    auxiliary_frame: CameraFrame


@dataclass(frozen=True)
class OnlineScenarioStep:
    station_frames: tuple[StationFrameSet, ...]
    virtual_calibration_states: tuple[CalibrationState, ...]
    virtual_ray_events: tuple[RayEvent, ...]


@dataclass(frozen=True)
class ScenarioTruth:
    """Privileged truth data; never a parameter of an online algorithm port."""

    time_ns: int
    truth_ref: str


@dataclass(frozen=True)
class ScenarioStep:
    online: OnlineScenarioStep
    truth: ScenarioTruth


@dataclass(frozen=True)
class OnlineEvaluationData:
    detections: tuple[DetectionBatch, ...]
    calibrations: tuple[CalibrationState, ...]
    events: tuple[RayEvent | DiagnosticEvent, ...]
    measurements: tuple[FusionMeasurement, ...]
    tracks: tuple[TrackEvent, ...]
