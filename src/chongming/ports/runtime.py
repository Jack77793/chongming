"""Orchestration is privileged; transport receives online events only."""

from __future__ import annotations

from typing import Protocol

from chongming.contracts.model import ProcessingTime
from chongming.contracts.runtime import (
    EventPath,
    EventSubmission,
    ProgressSubmission,
    RunManifest,
    RunSummary,
    ShutdownReport,
    StartupReport,
    SystemStepResult,
    TransportBatch,
    TransportReceipt,
)
from chongming.contracts.scene import (
    OnlineEvaluationData,
    ScenarioSpec,
    ScenarioStep,
    ScenarioTruth,
)


class EventTransport(Protocol):
    def submit(self, submission: EventSubmission) -> TransportReceipt:
        """Transport ordinary events; enforce bounded storage and explicit drop evidence."""
        ...

    def submit_progress(self, submission: ProgressSubmission) -> None:
        """Deliver completed/ended stream progress after its events, also for empty frames."""
        ...

    def collect(self, path: EventPath, now: ProcessingTime) -> TransportBatch:
        """Apply communication perturbations at this boundary; never mutate solver output."""
        ...


class EvidenceWriter(Protocol):
    def begin(self, manifest: RunManifest) -> None:
        """Keep spec, seed, versions and clock/policy configuration; no input replay API."""
        ...

    def record_online(self, data: OnlineEvaluationData) -> None: ...

    def record_truth(self, truth: ScenarioTruth) -> None:
        """Write privileged evidence separately from online dispatch."""
        ...

    def finish(self, summary: RunSummary) -> None: ...


class SystemRunner(Protocol):
    def start(self, manifest: RunManifest) -> StartupReport:
        """Initialize every module in one group, register configs, wait on one barrier."""
        ...

    def step(self, step: ScenarioStep, now: ProcessingTime) -> SystemStepResult:
        """Run both branches, dispatch one to center; truth only to evaluation/monitoring."""
        ...

    def run(self, spec: ScenarioSpec) -> RunSummary:
        """From the scenario source, drive shared time; finalize and stop on all exit paths."""
        ...

    def stop(self, now: ProcessingTime) -> ShutdownReport:
        """Drain transport, report stream ends, seal final windows, finalize and clean up."""
        ...
