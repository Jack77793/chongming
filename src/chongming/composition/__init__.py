"""All-module dependency and startup declarations."""

from .center import CenterAssembly
from .graph import (
    ArchitectureAssembly,
    SceneAssembly,
    StationAssembly,
    declare_assembly,
)

__all__ = [
    "ArchitectureAssembly",
    "CenterAssembly",
    "SceneAssembly",
    "StationAssembly",
    "declare_assembly",
]
