from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from chongming.contracts import scene
from chongming.modules.scene import (
    CameraProjectionModule,
    SceneLogicModule,
    TrajectoryModule,
    VisibilityModule,
)
from chongming.scenarios.authoring import (
    AUTHORING_FORMAT,
    compile_authoring,
    demo_authoring,
    save_authoring,
)
from chongming.scenarios.demo import demo_source, demo_spec
from chongming.scenarios.serialization import (
    load_compiled_scenario,
    load_scenario,
    save_compiled_scenario,
    scenario_to_dict,
    validate_scenario_spec,
    validate_supported_features,
    verify_scenario_assets,
)


class ScenarioPersistenceTests(unittest.TestCase):
    def test_repository_scenes_load_and_preserve_camera_contract(self) -> None:
        paths = sorted((Path(__file__).resolve().parents[1] / "scenarios").glob("*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                spec = load_scenario(path)
                validate_supported_features(spec)
                self.assertEqual(
                    {
                        (camera.camera_role.value, camera.actual_intrinsics.image_width_px,
                         camera.actual_intrinsics.image_height_px)
                        for camera in spec.scene_definition.cameras
                    },
                    {("main", 1920, 1080), ("auxiliary", 640, 480)},
                )
                self.assertEqual(
                    {camera.exposure.frame_period_ns for camera in spec.scene_definition.cameras},
                    {1_000_000_000 // 30},
                )

    def test_heightfield_scene_changes_capture_with_height_and_places_mesh_once(self) -> None:
        spec = load_scenario(Path(__file__).resolve().parents[1] / "scenarios/heightfield.json")
        logic = SceneLogicModule(TrajectoryModule(), CameraProjectionModule(), VisibilityModule())
        logic.initialize(spec.scene_definition, spec.perturbations, spec.random_seed)
        start = logic.sample(scene.SimulationTime(spec.timeline.clock_domain.clock_domain_id, 0, 0))
        end = logic.sample(scene.SimulationTime(spec.timeline.clock_domain.clock_domain_id, 11_000_000_000, 1))
        main_start = {
            str(item.truth_object_id): item
            for item in start.target_visibility if item.camera_role.value == "main"
        }
        main_end = {
            str(item.truth_object_id): item
            for item in end.target_visibility if item.camera_role.value == "main"
        }
        self.assertFalse(main_start["rising-sphere"].capture_eligible)
        self.assertIn(start.terrain.terrain_id,
                      {item.blocker_object_id for item in main_start["rising-sphere"].occlusions})
        self.assertTrue(main_end["rising-sphere"].capture_eligible)
        self.assertTrue(main_start["high-box"].capture_eligible)
        self.assertTrue(main_end["high-box"].capture_eligible)
        # Inspect the native request, without starting a renderer or HTTP server.
        from chongming.rendering.metal import _entities

        mesh = _entities(start)[0]
        self.assertEqual(mesh["position"], [-40.0, 32.0, 0.0])
        self.assertEqual(mesh["vertices"][0], [0.0, 0.0, 0.0])
        self.assertEqual(mesh["vertices"][9], [0.0, 10.0, 16.0])
        raised = replace(start.terrain, base_elevation_m=3.0)
        hit = VisibilityModule._segment_hit(raised, (18.0, 20.0, 15.0), (18.0, 65.0, 15.0))
        assert hit is not None
        self.assertEqual(hit[1].intersection_enu_m, (18.0, 39.5, 15.0))

    def test_heightfield_rejects_malformed_grid_and_spacing(self) -> None:
        for value in (
            {"spacing_m": 0, "heights_m": [[0, 0], [0, 0]]},
            {"spacing_m": -1, "heights_m": [[0, 0], [0, 0]]},
            {"heights_m": [[0, 0], "bad row", [0, 0]]},
            {"heights_m": [[0, 0], [0]]},
            {"origin_enu_m": [True, 0], "heights_m": [[0, 0], [0, 0]]},
        ):
            document = demo_authoring(duration_s=1)
            document["terrain"] = {"type": "heightfield", **value}
            with self.subTest(terrain=value), self.assertRaises(ValueError):
                compile_authoring(document)

    def test_crossing_scene_uses_constant_velocity_with_custom_ids(self) -> None:
        spec = load_scenario(Path(__file__).resolve().parents[1] / "scenarios/constant_velocity.json")
        logic = SceneLogicModule(TrajectoryModule(), CameraProjectionModule(), VisibilityModule())
        logic.initialize(spec.scene_definition, spec.perturbations, spec.random_seed)
        start = logic.sample(scene.SimulationTime(spec.timeline.clock_domain.clock_domain_id, 0, 0))
        middle = logic.sample(scene.SimulationTime(spec.timeline.clock_domain.clock_domain_id, 6_000_000_000, 1))
        self.assertEqual({str(item.truth_object_id) for item in start.flying_objects},
                         {"eastbound", "westbound"})
        self.assertEqual({item.state.position_enu_m for item in middle.flying_objects},
                         {(0.0, 62.0, 20.0)})
        for camera in middle.cameras:
            if camera.camera_role.value == "main":
                projections = [
                    item.projection.centroid_px for item in middle.target_visibility
                    if item.station_id == camera.station_id and item.camera_role == camera.camera_role
                ]
                self.assertEqual(projections[0], projections[1])

    def test_authoring_json_is_compact_semantic_and_uses_standard_resolution(self) -> None:
        document = demo_authoring(stations=2, fps=30, duration_s=2)
        self.assertEqual(document["format"], AUTHORING_FORMAT)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "authoring.json"
            save_authoring(document, path)
            self.assertLess(len(path.read_text().splitlines()), 120)
            compiled = compile_authoring(document)
        self.assertEqual(
            {
                (camera.camera_role.value, camera.actual_intrinsics.image_width_px, camera.actual_intrinsics.image_height_px)
                for camera in compiled.scene_definition.cameras
            },
            {("main", 1920, 1080), ("auxiliary", 640, 480)},
        )

    def test_authoring_edit_recompiles_without_expanded_trajectory(self) -> None:
        document = demo_authoring(stations=1, duration_s=1)
        document["targets"] = []
        compiled = compile_authoring(document)
        self.assertEqual(compiled.scene_definition.flying_objects, ())
        self.assertEqual(compiled.timeline.end_time_ns, 999_999_999)

    def test_json_round_trip_replays_same_logical_and_online_stream(self) -> None:
        base = demo_spec(station_count=1, fps=2)
        original = replace(base, timeline=replace(base.timeline, end_time_ns=1_000_000_000))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            save_compiled_scenario(original, path)
            loaded = load_compiled_scenario(path)
        first = list(demo_source(original).generate(original))
        second = list(demo_source(loaded).generate(loaded))

        def normalize(steps):
            return [
                (
                    step.video.sample_time,
                    step.truth.logical_scene,
                    tuple(
                        (
                            ray.frame_id,
                            ray.capture_time,
                            ray.ray_origin_enu_m,
                            ray.ray_direction_enu_unit,
                            ray.calibration_usage,
                        )
                        for ray in step.packets.rays
                    ),
                    tuple(item.category for item in step.packets.diagnostics),
                )
                for step in steps
            ]

        self.assertEqual(normalize(first), normalize(second))

    def test_json_is_explicit_and_editable(self) -> None:
        document = scenario_to_dict(demo_spec(fps=2))
        self.assertEqual(document["format"], "scenario-json-v1")
        self.assertIn("$type", document["scenario"])
        self.assertIn("timeline", document["scenario"])
        document["scenario"]["random_seed"] = 99
        self.assertEqual(
            load_scenario_from_document(document).random_seed,
            99,
        )

    def test_profile_validation_rejects_implicit_non_baseline_profile(self) -> None:
        spec = demo_spec(fps=2)
        camera = spec.scene_definition.cameras[0]
        bad = replace(
            spec,
            scene_definition=replace(
                spec.scene_definition,
                cameras=(replace(camera, metadata=replace(camera.metadata, profile_id="custom")),)
                + spec.scene_definition.cameras[1:],
            ),
        )
        with self.assertRaisesRegex(ValueError, "experimental"):
            validate_scenario_spec(bad)

    def test_external_asset_digest_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "terrain.bin"
            asset.write_bytes(b"heightfield-v1")
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            base = demo_spec(fps=2)
            terrain = replace(
                base.scene_definition.terrain,
                model=scene.TerrainModel.HEIGHTFIELD,
                maximum_relief_m=2.0,
                heightfield_asset_uri=asset.name,
                heightfield_asset_digest=digest,
            )
            spec = replace(
                base,
                scene_definition=replace(base.scene_definition, terrain=terrain),
            )
            verify_scenario_assets(spec, root)
            asset.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                verify_scenario_assets(spec, root)


def load_scenario_from_document(document: dict[str, object]):
    from chongming.scenarios.serialization import scenario_from_dict

    return scenario_from_dict(document)


if __name__ == "__main__":
    unittest.main()
