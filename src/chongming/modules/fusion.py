"""Pending direct-geometry comparison and uncertainty weighting stages."""

from dataclasses import dataclass

from chongming.contracts.fusion import (
    AdmittedRay,
    CalibrationUsageRule,
    CandidateBatch,
    RayGeometryWeight,
    SealedObservationWindow,
    TrackPrediction,
)
from chongming.contracts.model import VoxelSearchSpec
from chongming.modules.base import ModuleShell

_TODO = "Business method is not implemented"


@dataclass(frozen=True, kw_only=True)
class DirectGeometryCandidateModule(ModuleShell):
    search: VoxelSearchSpec

    def propose(
        self, window: SealedObservationWindow, predictions: tuple[TrackPrediction, ...]
    ) -> CandidateBatch:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class CalibrationWeightingModule(ModuleShell):
    rules: tuple[CalibrationUsageRule, ...]

    def weight(self, ray: AdmittedRay) -> RayGeometryWeight:
        raise NotImplementedError(_TODO)
