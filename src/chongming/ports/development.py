"""Volunteer algorithm hook; registration and input have online-only types."""

from typing import Protocol

from chongming.contracts.development import (
    CenterAlgorithmResult,
    CenterInputStep,
    CenterRegistration,
)


class CenterAlgorithm(Protocol):
    def start(self, registration: CenterRegistration) -> None: ...

    def process(self, step: CenterInputStep) -> CenterAlgorithmResult: ...

    def close(self) -> None: ...
