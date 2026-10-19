"""Online device ports. No scene-generation or evaluation truth types here."""

from __future__ import annotations

from typing import Protocol

from chongming.contracts.device import (
    CalibrationAttempt,
    Candidate2DBatch,
    DeviceFrameResult,
    MainDetectionResult,
    ReferenceObservationBatch,
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
    StationConfig,
)


class EventSequencer(Protocol):
    def next_envelope(self, event_type: EventType, config_digest: ConfigDigest) -> EventEnvelope:
        """One sequence for ray, calibration and diagnostic events in this session."""
        ...


class MotionDetector(Protocol):
    def detect(self, frame: CameraFrame) -> Candidate2DBatch:
        """MAIN only; distinguish processed-empty frames from processing failures."""
        ...


class Tracker2D(Protocol):
    def update(self, candidates: Candidate2DBatch) -> Tracking2DResult:
        """Retain local history, including missed frames; IDs are station-session scoped.

        Only this frame's measured candidates may become observations. Predictions
        and coasting remain track states and never become measured rays.
        """
        ...


class MainDetector(Protocol):
    def process(self, frame: CameraFrame) -> MainDetectionResult:
        """Detect and track; a negative frame remains an explicit completed batch."""
        ...


class ReferenceRecognizer(Protocol):
    def recognize(self, frame: CameraFrame, config: StationConfig) -> ReferenceObservationBatch:
        """AUXILIARY only; initial-image and surveyed references share this boundary."""
        ...


class CalibrationEstimator(Protocol):
    def estimate(
        self, references: ReferenceObservationBatch, config: StationConfig, now: ProcessingTime
    ) -> CalibrationAttempt:
        """Estimate low-frequency common rotation, respecting fixed main/aux extrinsics."""
        ...


class CalibrationHistory(Protocol):
    def record_solution(self, solution: CalibrationSolution) -> None:
        """Retain immutable solutions and reject conflicting reuse of an ID."""
        ...

    def record_state(self, state: CalibrationState) -> None:
        """Retain time-addressed health history; no implicit latest-only overwrite."""
        ...

    def lookup_solution(self, ref: CalibrationSolutionRef) -> CalibrationSolution | None: ...

    def snapshot_at(self, capture_time: CaptureTime) -> CalibrationSnapshot:
        """Select only state/solutions known as of exposure; never wait for an auxiliary frame.

        Source and acceptance times and health observation time must not be later
        than main exposure midpoint. Missing or incompatible history yields no usable
        solution, not baseline-as-fresh. Compare clocks through manifest mappings.
        """
        ...


class CalibrationStateManager(Protocol):
    def consider(self, attempt: CalibrationAttempt, config: StationConfig) -> CalibrationUpdate:
        """Accept increasing reference times; failure freezes an existing trusted solution.

        Recovery is gated by configured success/quality rules. A health-only change
        keeps solution ID and last trusted time. With no trusted solution send diagnostics.
        """
        ...

    def tick(self, now: ProcessingTime, config: StationConfig) -> CalibrationUpdate | None:
        """Report overdue failures/staleness; between scheduled updates remains valid."""
        ...


class AuxiliaryCalibrator(Protocol):
    def process(
        self, frame: CameraFrame, config: StationConfig, now: ProcessingTime
    ) -> CalibrationUpdate: ...

    def tick(self, now: ProcessingTime, config: StationConfig) -> CalibrationUpdate | None: ...


class RayBuilder(Protocol):
    def build(
        self, observation: Observation2D, config: StationConfig, calibration: CalibrationSnapshot
    ) -> RayEvent | DiagnosticEvent:
        """Use the exposure-compatible solution; invalid geometry/unknown error => diagnostic.

        Propagate independent pixel/origin errors separately from shared installation
        and correction uncertainty. Do not invent probabilities or placeholder rays.
        """
        ...


class DevicePipeline(Protocol):
    """Reject station/session/role/camera-config mismatches with explicit diagnostics.

    Frame references must match the bound station, active image session and camera
    configuration. A mismatched frame cannot update detections or calibration history.
    """

    def process_main(self, frame: CameraFrame, now: ProcessingTime) -> DeviceFrameResult:
        """Process without waiting for current auxiliary output; complete negative frames too."""
        ...

    def process_auxiliary(self, frame: CameraFrame, now: ProcessingTime) -> CalibrationUpdate:
        """The independent lower-rate auxiliary stream updates retained calibration history."""
        ...

    def tick(self, now: ProcessingTime) -> CalibrationUpdate | None:
        """Advance health checks when no auxiliary sample is available."""
        ...
