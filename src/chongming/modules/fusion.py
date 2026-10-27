"""Constructor-wired localization and tracking stages, pending implementation.

Both candidate strategies and all pipeline stages are constructed at startup.
Configuration selects how a stage processes evidence, not whether it exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from chongming.contracts.fusion import (
    AdmittedRay,
    AssociationBatch,
    AssociationPolicy,
    CalibrationUsageRule,
    CandidateBatch,
    CandidatePolicy,
    EventClockReading,
    FusionBatchResult,
    FusionPolicy,
    GeometryBatch,
    GeometryPolicy,
    RayGeometryWeight,
    RegistryWriteResult,
    SealedObservationWindow,
    StationEventTimeProgress,
    TrackingPolicy,
    TrackPrediction,
)
from chongming.contracts.model import (
    CalibrationSolution,
    FusionMeasurement,
    ProcessingTime,
    ReceivedEvent,
    SessionId,
    StationConfig,
    TrackEvent,
    VoxelSearchSpec,
    WorldFrameId,
)
from chongming.modules.base import ModuleShell
from chongming.ports.fusion import (
    CalibrationRegistry,
    CandidateGenerator,
    CenterTracker,
    CrossStationAssociator,
    EventAdmission,
    EventClockMapping,
    EventWindows,
    GeometryRefiner,
    GeometryWeighting,
    SpatialCandidateStrategy,
    StationRegistry,
)

_TODO = "TODO(stage-3): business logic is intentionally absent from the architecture skeleton"


@dataclass(frozen=True, kw_only=True)
class VoxelCandidateModule(ModuleShell):
    """Vote only on forward ray/search-box intersection, per independent station."""

    policy: CandidatePolicy

    def propose(
        self,
        window: SealedObservationWindow,
        predictions: tuple[TrackPrediction, ...],
    ) -> CandidateBatch:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class DirectGeometryCandidateModule(ModuleShell):
    """Always constructed comparison strategy; no presumed optimal candidate route."""

    search: VoxelSearchSpec

    def propose(
        self,
        window: SealedObservationWindow,
        predictions: tuple[TrackPrediction, ...],
    ) -> CandidateBatch:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class CandidateGenerationModule(ModuleShell):
    policy: CandidatePolicy
    voxel: SpatialCandidateStrategy
    direct: SpatialCandidateStrategy

    def generate(
        self,
        window: SealedObservationWindow,
        predictions: tuple[TrackPrediction, ...],
    ) -> CandidateBatch:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class CrossStationAssociationModule(ModuleShell):
    policy: AssociationPolicy

    def associate(
        self,
        window: SealedObservationWindow,
        candidates: CandidateBatch,
        predictions: tuple[TrackPrediction, ...],
    ) -> AssociationBatch:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class CalibrationWeightingModule(ModuleShell):
    rules: tuple[CalibrationUsageRule, ...]

    def weight(self, ray: AdmittedRay) -> RayGeometryWeight:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class GeometryRefinementModule(ModuleShell):
    center_session_id: SessionId
    policy: GeometryPolicy
    weights: GeometryWeighting

    def refine(
        self,
        window: SealedObservationWindow,
        association: AssociationBatch,
        predictions: tuple[TrackPrediction, ...],
    ) -> GeometryBatch:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class CenterTrackingModule(ModuleShell):
    center_session_id: SessionId
    clock_domain_id: str
    world_frame_id: WorldFrameId
    policy: TrackingPolicy

    def predict(self, clock_domain_id: str, reference_time_ns: int) -> tuple[TrackPrediction, ...]:
        raise NotImplementedError(_TODO)

    def update(
        self,
        measurements: tuple[FusionMeasurement, ...],
        clock_domain_id: str,
        event_time_ns: int,
    ) -> tuple[TrackEvent, ...]:
        raise NotImplementedError(_TODO)

    def advance(self, event_clock: EventClockReading) -> tuple[TrackEvent, ...]:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class CenterFusionModule(ModuleShell):
    """Own ingress-to-track orchestration with an always-present full pipeline."""

    center_session_id: SessionId
    policy: FusionPolicy
    stations: StationRegistry
    calibrations: CalibrationRegistry
    admission: EventAdmission
    windows: EventWindows
    candidates: CandidateGenerator
    association: CrossStationAssociator
    geometry: GeometryRefiner
    tracker: CenterTracker
    clock: EventClockMapping

    def register_station(self, config: StationConfig) -> RegistryWriteResult:
        raise NotImplementedError(_TODO)

    def register_calibration(self, solution: CalibrationSolution) -> RegistryWriteResult:
        raise NotImplementedError(_TODO)

    def process(self, received: ReceivedEvent) -> FusionBatchResult:
        raise NotImplementedError(_TODO)

    def report_progress(self, progress: StationEventTimeProgress) -> FusionBatchResult:
        raise NotImplementedError(_TODO)

    def tick(self, now: ProcessingTime) -> FusionBatchResult:
        raise NotImplementedError(_TODO)
