"""Privileged generation/evaluation ports and passive development monitoring.

The online device/center ports live elsewhere and cannot accept scene truth.
The logical scene owns projection and finite-segment occlusion; the imager
consumes its state and never owns the simulation clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from chongming.contracts.model import Vec3M
from chongming.contracts.scene import (
    CameraStateTruth,
    EvaluationRecord,
    EvaluationReport,
    ExperimentPerturbations,
    ImageGenerationRequest,
    ImagingBatch,
    KinematicState,
    LandmarkVisibilityTruth,
    LogicalSceneSnapshot,
    MonitoringFrame,
    MonitoringSnapshot,
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


class ExperimentClock(Protocol):
    def initialize(self, timeline: TimelineSpec) -> None:
        """Start a seeded experiment time domain independently of UI/wall time."""
        ...

    def read(self) -> SimulationTime:
        """Read the current time without advancing it."""
        ...

    def advance_to(self, time_ns: int) -> SimulationTime:
        """The scenario driver alone advances the shared experiment clock."""
        ...


class TrajectorySampler(Protocol):
    def sample(self, trajectory: TrajectorySpec, time: SimulationTime) -> KinematicState:
        """Sample an explicitly configured trajectory on the experiment clock."""
        ...


class CameraProjector(Protocol):
    def project(self, camera: CameraStateTruth, reference_point_enu_m: Vec3M) -> ProjectionTruth:
        """Use actual generating optics and optical pose; no estimated station config."""
        ...


class VisibilityResolver(Protocol):
    def resolve(self, query: VisibilityQuery) -> TargetVisibilityTruth:
        """Test center-in-FOV and occlusion on the finite camera-to-center segment."""
        ...

    def resolve_reference(self, query: ReferenceVisibilityQuery) -> LandmarkVisibilityTruth:
        """Use the finite segment; a landmark's own terminal surface contact is allowed."""
        ...


class SceneLogic(Protocol):
    def initialize(
        self,
        scene: SceneDefinition,
        perturbations: ExperimentPerturbations,
        random_seed: int,
    ) -> None:
        """Load logical world/actual cameras; do not import a renderer or open a window."""
        ...

    def sample(self, time: SimulationTime) -> LogicalSceneSnapshot:
        """Produce kinematics/projection/visibility without advancing the clock."""
        ...


class CameraImager(Protocol):
    def render(self, request: ImageGenerationRequest) -> ImagingBatch:
        """Render main/auxiliary pixels from authoritative exposure samples."""
        ...


class VirtualObservationGenerator(Protocol):
    def generate(self, request: VirtualObservationRequest) -> VirtualObservationBatch:
        """Emit ordinary virtual online events and a separate privileged ID mapping."""
        ...


class ScenarioSource(Protocol):
    def generate(self, spec: ScenarioSpec) -> Iterable[ScenarioStep]:
        """Generate both branches with fresh, separate image/virtual sessions per run."""
        ...


class Evaluator(Protocol):
    def observe(
        self, truth: ScenarioTruth, online: OnlineEvaluationData
    ) -> tuple[EvaluationRecord, ...]:
        """Compare layered truth with online records and explicit error strata."""
        ...

    def finalize(self, scenario_id: str, random_seed: int) -> EvaluationReport:
        """Finalize simulation metrics; unsupported metrics remain explicitly undefined."""
        ...


class MonitorRenderer(Protocol):
    def render(self, snapshot: MonitoringSnapshot) -> MonitoringFrame:
        """Render a supplied immutable snapshot; never advance/sample the experiment."""
        ...
