"""Non-running stage-2 scenario, imaging, evaluation and monitor shells.

Constructors declare dependencies only. Every business operation deliberately
raises; these shells do not generate substitute pixels, observations or metrics.
"""

from __future__ import annotations

from collections.abc import Iterable

from chongming.contracts.model import StationSessionBinding, Vec3M
from chongming.contracts.scene import (
    CameraStateTruth,
    EvaluationRecord,
    EvaluationReport,
    EvaluationSpec,
    ExperimentPerturbations,
    ImageGenerationRequest,
    ImagingBatch,
    KinematicState,
    LandmarkVisibilityTruth,
    LogicalSceneSnapshot,
    MonitoringFrame,
    MonitoringSnapshot,
    MonitoringSpec,
    OnlineEvaluationData,
    ProjectionTruth,
    ReferenceVisibilityQuery,
    ScenarioSpec,
    ScenarioStep,
    ScenarioTruth,
    SceneDefinition,
    SimulationTime,
    TargetVisibilityTruth,
    TimelineSpec,
    TrajectorySpec,
    VirtualObservationBatch,
    VirtualObservationRequest,
    VisibilityQuery,
)
from chongming.modules.base import ModuleShell
from chongming.ports.device import EventSequencer
from chongming.ports.scene import (
    CameraImager,
    CameraProjector,
    ExperimentClock,
    SceneLogic,
    TrajectorySampler,
    VirtualObservationGenerator,
    VisibilityResolver,
)

_TODO = "TODO(stage-3): business logic is intentionally absent from the architecture skeleton"


class ExperimentClockModule(ModuleShell):
    def initialize(self, timeline: TimelineSpec) -> None:
        raise NotImplementedError(_TODO)

    def read(self) -> SimulationTime:
        raise NotImplementedError(_TODO)

    def advance_to(self, time_ns: int) -> SimulationTime:
        raise NotImplementedError(_TODO)


class TrajectoryModule(ModuleShell):
    def sample(self, trajectory: TrajectorySpec, time: SimulationTime) -> KinematicState:
        raise NotImplementedError(_TODO)


class CameraProjectionModule(ModuleShell):
    def project(self, camera: CameraStateTruth, reference_point_enu_m: Vec3M) -> ProjectionTruth:
        raise NotImplementedError(_TODO)


class VisibilityModule(ModuleShell):
    def resolve(self, query: VisibilityQuery) -> TargetVisibilityTruth:
        raise NotImplementedError(_TODO)

    def resolve_reference(self, query: ReferenceVisibilityQuery) -> LandmarkVisibilityTruth:
        raise NotImplementedError(_TODO)


class SceneLogicModule(ModuleShell):
    def __init__(
        self,
        trajectory_sampler: TrajectorySampler,
        projector: CameraProjector,
        visibility: VisibilityResolver,
    ) -> None:
        self.trajectory_sampler = trajectory_sampler
        self.projector = projector
        self.visibility = visibility

    def initialize(
        self,
        scene: SceneDefinition,
        perturbations: ExperimentPerturbations,
        random_seed: int,
    ) -> None:
        raise NotImplementedError(_TODO)

    def sample(self, time: SimulationTime) -> LogicalSceneSnapshot:
        raise NotImplementedError(_TODO)


class CameraImagingModule(ModuleShell):
    def render(self, request: ImageGenerationRequest) -> ImagingBatch:
        raise NotImplementedError(_TODO)


class VirtualObservationModule(ModuleShell):
    """Each sequencer owns one station's fresh virtual event session."""

    def __init__(self, sequencers: tuple[EventSequencer, ...]) -> None:
        self.sequencers = sequencers

    def generate(self, request: VirtualObservationRequest) -> VirtualObservationBatch:
        raise NotImplementedError(_TODO)


class ScenarioModule(ModuleShell):
    def __init__(
        self,
        clock: ExperimentClock,
        logic: SceneLogic,
        imager: CameraImager,
        virtual_generator: VirtualObservationGenerator,
        station_sessions: tuple[StationSessionBinding, ...],
    ) -> None:
        self.clock = clock
        self.logic = logic
        self.imager = imager
        self.virtual_generator = virtual_generator
        self.station_sessions = station_sessions

    def generate(self, spec: ScenarioSpec) -> Iterable[ScenarioStep]:
        raise NotImplementedError(_TODO)


class EvaluationModule(ModuleShell):
    def __init__(self, spec: EvaluationSpec) -> None:
        self.spec = spec

    def observe(
        self, truth: ScenarioTruth, online: OnlineEvaluationData
    ) -> tuple[EvaluationRecord, ...]:
        raise NotImplementedError(_TODO)

    def finalize(self, scenario_id: str, random_seed: int) -> EvaluationReport:
        raise NotImplementedError(_TODO)


class MonitoringModule(ModuleShell):
    def __init__(self, spec: MonitoringSpec) -> None:
        self.spec = spec

    def render(self, snapshot: MonitoringSnapshot) -> MonitoringFrame:
        raise NotImplementedError(_TODO)
