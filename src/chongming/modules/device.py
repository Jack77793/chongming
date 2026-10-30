"""Device dependencies, with calibration and ray business methods still deferred."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import count

from chongming.contracts.device import (
    CalibrationAttempt,
    CalibrationPolicy,
    DeviceFrameResult,
    RayGenerationPolicy,
    ReferenceObservationBatch,
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
from chongming.modules.detection import (
    MainDetectionModule,
    MotionDetectionModule,
    Tracking2DModule,
)
from chongming.ports.device import (
    AuxiliaryCalibrator,
    CalibrationEstimator,
    CalibrationHistory,
    CalibrationStateManager,
    EventSequencer,
    MainDetector,
    RayBuilder,
    ReferenceRecognizer,
)

__all__ = [
    "AuxiliaryCalibrationModule",
    "CalibrationEstimationModule",
    "CalibrationHistoryModule",
    "CalibrationStateModule",
    "DeviceModule",
    "EventSequencerModule",
    "MainDetectionModule",
    "MotionDetectionModule",
    "RayGenerationModule",
    "ReferenceRecognitionModule",
    "Tracking2DModule",
]

_TODO = "TODO(stage-3): business logic is intentionally absent from the architecture skeleton"


@dataclass(frozen=True, kw_only=True)
class EventSequencerModule(ModuleShell):
    business_implemented = True
    station_id: StationId
    station_session_id: SessionId
    schema_version: str
    _sequence: Iterator[int] = field(
        default_factory=lambda: count(1), init=False, repr=False, compare=False
    )

    def next_envelope(self, event_type: EventType, config_digest: ConfigDigest) -> EventEnvelope:
        if not self.schema_version:
            raise ValueError("event schema version cannot be empty")
        return EventEnvelope(
            schema_version=self.schema_version,
            event_type=event_type,
            station_id=self.station_id,
            station_session_id=self.station_session_id,
            sequence_no=next(self._sequence),
            station_config_digest=config_digest,
        )


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
