"""Lifecycle and transport shells; no worker, queue, driver or network is started."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chongming.contracts.model import ProcessingTime
from chongming.contracts.runtime import (
    EventPath,
    EventSubmission,
    ProgressSubmission,
    RunManifest,
    RunSpec,
    RunSummary,
    ShutdownReport,
    StartupPlan,
    StartupReport,
    SystemStepResult,
    TransportBatch,
    TransportDrainResult,
    TransportPolicy,
    TransportReceipt,
)
from chongming.contracts.scene import (
    CommunicationPerturbation,
    OnlineEvaluationData,
    ScenarioSpec,
    ScenarioStep,
    ScenarioTruth,
)
from chongming.modules.base import ModuleShell
from chongming.ports.device import DevicePipeline
from chongming.ports.fusion import FusionEngine
from chongming.ports.lifecycle import ModuleBinding
from chongming.ports.runtime import EventTransport, EvidenceWriter
from chongming.ports.scene import Evaluator, MonitorRenderer, ScenarioSource

_TODO = "TODO(stage-3): business logic is intentionally absent from the architecture skeleton"


@dataclass(frozen=True, kw_only=True)
class EventTransportModule(ModuleShell):
    policy: TransportPolicy
    perturbations: tuple[CommunicationPerturbation, ...]

    def submit(self, submission: EventSubmission) -> TransportReceipt:
        raise NotImplementedError(_TODO)

    def submit_progress(self, submission: ProgressSubmission) -> None:
        raise NotImplementedError(_TODO)

    def collect(self, path: EventPath, now: ProcessingTime) -> TransportBatch:
        raise NotImplementedError(_TODO)

    def drain(
        self, path: EventPath, now: ProcessingTime, deadline: ProcessingTime
    ) -> TransportDrainResult:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class EvidenceWriterModule(ModuleShell):
    directory: Path

    def begin(self, manifest: RunManifest) -> None:
        raise NotImplementedError(_TODO)

    def record_online(self, data: OnlineEvaluationData) -> None:
        raise NotImplementedError(_TODO)

    def record_transport(self, batch: TransportBatch) -> None:
        raise NotImplementedError(_TODO)

    def record_drain(self, result: TransportDrainResult) -> None:
        raise NotImplementedError(_TODO)

    def record_truth(self, truth: ScenarioTruth) -> None:
        raise NotImplementedError(_TODO)

    def finish(self, summary: RunSummary) -> None:
        raise NotImplementedError(_TODO)


@dataclass(frozen=True, kw_only=True)
class SystemRunnerModule:
    """Own startup and the three-exit router; it is not a managed module itself.

    ``step`` must send video only to devices, submit device and virtual events on
    their separate transport paths, and expose only the selected delivered path
    to fusion. Scenario truth is confined to evaluation, monitoring and evidence.
    """

    run_spec: RunSpec
    startup: StartupPlan
    bindings: tuple[ModuleBinding, ...]
    scenario: ScenarioSource
    devices: tuple[DevicePipeline, ...]
    fusion: FusionEngine
    transport: EventTransport
    evaluator: Evaluator
    monitor: MonitorRenderer
    evidence: EvidenceWriter

    def start(self, manifest: RunManifest) -> StartupReport:
        raise NotImplementedError(_TODO)

    def step(self, step: ScenarioStep, now: ProcessingTime) -> SystemStepResult:
        raise NotImplementedError(_TODO)

    def run(self, spec: ScenarioSpec) -> RunSummary:
        raise NotImplementedError(_TODO)

    def stop(self, now: ProcessingTime) -> ShutdownReport:
        raise NotImplementedError(_TODO)
