"""Uniform lifecycle for every module, separate from algorithm data readiness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from chongming.contracts.model import ProcessingTime
from chongming.contracts.runtime import ModuleHealth, ModuleNode


class ModuleLifecycle(Protocol):
    def start_module(self, module_key: str, now: ProcessingTime) -> ModuleHealth:
        """Initialize resources; no detection or trusted solution is required to start."""
        ...

    def stop_module(self, module_key: str, now: ProcessingTime) -> ModuleHealth:
        """Release initialized resources, including after startup/runtime failure."""
        ...


@dataclass(frozen=True, kw_only=True)
class ModuleBinding:
    node: ModuleNode
    module: ModuleLifecycle
