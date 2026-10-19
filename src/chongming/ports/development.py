"""Volunteer algorithm hook; registration and input have online-only types."""

from typing import Protocol

from chongming.contracts.development import CenterInputStep, CenterRegistration
from chongming.contracts.model import FusionMeasurement, TrackEvent


class CenterAlgorithm(Protocol):
    def start(self, registration: CenterRegistration) -> None: ...

    def process(
        self, step: CenterInputStep
    ) -> tuple[tuple[FusionMeasurement, ...], tuple[TrackEvent, ...]]: ...

    def close(self) -> None: ...
