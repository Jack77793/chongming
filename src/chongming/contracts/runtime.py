"""Complete-run lifecycle, routing and evidence declarations; no replay contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from chongming.contracts.device import DeviceFrameResult, DevicePolicy
from chongming.contracts.fusion import (
    EventClockMappingSpec,
    FusionBatchResult,
    FusionPolicy,
    StationEventTimeProgress,
)
from chongming.contracts.model import (
    CalibrationUpdate,
    ConfigDigest,
    EventId,
    ProcessingTime,
    ReceivedEvent,
    SessionId,
    StationEvent,
    StationId,
    StationSessionBinding,
)
from chongming.contracts.scene import (
    EvaluationReport,
    EvaluationSpec,
    MonitoringSpec,
    ScenarioSpec,
    SimulationTime,
)


class EventPath(StrEnum):
    IMAGE_PIPELINE = "image_pipeline"
    VIRTUAL_RAYS = "virtual_rays"


class ModuleState(StrEnum):
    DECLARED = "declared"
    INITIALIZING = "initializing"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class OverflowAction(StrEnum):
    REJECT_AND_REPORT = "reject_and_report"
    DROP_OLDEST_AND_REPORT = "drop_oldest_and_report"


@dataclass(frozen=True, kw_only=True)
class TransportPolicy:
    maximum_pending_events: int
    overflow_action: OverflowAction
    drain_timeout_ns: int


@dataclass(frozen=True, kw_only=True)
class StationRuntimeSpec:
    sessions: StationSessionBinding
    device_policy: DevicePolicy


@dataclass(frozen=True, kw_only=True)
class RunSpec:
    """No module enable flags: all modules are initialized and run in every route.

    center_input selects evidence only; it is not a startup/dependency switch.
    IDs are allocated fresh before assembly; configuration and seed are reproducible,
    but reusing a seed never reuses a station or center session.
    """

    run_id: str
    center_session_id: SessionId
    stations: tuple[StationRuntimeSpec, ...]
    schema_version: str
    center_input: EventPath
    fusion_policy: FusionPolicy
    processing_to_capture_clock: EventClockMappingSpec
    transport_policy: TransportPolicy
    evaluation: EvaluationSpec
    monitoring: MonitoringSpec
    artifacts_directory: Path


@dataclass(frozen=True, kw_only=True)
class ModuleNode:
    """Construction dependencies are not staggered module activation stages."""

    module_key: str
    dependencies: tuple[str, ...]
    station_id: StationId | None


@dataclass(frozen=True, kw_only=True)
class StartupPlan:
    """One group, one readiness barrier, then frames/events may be dispatched.

    Every declared node must initialize. A calibration solution or a detection is
    data readiness, never a module-start prerequisite. Initialization failure aborts
    the whole run and cleans up all initialized nodes; it cannot disable a branch.
    """

    modules: tuple[ModuleNode, ...]


@dataclass(frozen=True, kw_only=True)
class ModuleHealth:
    module_key: str
    state: ModuleState
    observed_at: ProcessingTime
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class StartupReport:
    run_id: str
    ready: bool
    modules: tuple[ModuleHealth, ...]


@dataclass(frozen=True, kw_only=True)
class ShutdownReport:
    run_id: str
    modules: tuple[ModuleHealth, ...]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class EventSubmission:
    """Path metadata is local routing context, absent from the center RayEvent."""

    path: EventPath
    event: StationEvent
    emitted_at: ProcessingTime


@dataclass(frozen=True, kw_only=True)
class TransportReceipt:
    event_id: EventId
    accepted: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ProgressSubmission:
    """Transport sets progress.received_at to its actual downstream delivery time."""

    path: EventPath
    progress: StationEventTimeProgress


@dataclass(frozen=True, kw_only=True)
class TransportBatch:
    path: EventPath
    received_events: tuple[ReceivedEvent, ...]
    completed_progress: tuple[StationEventTimeProgress, ...]
    receipts: tuple[TransportReceipt, ...]
    # Progress never overtakes the event deliveries it describes. Apply configured
    # delay/drop/duplicate policies to progress too, rather than bypassing transport.


@dataclass(frozen=True, kw_only=True)
class TransportDrainResult:
    """Account for delivery or discard; an empty collect batch is not completion.

    Terminal stream progress is retained until all preceding submissions are
    delivered or explicitly discarded. It is returned after those outcomes, even
    when ordinary progress is perturbed. discarded_events have accepted=False;
    each discarded progress record is preserved as evidence. complete requires
    both pending counts to be zero. Failure must expose remaining counts/reasons.
    """

    batch: TransportBatch
    completed_at: ProcessingTime
    discarded_events: tuple[TransportReceipt, ...]
    discarded_progress: tuple[StationEventTimeProgress, ...]
    pending_event_count: int
    pending_progress_count: int
    complete: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class RunManifest:
    run: RunSpec
    scenario: ScenarioSpec
    code_revision: str
    python_version: str
    dependency_lock_digest: str
    station_config_digests: tuple[tuple[StationId, ConfigDigest], ...]
    # Full privileged manifest goes to evidence storage, never online algorithms.


@dataclass(frozen=True, kw_only=True)
class SystemStepResult:
    time: SimulationTime
    device_frames: tuple[DeviceFrameResult, ...]
    calibration_updates: tuple[CalibrationUpdate, ...]
    transport: tuple[TransportBatch, ...]
    fusion: tuple[FusionBatchResult, ...]
    health: tuple[ModuleHealth, ...]


@dataclass(frozen=True, kw_only=True)
class RunSummary:
    run_id: str
    evaluation: EvaluationReport
    shutdown: ShutdownReport
    artifacts_directory: Path
    reason_codes: tuple[str, ...]
