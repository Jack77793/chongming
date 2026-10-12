"""Shared lifecycle declaration only; subclasses implement resources in stage 3."""

from chongming.contracts.model import ProcessingTime
from chongming.contracts.runtime import ModuleHealth


class ModuleShell:
    def start_module(self, module_key: str, now: ProcessingTime) -> ModuleHealth:
        raise NotImplementedError("TODO(stage-3): initialize module resources")

    def stop_module(self, module_key: str, now: ProcessingTime) -> ModuleHealth:
        raise NotImplementedError("TODO(stage-3): release module resources")
