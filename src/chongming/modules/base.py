"""Shared lifecycle declaration only; subclasses implement resources in stage 3."""

from typing import Self

from chongming.contracts.model import ProcessingTime
from chongming.contracts.runtime import ModuleHealth


class ModuleShell:
    def __new__(cls, *args: object, **kwargs: object) -> Self:
        # Allow dependency-declaration shells to receive constructor arguments.
        return super().__new__(cls)

    def start_module(self, module_key: str, now: ProcessingTime) -> ModuleHealth:
        raise NotImplementedError("TODO(stage-3): initialize module resources")

    def stop_module(self, module_key: str, now: ProcessingTime) -> ModuleHealth:
        raise NotImplementedError("TODO(stage-3): release module resources")
