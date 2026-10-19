"""All-module dependency and startup declarations."""

from .graph import (
    ArchitectureAssembly,
    CenterAssembly,
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
