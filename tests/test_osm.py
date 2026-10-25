"""Offline import, shared geometry, provenance and real Godot building checks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from chongming.contracts import model as m
from chongming.contracts import scene as s
from chongming.modules.scene import (
    CameraProjectionModule,
    SceneLogicModule,
    TrajectoryModule,
    VisibilityModule,
)
from chongming.rendering.geometry import contains_point, surface_hits
from chongming.rendering.godot import GodotRenderer, _entities
from chongming.scenarios.demo import state
from chongming.scenarios.json_content import content_sha256, parse_json
from chongming.scenarios.osm import (
    asset_bytes,
    building_objects,
    canonical_ring,
    extrude,
    load_landscape,
    main,
    prepare_osm,
    project_enu,
    validate_import,
)
from chongming.scenarios.serialization import (
    _decode,
    load_compiled_scenario,
    load_scenario,
    save_compiled_scenario,
    validate_scenario_spec,
)

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "scenarios/assets"
SOURCE = (ASSETS / "osm_demo.osm").read_bytes()
CONFIG: s.OSMImportConfig = _decode(
    json.loads((ASSETS / "osm_import.json").read_text()), s.OSMImportConfig
)


def prepare(content: bytes = SOURCE, config: s.OSMImportConfig = CONFIG) -> s.OSMImport:
    return prepare_osm(content, config, source_url="synthetic:CC0", data_date="2026-10-20").imported


def changed_source(
    *,
    tags: dict[str, str] | None = None,
    refs: tuple[int, ...] | None = None,
    relation: bool = False,
) -> bytes:
    root = ET.fromstring(SOURCE)
    way = root.find("way")
    assert way is not None
    if tags is not None:
        for tag in list(way.findall("tag")):
            way.remove(tag)
        for key, value in tags.items():
            ET.SubElement(way, "tag", k=key, v=value)
    if refs is not None:
        for nd in list(way.findall("nd")):
            way.remove(nd)
        for ref in refs:
            ET.SubElement(way, "nd", ref=str(ref))
    if relation:
        rel = ET.SubElement(root, "relation", id="900")
        ET.SubElement(rel, "member", type="way", ref="101", role="outer")
        ET.SubElement(rel, "member", type="way", ref="12345", role="inner")
        ET.SubElement(rel, "tag", k="type", v="multipolygon")
        ET.SubElement(rel, "tag", k="building", v="yes")
    return ET.tostring(root)


def sample(spec: s.ScenarioSpec, time_ns: int = 0) -> s.LogicalSceneSnapshot:
    logic = SceneLogicModule(TrajectoryModule(), CameraProjectionModule(), VisibilityModule())
    logic.initialize(spec.scene_definition, spec.perturbations, spec.random_seed)
    return logic.sample(s.SimulationTime(spec.timeline.clock_domain.clock_domain_id, time_ns, 0))


class OSMTests(unittest.TestCase):
    def test_projection_control_points_and_axis(self) -> None:
        anchor = replace(CONFIG.anchor, latitude_deg=0, longitude_deg=0, height_m=0)
        self.assertEqual(project_enu(0, 0, anchor), (0, 0))
        np.testing.assert_allclose(project_enu(0.001, 0, anchor), (111.319491, 0), atol=1e-6)
        np.testing.assert_allclose(project_enu(0, 0.001, anchor), (0, 110.574276), atol=1e-6)
        self.assertEqual(project_enu(121.47, 31.23, CONFIG.anchor), (0, 0))
        east, north = project_enu(121.4701, 31.2301, CONFIG.anchor)
        self.assertTrue(9 < east < 10 and 11 < north < 12)

    def test_cleaning_determinism_and_traversal_order(self) -> None:
        first = prepare_osm(SOURCE, CONFIG, source_url="local:test", data_date="2026-10-20")
        second = prepare_osm(SOURCE, CONFIG, source_url="local:test", data_date="2026-10-20")
        self.assertEqual(asset_bytes(first.imported), asset_bytes(second.imported))
        self.assertLess(len(first.cleaned_xml), len(SOURCE))
        self.assertNotIn(b"addr:street", first.cleaned_xml)
        self.assertNotIn(b'"bench"', first.cleaned_xml)
        self.assertIn(b'id="999"', first.cleaned_xml)
        self.assertEqual(len(first.imported.roads), 1)
        cleaned = prepare(first.cleaned_xml)
        self.assertEqual(first.imported.buildings, cleaned.buildings)
        self.assertEqual(first.imported.diagnostics, cleaned.diagnostics)
        root = ET.fromstring(SOURCE)
        for way in root.findall("way"):
            nds = way.findall("nd")
            if len(nds) > 3 and nds[0].get("ref") == nds[-1].get("ref"):
                refs = [nd.attrib["ref"] for nd in nds[:-1]][::-1]
                refs = refs[1:] + refs[:1]
                for nd, ref in zip(nds, refs + refs[:1], strict=True):
                    nd.set("ref", ref)
        root[:] = list(root)[::-1]
        reordered = prepare(ET.tostring(root))
        self.assertEqual(first.imported.buildings, reordered.buildings)
        self.assertEqual(first.imported.diagnostics, reordered.diagnostics)

    def test_explicit_height_policy_and_diagnostics(self) -> None:
        for tags, expected in (
            ({"height": "40 ft"}, (12.192, "height")),
            ({"height": "8 m", "building:levels": "5"}, (8, "height")),
            ({"height": "unknown", "building:levels": "5"}, (15, "building:levels")),
            ({"height": "9999", "building:levels": "bad"}, (9, "default")),
            ({}, (9, "default")),
        ):
            with self.subTest(tags=tags):
                imported = prepare(changed_source(tags={"building": "yes", **tags}))
                building = imported.buildings[0]
                self.assertAlmostEqual(building.height_m, expected[0])
                self.assertEqual(building.height_source, expected[1])
                reasons = {d.reason for d in imported.diagnostics if d.element_id == "way/101"}
                self.assertEqual("default_height" in reasons, expected[1] == "default")
                if tags.get("height") in {"unknown", "9999"}:
                    self.assertIn("invalid_height_fallback", reasons)
                if tags.get("building:levels") == "bad":
                    self.assertIn("invalid_levels_fallback", reasons)

    def test_rejection_and_cleaned_relation_diagnostics(self) -> None:
        for content, reason in (
            (changed_source(refs=(1, 2, 3, 4)), "unclosed_way"),
            (changed_source(refs=(1, 2, 12345, 4, 1)), "missing_node"),
            (changed_source(refs=(1, 3, 2, 4, 1)), "self_intersection"),
            (changed_source(refs=(1, 2, 3, 2, 1)), "degenerate_or_repeated_vertices"),
            (
                changed_source(tags={"building": "yes", "building:part": "yes"}),
                "unsupported_building_part",
            ),
            (
                changed_source(tags={"building": "yes", "min_height": "2"}),
                "unsupported_raised_building",
            ),
            (changed_source(relation=True), "unsupported_relation_member"),
        ):
            with self.subTest(reason=reason):
                result = prepare_osm(content, CONFIG, source_url="test", data_date="2026-10-20")
                self.assertNotIn(101, {b.osm_way_id for b in result.imported.buildings})
                self.assertIn(s.OSMDiagnostic("way/101", reason), result.imported.diagnostics)
                self.assertEqual(
                    result.imported.diagnostics, prepare(result.cleaned_xml).diagnostics
                )

    def test_boundary_policy_and_invalid_configuration(self) -> None:
        crossing = replace(CONFIG, bounds_wgs84=(121.47, 31.229, 121.471, 31.231))
        imported = prepare(config=crossing)
        self.assertIn(s.OSMDiagnostic("way/101", "boundary_crossing"), imported.diagnostics)
        for config in (
            replace(CONFIG, level_height_m=0),
            replace(CONFIG, default_height_m=float("nan")),
            replace(CONFIG, anchor=replace(CONFIG.anchor, datum="other")),
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                prepare(config=config)

    def test_concave_prism_roof_notch_and_finite_sight_segment(self) -> None:
        ring = canonical_ring(
            ((0.0, 0.0), (4.0, 0.0), (4.0, 1.0), (1.0, 1.0), (1.0, 4.0), (0.0, 4.0))
        )
        mesh = extrude(s.OSMBuilding(1, ring, 3, "default", (("building", "yes"),)))
        pose = state((0.0, 0.0, 0.0))
        self.assertTrue(contains_point((0.5, 0.5, 1.0), pose, mesh))
        self.assertFalse(contains_point((2.0, 2.0, 1.0), pose, mesh))
        rays = np.asarray([[0.0, 0.0, -1.0]])
        self.assertTrue(np.isinf(surface_hits((2.0, 2.0, 5.0), rays, pose, mesh)[0][0]))
        self.assertAlmostEqual(surface_hits((0.5, 0.5, 5.0), rays, pose, mesh)[0][0], 2.0)
        entity = replace(building_objects(prepare())[0], pose=pose, geometry=mesh)
        inside = VisibilityModule._segment_hit(entity, (0.5, 0.5, 1.0), (0.7, 0.7, 1.0))
        assert inside is not None
        self.assertEqual(inside[0], 0)
        self.assertIsNone(VisibilityModule._segment_hit(entity, (-3.0, 0.5, 1.0), (-1.0, 0.5, 1.0)))
        hit = VisibilityModule._segment_hit(entity, (-3.0, 0.5, 1.0), (2.0, 0.5, 1.0))
        assert hit is not None
        self.assertAlmostEqual(hit[0], 0.6)
        open_mesh = replace(mesh, triangle_vertex_indices=mesh.triangle_vertex_indices[1:])
        self.assertFalse(contains_point(mesh.vertices_body_m[0], pose, open_mesh))

    def test_preview_publishes_only_completed_validated_import(self) -> None:
        received: list[s.OSMImport] = []
        result = prepare_osm(
            SOURCE, CONFIG, source_url="test", data_date="2026-10-20", preview=received.append
        )
        self.assertEqual(received, [result.imported])
        self.assertEqual(
            [str(o.object_id) for o in building_objects(received[0])],
            ["osm/way/101", "osm/way/102", "osm/way/103"],
        )
        with self.assertRaises(ValueError):
            prepare_osm(
                b"<!DOCTYPE osm><osm/>",
                CONFIG,
                source_url="test",
                data_date="2026-10-20",
                preview=received.append,
            )
        self.assertEqual(len(received), 1)

    def test_verified_local_asset_content_and_version_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "asset.json"
            path.write_bytes(b"{}")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                load_landscape(path.name, "0" * 64, root)
            with self.assertRaisesRegex(ValueError, "local asset URI"):
                load_landscape("https://example.org/asset.json", "0" * 64, root)
            payload = asset_bytes(prepare())
            path.write_bytes(payload)
            landscape = load_landscape(path.name, content_sha256(parse_json(payload)), root)
            with self.assertRaisesRegex(ValueError, "version"):
                validate_import(replace(landscape.imported, format="future-version"))
            building = replace(landscape.imported.buildings[0], height_m=99)
            with self.assertRaisesRegex(ValueError, "height disagrees"):
                validate_import(replace(landscape.imported, buildings=(building,)))

    def test_landscape_decodes_the_single_verified_parse(self) -> None:
        payload = asset_bytes(prepare())
        digest = content_sha256(parse_json(payload))
        with patch.object(Path, "read_bytes", side_effect=[payload, b"changed"]) as read:
            landscape = load_landscape("asset.json", digest, ROOT)
        read.assert_called_once()
        self.assertEqual(landscape.imported, prepare())

    def test_landscape_does_not_decode_unverified_content(self) -> None:
        payload = asset_bytes(prepare())
        with (
            patch.object(Path, "read_bytes", return_value=payload),
            patch("chongming.scenarios.serialization._decode") as decode,
            self.assertRaisesRegex(ValueError, "content digest mismatch"),
        ):
            load_landscape("asset.json", "0" * 64, ROOT)
        decode.assert_not_called()

    def test_compiled_landscape_reverification_uses_content_identity(self) -> None:
        spec = load_scenario(ROOT / "scenarios/osm.json")
        landscape = spec.scene_definition.osm_landscape
        assert landscape is not None
        content = (ROOT / "scenarios" / landscape.asset_uri).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / landscape.asset_uri
            asset.parent.mkdir(parents=True)
            asset.write_text(json.dumps(parse_json(content), indent=2) + "\n")
            compiled = root / "compiled.json"
            save_compiled_scenario(spec, compiled)
            self.assertEqual(load_compiled_scenario(compiled), spec)
            document = json.loads(content)
            document["attribution"] += " changed"
            asset.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "content digest mismatch"):
                load_compiled_scenario(compiled)

    def test_scene_roundtrip_authoritative_mesh_and_anchor(self) -> None:
        spec = load_scenario(ROOT / "scenarios/osm.json")
        landscape = spec.scene_definition.osm_landscape
        assert landscape is not None
        self.assertEqual(spec.station_configs[0].geodetic_anchor, CONFIG.anchor)
        self.assertEqual(
            spec.station_configs[0].config_digest,
            spec.station_configs[0].installation_uncertainty.state_ref.config_digest,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "compiled.json"
            save_compiled_scenario(spec, path)
            restored = load_compiled_scenario(path, asset_root=ROOT / "scenarios")
            self.assertEqual(restored, spec)
        objects = spec.scene_definition.world_objects
        modified = tuple(
            replace(o, geometry=s.BoxGeometry((1.0, 1.0, 1.0)))
            if o.category == "osm-building"
            else o
            for o in objects
        )
        with self.assertRaisesRegex(ValueError, "canonical asset"):
            validate_scenario_spec(
                replace(
                    spec, scene_definition=replace(spec.scene_definition, world_objects=modified)
                )
            )
        with self.assertRaisesRegex(ValueError, "anchors must agree"):
            validate_scenario_spec(
                replace(
                    spec,
                    station_configs=(
                        replace(
                            spec.station_configs[0],
                            geodetic_anchor=replace(CONFIG.anchor, longitude_deg=121.4701),
                        ),
                    ),
                )
            )

    def test_capture_and_godot_payload_share_geometry(self) -> None:
        spec = load_scenario(ROOT / "scenarios/osm.json")
        first, later = sample(spec), sample(spec, 8_000_000_000)

        def visible(scene: s.LogicalSceneSnapshot) -> s.TargetVisibilityTruth:
            return next(v for v in scene.target_visibility if v.camera_role == m.CameraRole.MAIN)

        self.assertFalse(visible(first).capture_eligible)
        self.assertIn("osm/way/101", {o.blocker_object_id for o in visible(first).occlusions})
        self.assertTrue(visible(later).capture_eligible)
        payload = next(p for p in _entities(first) if p.get("object_id") == "osm/way/101")
        building = next(o for o in first.world_objects if str(o.object_id) == "osm/way/101")
        self.assertIsInstance(building.geometry, s.TriangleMeshGeometry)
        assert isinstance(building.geometry, s.TriangleMeshGeometry)
        self.assertEqual(payload["vertices"], [list(v) for v in building.geometry.vertices_body_m])

    def test_cli_persists_cleaning_provenance_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "buildings.json"
            cleaned = Path(directory) / "cleaned.osm"
            argv = [
                "chongming-osm",
                str(ASSETS / "osm_demo.osm"),
                "--config",
                str(ASSETS / "osm_import.json"),
                "--source-url",
                "synthetic:CC0",
                "--data-date",
                "2026-10-20",
                "--output",
                str(output),
                "--cleaned-output",
                str(cleaned),
                "--license",
                "CC0-1.0",
                "--attribution",
                "Synthetic CC0",
            ]
            with patch("sys.argv", argv), patch("builtins.print"):
                main()
            report = json.loads(output.with_suffix(".report.json").read_text())
            self.assertEqual(report["source_sha256"], hashlib.sha256(SOURCE).hexdigest())
            self.assertEqual(
                report["cleaned_sha256"], hashlib.sha256(cleaned.read_bytes()).hexdigest()
            )
            self.assertEqual(
                report["asset_content_sha256"], content_sha256(parse_json(output.read_bytes()))
            )
            self.assertEqual(report["license"], "CC0-1.0")


@unittest.skipUnless(
    os.environ.get("CHONGMING_GODOT") or shutil.which("godot4") or shutil.which("godot"),
    "Godot executable is required",
)
class OSMGodotTests(unittest.TestCase):
    def test_building_hides_target_and_motion_reveals_it(self) -> None:
        spec = load_scenario(ROOT / "scenarios/osm.json")
        renderer = GodotRenderer()
        try:
            for time_ns, hidden in ((0, True), (8_000_000_000, False)):
                scene = sample(spec, time_ns)
                camera = next(c for c in scene.cameras if c.camera_role == m.CameraRole.MAIN)
                camera = replace(
                    camera,
                    actual_intrinsics=replace(
                        camera.actual_intrinsics,
                        image_width_px=512,
                        image_height_px=288,
                        focal_x_px=512,
                        focal_y_px=512,
                        principal_x_px=255.5,
                        principal_y_px=143.5,
                    ),
                )
                raw, _ = renderer.render(scene, camera)
                empty, _ = renderer.render(replace(scene, flying_objects=()), camera)
                image = np.frombuffer(raw, np.uint8).astype(int)
                background = np.frombuffer(empty, np.uint8).astype(int)
                difference = int(np.count_nonzero(np.abs(image - background) > 5))
                if hidden:
                    self.assertLess(difference, 10)
                else:
                    self.assertGreater(difference, 50)
            self.assertNotIn("SCRIPT ERROR", renderer._diagnostics())
        finally:
            renderer.close()
