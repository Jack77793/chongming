"""Clean online step boundary. No generation parameters or correct answers."""

from dataclasses import dataclass

from chongming.contracts.fusion import StationEventTimeProgress
from chongming.contracts.model import (
    CalibrationSolution,
    ProcessingTime,
    ReceivedEvent,
    StationConfig,
    VoxelSearchSpec,
)


@dataclass(frozen=True, kw_only=True)
class CenterRegistration:
    stations: tuple[StationConfig, ...]
    search: VoxelSearchSpec
    clock_domain_id: str
    center_session_id: str


@dataclass(frozen=True, kw_only=True)
class CenterInputStep:
    step_no: int
    time: ProcessingTime
    solutions: tuple[CalibrationSolution, ...]
    events: tuple[ReceivedEvent, ...]
    progress: tuple[StationEventTimeProgress, ...]
    stream_ended: bool
