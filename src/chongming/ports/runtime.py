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
    TransportDrainResult,
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
        """Deliver completed/ended stream progress after its events, also for empty frames.

        Retain terminal progress until preceding submissions are accounted for;
        normal progress perturbations cannot silently lose stream termination.
        """
        ...

    def collect(self, path: EventPath, now: ProcessingTime) -> TransportBatch:
        """Apply communication perturbations at this boundary; never mutate solver output."""
        ...

    def drain(
        self, path: EventPath, now: ProcessingTime, deadline: ProcessingTime
    ) -> TransportDrainResult:
        """Finish a closed path in order, including explicit deadline discard evidence.

        Stop new submissions first. now/deadline use one processing clock; return
        the effective completion time, last deliveries and terminal progress. Do
        not infer exposure times or call an empty collect result fully drained.
        """
        ...


class EvidenceWriter(Protocol):
    def begin(self, manifest: RunManifest) -> None:
        """Keep spec, seed, versions and clock/policy configuration; no input replay API."""
        ...

    def record_online(self, data: OnlineEvaluationData) -> None: ...

    def record_transport(self, batch: TransportBatch) -> None:
        """Persist delivery/progress/receipt evidence for both paths, including drops."""
        ...

    def record_drain(self, result: TransportDrainResult) -> None:
        """Persist final deliveries, deadline discards and pending state on failure."""
        ...

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
        """Close both paths, submit stream ends, drain, seal, finalize and clean up.

        Use TransportPolicy.drain_timeout_ns as the finite deadline; record all
        delivery/discard outcomes before finalization. Only the selected path's
        terminal progress reaches fusion. Zero-frame ends carry no fake exposure.
        Cleanup still runs if draining fails, with pending state in the evidence.
        """
        ...
