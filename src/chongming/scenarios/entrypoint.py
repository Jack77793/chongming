"""The sole complete-system run declaration; execution remains stage-3 work."""

from __future__ import annotations

from chongming.composition import ArchitectureAssembly
from chongming.contracts.runtime import RunManifest, RunSummary
from chongming.contracts.scene import ScenarioSpec


def declare_run(
    spec: ScenarioSpec, manifest: RunManifest, assembly: ArchitectureAssembly
) -> RunSummary:
    """Validate configuration, start every binding, then drive the scenario source.

    Register estimated configs before dispatch. Both image and virtual branches
    run; route only the selected event stream and its matching calibration registry
    snapshots/progress to the center. Send rejected/late events to evidence/evaluation.
    Negative frames still report progress; no camera sample still calls device/center
    ticks. Advance simulation only here through ScenarioSource, never on monitor
    refresh. On end/failure close both paths, submit terminal progress (no invented
    exposure for zero-frame stations), then drain with a finite policy deadline.
    Account for all deliveries/discards, seal final windows, finalize and stop all
    initialized nodes, including after drain failure.
    """

    raise NotImplementedError("TODO(stage-3): implement scenario-driven complete-system execution")
