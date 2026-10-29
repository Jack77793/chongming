"""Typed module implementations and the remaining architecture declarations."""

from .device import (
    AuxiliaryCalibrationModule,
    CalibrationEstimationModule,
    CalibrationHistoryModule,
    CalibrationStateModule,
    DeviceModule,
    EventSequencerModule,
    MainDetectionModule,
    MotionDetectionModule,
    RayGenerationModule,
    ReferenceRecognitionModule,
    Tracking2DModule,
)
from .fusion import (
    CalibrationWeightingModule,
    DirectGeometryCandidateModule,
)
from .fusion_association import CrossStationAssociationModule
from .fusion_engine import CenterFusionModule
from .fusion_geometry import (
    CandidateGenerationModule,
    GeometryRefinementModule,
    VoxelCandidateModule,
)
from .fusion_ingress import (
    AdmissionJournalModule,
    CalibrationRegistryModule,
    EventAdmissionModule,
    EventClockMappingModule,
    EventWindowModule,
    StationRegistryModule,
)
from .fusion_tracking import CenterTrackingModule
from .runtime import (
    EventTransportModule,
    EvidenceWriterModule,
    SystemRunnerModule,
)
from .scene import (
    CameraImagingModule,
    CameraProjectionModule,
    EvaluationModule,
    ExperimentClockModule,
    MonitoringModule,
    ScenarioModule,
    SceneLogicModule,
    TrajectoryModule,
    VirtualObservationModule,
    VisibilityModule,
)

__all__ = [
    "AdmissionJournalModule",
    "AuxiliaryCalibrationModule",
    "CalibrationEstimationModule",
    "CalibrationHistoryModule",
    "CalibrationRegistryModule",
    "CalibrationStateModule",
    "CalibrationWeightingModule",
    "CameraImagingModule",
    "CameraProjectionModule",
    "CandidateGenerationModule",
    "CenterFusionModule",
    "CenterTrackingModule",
    "CrossStationAssociationModule",
    "DeviceModule",
    "DirectGeometryCandidateModule",
    "EvaluationModule",
    "EventAdmissionModule",
    "EventClockMappingModule",
    "EventSequencerModule",
    "EventTransportModule",
    "EventWindowModule",
    "EvidenceWriterModule",
    "ExperimentClockModule",
    "GeometryRefinementModule",
    "MainDetectionModule",
    "MonitoringModule",
    "MotionDetectionModule",
    "RayGenerationModule",
    "ReferenceRecognitionModule",
    "ScenarioModule",
    "SceneLogicModule",
    "StationRegistryModule",
    "SystemRunnerModule",
    "Tracking2DModule",
    "TrajectoryModule",
    "VirtualObservationModule",
    "VisibilityModule",
    "VoxelCandidateModule",
]
