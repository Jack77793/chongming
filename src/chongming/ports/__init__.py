"""Stable module boundaries; implementations belong to stage 3."""

from .interfaces import (
    AuxiliaryCalibrator,
    Evaluator,
    FusionEngine,
    MainDetector,
    RayBuilder,
    ScenarioSource,
)

__all__ = [
    "AuxiliaryCalibrator",
    "Evaluator",
    "FusionEngine",
    "MainDetector",
    "RayBuilder",
    "ScenarioSource",
]
