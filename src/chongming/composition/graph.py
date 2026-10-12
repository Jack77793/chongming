"""Declared dependency graph; assembly itself is a stage-3 concern."""

from __future__ import annotations

from dataclasses import dataclass

from chongming.ports import (
    AuxiliaryCalibrator,
    Evaluator,
    FusionEngine,
    MainDetector,
    RayBuilder,
    ScenarioSource,
)


@dataclass(frozen=True)
class ArchitectureAssembly:
    scenario: ScenarioSource
    detector: MainDetector
    calibrator: AuxiliaryCalibrator
    ray_builder: RayBuilder
    fusion: FusionEngine
    evaluator: Evaluator


def declare_assembly() -> ArchitectureAssembly:
    """Declare the required graph without constructing runtime implementations."""

    raise NotImplementedError("TODO(stage-3): bind concrete implementations")
