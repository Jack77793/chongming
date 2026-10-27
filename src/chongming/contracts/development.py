"""Clean online step boundary. No generation parameters or correct answers."""

from dataclasses import dataclass

from chongming.contracts.fusion import (
    EventClockMappingSpec,
    FusionBatchResult,
    FusionPolicy,
    StationEventTimeProgress,
)
from chongming.contracts.model import (
    CalibrationSolution,
    FusionMeasurement,
    ProcessingTime,
    ReceivedEvent,
    StationConfig,
    TrackEvent,
)


@dataclass(frozen=True, kw_only=True)
class CenterRegistration:
    stations: tuple[StationConfig, ...]
    center_session_id: str
    policy: FusionPolicy
    clock_mapping: EventClockMappingSpec
    started_at: ProcessingTime


@dataclass(frozen=True, kw_only=True)
class CenterInputStep:
    step_no: int
    time: ProcessingTime
    solutions: tuple[CalibrationSolution, ...]
    events: tuple[ReceivedEvent, ...]
    progress: tuple[StationEventTimeProgress, ...]
    stream_ended: bool


@dataclass(frozen=True, kw_only=True)
class CenterAlgorithmResult:
    measurements: tuple[FusionMeasurement, ...]
    tracks: tuple[TrackEvent, ...]
    batches: tuple[FusionBatchResult, ...]
