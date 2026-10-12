"""Explicit stage-2 shells for the three parallel implementation lines."""

from __future__ import annotations

from collections.abc import Iterable

from chongming.contracts import (
    CalibrationState,
    CameraFrame,
    DetectionBatch,
    DiagnosticEvent,
    EventEnvelope,
    FusionMeasurement,
    Observation2D,
    OnlineEvaluationData,
    RayEvent,
    ScenarioSpec,
    ScenarioStep,
    ScenarioTruth,
    StationConfig,
    TrackEvent,
    VoxelSearchSpec,
)

_TODO = "TODO(stage-3): business logic is intentionally absent from the architecture skeleton"


class ScenarioModule:
    def generate(self, spec: ScenarioSpec) -> Iterable[ScenarioStep]:
        raise NotImplementedError(_TODO)


class MainDetectionModule:
    def process(self, frame: CameraFrame) -> DetectionBatch:
        raise NotImplementedError(_TODO)


class AuxiliaryCalibrationModule:
    def process(self, frame: CameraFrame, config: StationConfig) -> CalibrationState:
        raise NotImplementedError(_TODO)


class RayGenerationModule:
    def build(
        self,
        envelope: EventEnvelope,
        observation: Observation2D,
        config: StationConfig,
        calibration: CalibrationState,
    ) -> RayEvent | DiagnosticEvent:
        raise NotImplementedError(_TODO)


class CenterFusionModule:
    def register_station(self, config: StationConfig) -> None:
        raise NotImplementedError(_TODO)

    def register_calibration(self, state: CalibrationState) -> None:
        raise NotImplementedError(_TODO)

    def process(
        self,
        events: Iterable[RayEvent | DiagnosticEvent],
        search: VoxelSearchSpec,
    ) -> tuple[tuple[FusionMeasurement, ...], tuple[TrackEvent, ...]]:
        raise NotImplementedError(_TODO)


class EvaluationModule:
    def observe(self, truth: ScenarioTruth, online: OnlineEvaluationData) -> None:
        raise NotImplementedError(_TODO)
