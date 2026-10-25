"""Scenario source abstraction for live rendering and playback."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from chongming.contracts.scene import ScenarioSpec, ScenarioStep


class ScenarioSource(Protocol):
    """Abstract source of scenario steps for preview and evaluation.

    Implementations provide either live-rendered steps from a scene definition
    or pre-recorded steps from a recording package.  The consumer only sees
    the unified ScenarioStep interface and does not need to know the origin.
    """

    def get_spec(self) -> ScenarioSpec:
        """Return the immutable scenario specification."""
        ...

    def generate(self, spec: ScenarioSpec) -> Iterator[ScenarioStep]:
        """Yield scenario steps in temporal order until the scenario ends.

        Args:
            spec: Scenario specification (may be ignored by playback sources).
        """
        ...
