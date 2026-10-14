"""Shared lifecycle declaration only; subclasses implement resources in stage 3."""

from typing import ClassVar, Self

from chongming.contracts.model import ProcessingTime
from chongming.contracts.runtime import ModuleHealth, ModuleState


class ModuleShell:
    business_implemented: ClassVar[bool] = False

    def __new__(cls, *args: object, **kwargs: object) -> Self:
        # Allow dependency-declaration shells to receive constructor arguments.
        return super().__new__(cls)

    def start_module(self, module_key: str, now: ProcessingTime) -> ModuleHealth:
        if getattr(self, "_lifecycle_state", None) is ModuleState.RUNNING:
            raise RuntimeError(f"module already started: {module_key}")
        object.__setattr__(self, "_lifecycle_state", ModuleState.RUNNING)
        return ModuleHealth(
            module_key=module_key,
            state=ModuleState.RUNNING,
            observed_at=now,
            reason_codes=() if self.business_implemented else ("algorithm_unimplemented",),
        )

    def stop_module(self, module_key: str, now: ProcessingTime) -> ModuleHealth:
        object.__setattr__(self, "_lifecycle_state", ModuleState.STOPPED)
        return ModuleHealth(
            module_key=module_key, state=ModuleState.STOPPED, observed_at=now, reason_codes=()
        )
