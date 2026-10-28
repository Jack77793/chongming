"""Replaceable center stages, with storage and time mapping as explicit ports."""

from __future__ import annotations

from typing import Protocol

from chongming.contracts.fusion import (
    AdmissionOutcome,
    AdmissionRecord,
    AdmittedRay,
    AssociationBatch,
    CandidateBatch,
    EventClockReading,
    FusionBatchResult,
    GeometryBatch,
    RayGeometryWeight,
    RegistryWriteResult,
    SealedObservationWindow,
    StationEventTimeProgress,
    TrackPrediction,
    WindowUpdate,
)
from chongming.contracts.model import (
    CalibrationSolution,
    CalibrationSolutionRef,
    CalibrationState,
    CalibrationStateEvent,
    ConfigDigest,
    EventId,
    FusionMeasurement,
    ProcessingTime,
    ReceivedEvent,
    SessionId,
    StationConfig,
    StationEvent,
    StationId,
    TrackEvent,
)


class StationRegistry(Protocol):
    def register(self, config: StationConfig) -> RegistryWriteResult:
        """Retain immutable digest-addressed snapshots; reject conflicting content."""
        ...

    def lookup(self, station_id: StationId, digest: ConfigDigest) -> StationConfig | None:
        """No default position/orientation may replace a missing configuration."""
        ...

    def registered_stations(self) -> tuple[StationId, ...]:
        """Include stations that have not yet sent an observation."""
        ...


class CalibrationRegistry(Protocol):
    def register_solution(self, solution: CalibrationSolution) -> RegistryWriteResult:
        """Solutions have station/session/solution scope and a matching config."""
        ...

    def lookup_solution(self, ref: CalibrationSolutionRef) -> CalibrationSolution | None:
        """A restart cannot resolve a new-session reference to an old-session ID."""
        ...

    def record_state(self, event: CalibrationStateEvent) -> RegistryWriteResult:
        """Audit health transitions; do not overwrite old solutions or ray usage."""
        ...

    def state_history(
        self, station_id: StationId, station_session_id: SessionId
    ) -> tuple[CalibrationState, ...]:
        """Return exposure-relevant history rather than a mutable latest-only state."""
        ...


class AdmissionJournal(Protocol):
    def first_event(self, event_id: EventId) -> StationEvent | None:
        """Expose the original payload for exact deduplication and ID conflicts."""
        ...

    def record(self, record: AdmissionRecord) -> None:
        """Append each decision without replacing the original receive time."""
        ...

    def quarantined(self) -> tuple[AdmissionRecord, ...]:
        """Return unresolved records eligible for prerequisite reconsideration."""
        ...


class EventAdmission(Protocol):
    def admit(self, received: ReceivedEvent) -> AdmissionOutcome:
        """Validate transport, schema, time/frame, configuration, and ray evidence.

        Valid usage requires the referenced solution's exposure validity. Frozen
        and stale usage follow injected policy, retaining the original trusted
        solution rather than claiming a fresh solution or unconditional rejection.
        """
        ...

    def reconsider_quarantined(self, now: ProcessingTime) -> tuple[AdmissionOutcome, ...]:
        """Retry after registry updates; sealing may still make an accepted ray late."""
        ...


class EventClockMapping(Protocol):
    def map_processing_time(self, now: ProcessingTime) -> EventClockReading:
        """Use manifest origin/scale, never infer exposure time from arrival order."""
        ...


class EventWindows(Protocol):
    def offer(self, outcome: AdmissionOutcome) -> WindowUpdate:
        """Buffer accepted rays by exposure midpoint; record all other dispositions."""
        ...

    def report_progress(self, progress: StationEventTimeProgress) -> WindowUpdate:
        """Advance a processed empty batch without inventing an observation.

        When all registered streams end, seal through the last completed exposure
        or buffered ray window using STREAMS_ENDED, even under HOLD. Zero-frame,
        zero-ray runs do not fabricate windows; retain the finite final horizon.
        """
        ...

    def advance(self, now: ProcessingTime) -> WindowUpdate:
        """Check station health/deadlines and seal; late rays never reopen windows.

        Emit sealed empty windows through the same eligible horizon, allowing the
        tracker to consume time progress even when no accepted rays arrive.
        """
        ...


class SpatialCandidateStrategy(Protocol):
    def propose(
        self,
        window: SealedObservationWindow,
        predictions: tuple[TrackPrediction, ...],
    ) -> CandidateBatch:
        """Count independent stations and retain an unguided new-target entrance."""
        ...


class CandidateGenerator(Protocol):
    def generate(
        self,
        window: SealedObservationWindow,
        predictions: tuple[TrackPrediction, ...],
    ) -> CandidateBatch:
        """Select configured coarse/direct strategy; never output a final position."""
        ...


class CrossStationAssociator(Protocol):
    def associate(
        self,
        window: SealedObservationWindow,
        candidates: CandidateBatch,
        predictions: tuple[TrackPrediction, ...],
    ) -> AssociationBatch:
        """Use each EventId once and each station once per selected target/window."""
        ...


class GeometryWeighting(Protocol):
    def weight(self, ray: AdmittedRay) -> RayGeometryWeight:
        """Scale independent precision using event-time usage; zero excludes a ray.

        Shared installation/calibration uncertainty is not per-ray independent noise.
        """
        ...


class GeometryRefiner(Protocol):
    def refine(
        self,
        window: SealedObservationWindow,
        association: AssociationBatch,
        predictions: tuple[TrackPrediction, ...],
    ) -> GeometryBatch:
        """Joint robust continuous ray solution, time alignment, and degeneracy audit.

        Use original rays, not voxel centers or averaged pairwise midpoints. Report
        covariance conditional on the registered installation/calibration solutions.
        Motion-aligned groups require predictions at their supporting reference
        epoch; the returned dependence on the velocity prior is not independent
        position evidence for a second filter update.
        """
        ...


class CenterTracker(Protocol):
    def predict(self, clock_domain_id: str, reference_time_ns: int) -> tuple[TrackPrediction, ...]:
        """Read priors without mutating state; retain an unguided candidate path."""
        ...

    def update(
        self,
        measurements: tuple[FusionMeasurement, ...],
        clock_domain_id: str,
        event_time_ns: int,
    ) -> tuple[TrackEvent, ...]:
        """Maintain tentative -> confirmed -> coasting -> deleted session-local IDs.

        Consume one sealed batch exactly once with one-to-one assignments. Each
        measurement keeps its own reference time, after the previous consumed
        horizon and no later than event_time_ns, the current batch horizon.
        Ambiguous assignments do not update filters or create duplicate tracks;
        a publication is never retrospectively rewritten by a late arrival.
        """
        ...

    def advance(self, event_clock: EventClockReading) -> tuple[TrackEvent, ...]:
        """Advance missing-observation lifecycle even when no new rays are received.

        The effective event_clock is bounded by sealed windows already consumed
        in reference-time order. Mapping processing ``now`` is insufficient: it
        must not advance/delete filter state beyond a still-open window, discard
        that window, or pretend its measurements have already been consumed.
        """
        ...


class FusionEngine(Protocol):
    def register_station(self, config: StationConfig) -> RegistryWriteResult:
        """Explicit prerequisite; registration does not imply a fresh calibration."""
        ...

    def register_calibration(self, solution: CalibrationSolution) -> RegistryWriteResult:
        """Register a trustworthy versioned solution independently of health events."""
        ...

    def process(self, received: ReceivedEvent) -> FusionBatchResult:
        """Ingest one event; diagnostics/calibration transitions do not become rays."""
        ...

    def report_progress(self, progress: StationEventTimeProgress) -> FusionBatchResult:
        """Advance explicit device processing progress without a synthetic ray."""
        ...

    def tick(self, now: ProcessingTime) -> FusionBatchResult:
        """Reconsider quarantine, seal due windows, and advance track lifecycle.

        Constrain the tracker horizon to consumed sealed-window progress, including
        empty windows, instead of blindly advancing filter state to mapped now.
        """
        ...
