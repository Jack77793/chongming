"""Calibration usage weights and the pending direct-geometry comparison stage."""

import math
from dataclasses import dataclass

from chongming.contracts.fusion import (
    AdmittedRay,
    CalibrationAction,
    CalibrationUsageRule,
    CandidateBatch,
    RayGeometryWeight,
    SealedObservationWindow,
    TrackPrediction,
)
from chongming.contracts.model import CalibrationUsage, VoxelSearchSpec
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
    """Scale independent precision only; shared pose errors remain conditional."""

    business_implemented = True
    rules: tuple[CalibrationUsageRule, ...]

    def __post_init__(self) -> None:
        if (
            len(self.rules) != len(CalibrationUsage)
            or {rule.usage for rule in self.rules} != set(CalibrationUsage)
            or any(
                not math.isfinite(rule.geometry_weight_scale) or rule.geometry_weight_scale < 0
                for rule in self.rules
            )
        ):
            raise ValueError("invalid calibration weighting rules")

    def weight(self, ray: AdmittedRay) -> RayGeometryWeight:
        rule = next(item for item in self.rules if item.usage is ray.event.calibration_usage)
        envelope = ray.event.envelope
        return RayGeometryWeight(
            event_id=(envelope.station_id, envelope.station_session_id, envelope.sequence_no),
            weight_scale=rule.geometry_weight_scale
            if rule.action is CalibrationAction.ACCEPT
            else 0.0,
            reason_codes=(f"calibration_{rule.usage.value}_{rule.action.value}",),
        )
