"""Inspect graph construction and contract boundaries, without running algorithms.

The stage-2 AST guard is temporary: replace it with behavioral validation when
implementing stage 3. It deliberately checks the agreed absence of business logic,
not mathematical outcomes or duplicated implementation details.
"""

from __future__ import annotations

import ast
import inspect
import unittest
from collections import Counter
from dataclasses import fields, is_dataclass
from pathlib import Path
from types import ModuleType
from typing import get_args, get_type_hints
from unittest.mock import patch

from chongming.composition.graph import ArchitectureAssembly, declare_assembly
from chongming.contracts.fusion import CandidateMethod
from chongming.contracts.runtime import EventPath
from chongming.contracts.scene import (
    ImagingBatch,
    ImagingTruthBatch,
    ScenarioPacketOutput,
    ScenarioStep,
    ScenarioTruthOutput,
    ScenarioVideoOutput,
    VirtualObservationBatch,
    VirtualObservationTruthBatch,
)
from chongming.modules import base
from chongming.modules.base import ModuleShell
from chongming.modules.fusion import DirectGeometryCandidateModule, VoxelCandidateModule
from chongming.ports import device, fusion, runtime
from tests.fixtures import architecture_specs


def _construct(
    *,
    path: EventPath = EventPath.IMAGE_PIPELINE,
    method: CandidateMethod = CandidateMethod.COARSE_VOXEL,
) -> tuple[ArchitectureAssembly, tuple[ModuleShell, ...]]:
    """Capture actual managed shell allocations, including any discarded by assembly."""

    created: list[ModuleShell] = []

    def record_new(cls: type[ModuleShell], *args: object, **kwargs: object) -> ModuleShell:
        instance = object.__new__(cls)
        created.append(instance)
        return instance

    spec, run = architecture_specs(center_input=path, candidate_method=method)
    with patch.object(ModuleShell, "__new__", new=record_new):
        assembly = declare_assembly(spec, run)
    return assembly, tuple(created)


def _port_classes(module: ModuleType) -> tuple[type[object], ...]:
    return tuple(
        cls
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if cls.__module__ == module.__name__ and getattr(cls, "_is_protocol", False)
    )


class ArchitectureStructureTests(unittest.TestCase):
    def test_scenario_step_has_three_explicit_typed_exits(self) -> None:
        field_names = tuple(field.name for field in fields(ScenarioStep))
        self.assertEqual(field_names, ("video", "packets", "truth"))
        self.assertTrue(is_dataclass(ScenarioVideoOutput))
        self.assertTrue(is_dataclass(ScenarioPacketOutput))
        self.assertTrue(is_dataclass(ScenarioTruthOutput))
        video_types = get_type_hints(ScenarioVideoOutput)
        packet_types = get_type_hints(ScenarioPacketOutput)
        truth_types = get_type_hints(ScenarioTruthOutput)
        self.assertNotIn("truth_object_id", video_types)
        self.assertNotIn("truth_object_id", packet_types)
        self.assertIn("online_correspondences", truth_types)

    def test_generation_batches_keep_online_and_truth_data_separate(self) -> None:
        self.assertEqual(tuple(field.name for field in fields(ImagingBatch)), ("frames",))
        self.assertEqual(tuple(field.name for field in fields(ImagingTruthBatch)), ("frame_truth",))
        self.assertNotIn(
            "truth_correspondences", {field.name for field in fields(VirtualObservationBatch)}
        )
        self.assertEqual(
            tuple(field.name for field in fields(VirtualObservationTruthBatch)),
            ("truth_correspondences",),
        )

    def test_every_constructed_shell_is_bound_once_in_resolvable_dag(self) -> None:
        assembly, constructed = _construct()
        constructed_ids = [id(module) for module in constructed]
        binding_ids = [id(binding.module) for binding in assembly.bindings]
        self.assertTrue(constructed_ids, "The factory must construct managed modules")
        self.assertEqual(Counter(binding_ids), Counter(constructed_ids))
        self.assertTrue(all(count == 1 for count in Counter(binding_ids).values()))

        nodes = {node.module_key: node for node in assembly.startup.modules}
        self.assertEqual(len(nodes), len(assembly.startup.modules), "Module keys must be unique")
        self.assertEqual(
            tuple(binding.node for binding in assembly.bindings), assembly.startup.modules
        )
        for node in nodes.values():
            with self.subTest(module=node.module_key):
                self.assertTrue(set(node.dependencies) <= nodes.keys())
                self.assertEqual(len(node.dependencies), len(set(node.dependencies)))

        remaining = {key: set(node.dependencies) for key, node in nodes.items()}
        resolved: set[str] = set()
        while remaining:
            ready = {key for key, dependencies in remaining.items() if dependencies <= resolved}
            self.assertTrue(ready, f"Dependency cycle among {sorted(remaining)}")
            resolved.update(ready)
            for key in ready:
                del remaining[key]
        self.assertEqual(resolved, set(nodes))

    def test_routes_and_candidate_methods_keep_all_modules_constructed(self) -> None:
        signatures: set[frozenset[tuple[str, type[object]]]] = set()
        for path in EventPath:
            for method in CandidateMethod:
                with self.subTest(path=path, method=method):
                    assembly, constructed = _construct(path=path, method=method)
                    self.assertEqual(
                        Counter(id(binding.module) for binding in assembly.bindings),
                        Counter(id(module) for module in constructed),
                    )
                    signatures.add(
                        frozenset(
                            (binding.node.module_key, type(binding.module))
                            for binding in assembly.bindings
                        )
                    )
                    self.assertEqual(assembly.runner.run_spec.center_input, path)
                    self.assertEqual(assembly.center.candidates.policy.primary_method, method)
                    self.assertIs(assembly.center.candidates.voxel, assembly.center.voxels)
                    self.assertIs(
                        assembly.center.candidates.direct, assembly.center.direct_candidates
                    )
                    self.assertTrue(any(isinstance(m, VoxelCandidateModule) for m in constructed))
                    self.assertTrue(
                        any(isinstance(m, DirectGeometryCandidateModule) for m in constructed)
                    )
        self.assertEqual(len(signatures), 1, "Selecting evidence must not skip a module")

    def test_station_sessions_and_sequencer_ownership_are_separate(self) -> None:
        assembly, _ = _construct()
        virtual = {s.station_id: s for s in assembly.scene.virtual_sequencers}
        self.assertEqual(len(virtual), len(assembly.stations))
        all_sequencers: list[ModuleShell] = []
        for station in assembly.stations:
            with self.subTest(station=station.config.station_id):
                image_sequence = station.sequencer
                virtual_sequence = virtual[station.config.station_id]
                self.assertNotEqual(
                    image_sequence.station_session_id, virtual_sequence.station_session_id
                )
                self.assertIs(station.calibration_states.sequencer, image_sequence)
                self.assertIs(station.calibration.states, station.calibration_states)
                self.assertIs(station.rays.sequencer, image_sequence)
                self.assertIs(station.device.sequencer, image_sequence)
                self.assertIs(station.device.calibrator, station.calibration)
                self.assertIs(station.device.ray_builder, station.rays)
                all_sequencers.extend((image_sequence, virtual_sequence))
        self.assertEqual(len({id(s) for s in all_sequencers}), len(all_sequencers))
        self.assertEqual(
            tuple(id(s) for s in assembly.scene.virtual_observations.sequencers),
            tuple(id(s) for s in assembly.scene.virtual_sequencers),
        )

    def test_online_port_types_cannot_reach_scene_truth_or_generation_settings(self) -> None:
        ports: tuple[type[object], ...] = (
            *_port_classes(device),
            *_port_classes(fusion),
            runtime.EventTransport,
        )
        checked_methods = 0

        def check_type(annotation: object, route: str, visited: set[object]) -> None:
            if annotation in visited:
                return
            visited.add(annotation)
            self.assertNotEqual(
                getattr(annotation, "__module__", None),
                "chongming.contracts.scene",
                f"Privileged scenario type leaked through {route}: {annotation}",
            )
            for argument in get_args(annotation):
                check_type(argument, route, visited)
            underlying = getattr(annotation, "__supertype__", None)
            if underlying is not None:
                check_type(underlying, route, visited)
            if inspect.isclass(annotation) and is_dataclass(annotation):
                for field, nested in get_type_hints(annotation).items():
                    check_type(nested, f"{route}.{annotation.__name__}.{field}", visited)

        for port in ports:
            for name, method in inspect.getmembers(port, inspect.isfunction):
                if name.startswith("_"):
                    continue
                checked_methods += 1
                with self.subTest(port=port.__name__, method=name):
                    for parameter, annotation in get_type_hints(method).items():
                        check_type(annotation, f"{port.__name__}.{name}.{parameter}", set())
        self.assertGreater(checked_methods, 0)

    def test_stage_two_shell_business_methods_only_raise(self) -> None:
        module_directory = Path(inspect.getfile(base)).parent
        implemented_scene = {
            "ExperimentClockModule",
            "TrajectoryModule",
            "CameraProjectionModule",
            "VisibilityModule",
            "SceneLogicModule",
        }
        checked_methods = 0
        for source in module_directory.glob("*.py"):
            tree = ast.parse(source.read_text(), filename=str(source))
            for declaration in tree.body:
                if not isinstance(declaration, ast.ClassDef):
                    continue
                if declaration.name in implemented_scene:
                    continue
                for method in declaration.body:
                    if not isinstance(method, ast.FunctionDef) or method.name == "__init__":
                        continue
                    checked_methods += 1
                    with self.subTest(source=source.name, cls=declaration.name, method=method.name):
                        body = list(method.body)
                        if (
                            body
                            and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)
                        ):
                            body.pop(0)
                        self.assertEqual(len(body), 1, "Stage 2 methods cannot produce fake data")
                        self.assertIsInstance(body[0], ast.Raise)
                        assert isinstance(body[0], ast.Raise)
                        exception = body[0].exc
                        self.assertIsInstance(exception, ast.Call)
                        assert isinstance(exception, ast.Call)
                        self.assertIsInstance(exception.func, ast.Name)
                        assert isinstance(exception.func, ast.Name)
                        self.assertEqual(exception.func.id, "NotImplementedError")
                        self.assertFalse(exception.keywords)
                        self.assertLessEqual(len(exception.args), 1)
                        for argument in exception.args:
                            self.assertIsInstance(argument, (ast.Name, ast.Constant))
        self.assertGreater(checked_methods, 0)


if __name__ == "__main__":
    unittest.main()
