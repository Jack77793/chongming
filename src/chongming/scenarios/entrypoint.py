"""Scenario run declaration. There is no replay or data-generation shortcut."""

from __future__ import annotations

from chongming.composition import ArchitectureAssembly
from chongming.contracts import ScenarioSpec


def declare_run(spec: ScenarioSpec, assembly: ArchitectureAssembly) -> None:
    """Declare the future full-chain run from a scenario spec and seed."""

    del spec, assembly
    raise NotImplementedError("TODO(stage-3): implement scenario-driven execution")
