"""Device dependency declarations. Business methods deliberately remain unimplemented."""

from __future__ import annotations

from dataclasses import dataclass

from chongming.contracts.device import (
    CalibrationAttempt,
    CalibrationPolicy,
    Candidate2DBatch,
    DetectionPolicy,
    DeviceFrameResult,
    MainDetectionResult,
    RayGenerationPolicy,
    ReferenceObservationBatch,
    Tracking2DPolicy,
    Tracking2DResult,
)
from chongming.contracts.model import (
    CalibrationSnapshot,
    CalibrationSolution,
    CalibrationSolutionRef,
    CalibrationState,
    CalibrationUpdate,
    CameraFrame,
    CaptureTime,
    ConfigDigest,
    DiagnosticEvent,
    EventEnvelope,
    EventType,
    Observation2D,
    ProcessingTime,
    RayEvent,
    SessionId,
    StationConfig,
    StationId,
)
from chongming.modules.base import ModuleShell
from chongming.ports.device import (
    AuxiliaryCalibrator,
    CalibrationEstimator,
    CalibrationHistory,
    CalibrationStateManager,
    EventSequencer,
    MainDetector,
    MotionDetector,
    RayBuilder,
    ReferenceRecognizer,
    Tracker2D,
)

_TODO = "TODO(stage-3): business logic is intentionally absent from the architecture skeleton"


@dataclass(frozen=True, kw_only=True)
class EventSequencerModule(ModuleShell):
    station_id: StationId
    station_session_id: SessionId
    schema_version: str

    def next_envelope(self, event_type: EventType, config_digest: ConfigDigest) -> EventEnvelope:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class MotionDetectionModule(ModuleShell):
    policy: DetectionPolicy

    def detect(self, frame: CameraFrame) -> Candidate2DBatch:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class Tracking2DModule(ModuleShell):
    station_id: StationId
    station_session_id: SessionId
    policy: Tracking2DPolicy

    def update(self, candidates: Candidate2DBatch) -> Tracking2DResult:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class MainDetectionModule(ModuleShell):
    detector: MotionDetector
    tracker: Tracker2D

    def process(self, frame: CameraFrame) -> MainDetectionResult:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class ReferenceRecognitionModule(ModuleShell):
    policy: CalibrationPolicy

    def recognize(self, frame: CameraFrame, config: StationConfig) -> ReferenceObservationBatch:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class CalibrationEstimationModule(ModuleShell):
    policy: CalibrationPolicy

    def estimate(
        self, references: ReferenceObservationBatch, config: StationConfig, now: ProcessingTime
    ) -> CalibrationAttempt:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class CalibrationHistoryModule(ModuleShell):
    station_id: StationId
    station_session_id: SessionId

    def record_solution(self, solution: CalibrationSolution) -> None:
        raise NotImplementedError(_TODO)

    def record_state(self, state: CalibrationState) -> None:
        raise NotImplementedError(_TODO)

    def lookup_solution(self, ref: CalibrationSolutionRef) -> CalibrationSolution | None:
        raise NotImplementedError(_TODO)

    def snapshot_at(self, capture_time: CaptureTime) -> CalibrationSnapshot:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class CalibrationStateModule(ModuleShell):
    policy: CalibrationPolicy
    history: CalibrationHistory
    sequencer: EventSequencer

    def consider(self, attempt: CalibrationAttempt, config: StationConfig) -> CalibrationUpdate:
        raise NotImplementedError(_TODO)

    def tick(self, now: ProcessingTime, config: StationConfig) -> CalibrationUpdate | None:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class AuxiliaryCalibrationModule(ModuleShell):
    recognizer: ReferenceRecognizer
    estimator: CalibrationEstimator
    states: CalibrationStateManager

    def process(
        self, frame: CameraFrame, config: StationConfig, now: ProcessingTime
    ) -> CalibrationUpdate:
        raise NotImplementedError(_TODO)

    def tick(self, now: ProcessingTime, config: StationConfig) -> CalibrationUpdate | None:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class RayGenerationModule(ModuleShell):
    policy: RayGenerationPolicy
    sequencer: EventSequencer

    def build(
        self, observation: Observation2D, config: StationConfig, calibration: CalibrationSnapshot
    ) -> RayEvent | DiagnosticEvent:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class DeviceModule(ModuleShell):
    config: StationConfig
    detector: MainDetector
    calibrator: AuxiliaryCalibrator
    history: CalibrationHistory
    ray_builder: RayBuilder
    sequencer: EventSequencer

    def process_main(self, frame: CameraFrame, now: ProcessingTime) -> DeviceFrameResult:
        raise NotImplementedError(_TODO)

    def process_auxiliary(self, frame: CameraFrame, now: ProcessingTime) -> CalibrationUpdate:
        raise NotImplementedError(_TODO)

    def tick(self, now: ProcessingTime) -> CalibrationUpdate | None:
        raise NotImplementedError(_TODO)
