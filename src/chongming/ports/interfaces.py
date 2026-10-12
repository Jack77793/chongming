"""Port contracts for the three post-scenario parallel development lines."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

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


class ScenarioSource(Protocol):
    def generate(self, spec: ScenarioSpec) -> Iterable[ScenarioStep]:
        """Produce one run from a scenario spec; no replay input is accepted."""
        ...


class MainDetector(Protocol):
    def process(self, frame: CameraFrame) -> DetectionBatch:
        """Return processed observations or an explicit processing failure."""
        ...


class AuxiliaryCalibrator(Protocol):
    def process(self, frame: CameraFrame, config: StationConfig) -> CalibrationState:
        """Produce a calibration state; implementation owns state transitions."""
        ...


class RayBuilder(Protocol):
    def build(
        self,
        envelope: EventEnvelope,
        observation: Observation2D,
        config: StationConfig,
        calibration: CalibrationState,
    ) -> RayEvent | DiagnosticEvent:
        """Build an event or a diagnostic; never use a placeholder ray."""
        ...


class FusionEngine(Protocol):
    def register_station(self, config: StationConfig) -> None:
        """Register the versioned online station configuration."""
        ...

    def register_calibration(self, state: CalibrationState) -> None:
        """Register a trustworthy solution or a health-state transition."""
        ...

    def process(
        self,
        events: Iterable[RayEvent | DiagnosticEvent],
        search: VoxelSearchSpec,
    ) -> tuple[tuple[FusionMeasurement, ...], tuple[TrackEvent, ...]]:
        """Consume online events and return measurements plus track updates."""
        ...


class Evaluator(Protocol):
    def observe(self, truth: ScenarioTruth, online: OnlineEvaluationData) -> None:
        """Consume truth only on the evaluation side."""
        ...
