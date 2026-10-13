"""Behavioral checks for image geometry, deterministic exposures, and preview delivery."""

from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
from PIL import Image

from chongming.contracts import model as m
from chongming.contracts import scene as s
from chongming.modules.scene import (
    CameraImagingModule,
    CameraProjectionModule,
    SceneLogicModule,
    TrajectoryModule,
    VisibilityModule,
)
from chongming.preview import PreviewRuntime, encode_video, preview_payload, preview_server
from chongming.rendering.geometry import optical_rays, surface_hits
from chongming.rendering.metal import (
    MetalRenderer,
    projected_footprints,
    quantize_rgb8,
    render_rgb8,
)
from chongming.scenarios.demo import demo_source, demo_spec, optical_rotation, state
from tests.fixtures import architecture_specs


def test_scene(geometry: s.GeometrySpec, position: m.Vec3 = (0.0, 0.0, 10.0)) -> s.ScenarioSpec:
    spec, _ = architecture_specs()
    target = s.FlyingObjectSpec(
        s.TruthObjectId("target"),
        "target",
        "test",
        s.TrajectorySpec(s.TrajectoryInterpolation.HOLD, (s.TrajectoryKnot(0, state(position)),)),
        None,
        geometry,
        s.SurfaceAppearance((0.1, 0.1, 0.1), 1.0),
        (0.0, 0.0, 0.0),
    )
    return replace(
        spec,
        scene_definition=replace(
            spec.scene_definition,
            terrain=replace(spec.scene_definition.terrain, base_elevation_m=-100.0),
            flying_objects=(target,),
            illumination=s.IlluminationSpec((0.0, 0.0, -1.0), 20_000.0, (0.8, 0.8, 0.8)),
        ),
    )


def snapshot(spec: s.ScenarioSpec, time_ns: int = 500_000) -> s.LogicalSceneSnapshot:
    logic = SceneLogicModule(TrajectoryModule(), CameraProjectionModule(), VisibilityModule())
    logic.initialize(spec.scene_definition, spec.perturbations, spec.random_seed)
    return logic.sample(s.SimulationTime("structure-clock", time_ns, 0))


def render_gpu(
    scene: s.LogicalSceneSnapshot, camera: s.CameraStateTruth
) -> tuple[np.ndarray, dict[str, tuple[float, float]]]:
    raw, _ = render_rgb8(scene, camera)
    i = camera.actual_intrinsics
    image = np.frombuffer(raw, dtype=np.uint8).reshape(i.image_height_px, i.image_width_px, 3)
    return image, projected_footprints(scene, camera)


class SurfaceRenderingTests(unittest.TestCase):
    def test_renderer_survives_parent_terminal_signals_and_closes_cleanly(self) -> None:
        renderer = MetalRenderer()
        self.addCleanup(renderer.close)
        process = renderer._process
        assert process is not None
        self.assertNotEqual(os.getpgid(process.pid), os.getpgrp())
        scene = snapshot(test_scene(s.SphereGeometry(1.0)))
        raw, _ = renderer.render(scene, scene.cameras[0])
        self.assertEqual(len(raw), 64 * 48 * 3)
        renderer.close()
        renderer.close()
        self.assertEqual(process.poll(), 0)
        self.assertTrue(
            all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        )
        with self.assertRaisesRegex(RuntimeError, "closed"):
            renderer.render(scene, scene.cameras[0])

    def test_sphere_box_and_mesh_have_nontrivial_projected_footprints(self) -> None:
        geometries = (
            s.SphereGeometry(1.0),
            s.BoxGeometry((1.0, 0.6, 0.4)),
            s.TriangleMeshGeometry(
                ((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (0.0, 1.0, 0.0)), ((0, 1, 2),)
            ),
        )
        for geometry in geometries:
            with self.subTest(geometry=type(geometry).__name__):
                scene = snapshot(test_scene(geometry))
                pixels, footprints = render_gpu(scene, scene.cameras[0])
                self.assertGreater(np.count_nonzero(pixels.mean(axis=2) < 128), 100)
                self.assertGreater(footprints["target"][0], 10)
                self.assertGreater(footprints["target"][1], 10)

    def test_target_size_changes_with_distance_and_clips_at_image_edge(self) -> None:
        areas = []
        for distance in (10.0, 20.0):
            scene = snapshot(test_scene(s.SphereGeometry(1.0), (0.0, 0.0, distance)))
            pixels, _ = render_gpu(scene, scene.cameras[0])
            areas.append(np.count_nonzero(pixels.mean(axis=2) < 128))
        self.assertGreater(areas[0], 3.5 * areas[1])
        scene = snapshot(test_scene(s.SphereGeometry(1.0), (3.7, 0.0, 10.0)))
        self.assertFalse(scene.target_visibility[0].capture_eligible)
        pixels, _ = render_gpu(scene, scene.cameras[0])
        self.assertGreater(np.count_nonzero(pixels.mean(axis=2) < 128), 0)

    def test_depth_hides_target_and_preserves_partial_silhouette(self) -> None:
        spec = test_scene(s.SphereGeometry(1.0))
        obstacle = s.WorldObjectSpec(
            s.WorldObjectId("wall"),
            "wall",
            s.WorldObjectKind.OBSTACLE,
            "test",
            state((0.0, 0.0, 5.0)),
            s.BoxGeometry((1.0, 1.0, 0.1)),
            s.SurfaceAppearance((0.7, 0.7, 0.7), 1.0),
            True,
        )
        full = snapshot(
            replace(
                spec, scene_definition=replace(spec.scene_definition, world_objects=(obstacle,))
            )
        )
        self.assertFalse(full.target_visibility[0].capture_eligible)
        image, _ = render_gpu(full, full.cameras[0])
        self.assertEqual(np.count_nonzero(image.mean(axis=2) < 128), 0)
        wall = replace(
            obstacle, pose=state((-0.5, 0.0, 5.0)), geometry=s.BoxGeometry((0.5, 1.0, 0.1))
        )
        partial = snapshot(
            replace(spec, scene_definition=replace(spec.scene_definition, world_objects=(wall,)))
        )
        self.assertFalse(partial.target_visibility[0].capture_eligible)
        image, _ = render_gpu(partial, partial.cameras[0])
        count = np.count_nonzero(image.mean(axis=2) < 128)
        self.assertGreater(count, 50)
        self.assertLess(count, 250)
        beyond = replace(obstacle, pose=state((0.0, 0.0, 15.0)))
        visible = snapshot(
            replace(spec, scene_definition=replace(spec.scene_definition, world_objects=(beyond,)))
        )
        self.assertTrue(visible.target_visibility[0].capture_eligible)
        image, _ = render_gpu(visible, visible.cameras[0])
        self.assertGreater(np.count_nonzero(image.mean(axis=2) < 128), 250)

    def test_near_plane_mesh_and_rotated_box_render_without_projection_singularities(self) -> None:
        geometry = s.TriangleMeshGeometry(
            ((-1.0, -1.0, -0.2), (1.0, -1.0, 1.0), (0.0, 1.0, 1.0)), ((0, 1, 2),)
        )
        scene = snapshot(test_scene(geometry, (0.0, 0.0, 0.0)))
        image, _ = render_gpu(scene, scene.cameras[0])
        self.assertTrue(np.all(np.isfinite(image)))
        self.assertGreater(np.count_nonzero(image.mean(axis=2) < 128), 0)
        rays = np.asarray(((0.0, 0.0, 1.0),))
        depth, _ = surface_hits(
            (0.0, 0.0, 0.0),
            rays,
            state((0.0, 0.0, 5.0), ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))),
            s.BoxGeometry((2.0, 1.0, 0.5)),
        )
        self.assertAlmostEqual(depth[0], 3.0)

    def test_visibility_records_surface_intersection_and_flat_terrain(self) -> None:
        obstacle = s.WorldObjectSpec(
            s.WorldObjectId("sphere"),
            "sphere",
            s.WorldObjectKind.OBSTACLE,
            "test",
            state((0.0, 0.0, 5.0)),
            s.SphereGeometry(1.0),
            s.SurfaceAppearance((1.0, 1.0, 1.0), 1.0),
            True,
        )
        hit = VisibilityModule._segment_hit(obstacle, (0.0, 0.0, 0.0), (0.0, 0.0, 10.0))
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertAlmostEqual(hit[0], 0.4)
        self.assertEqual(hit[1].intersection_enu_m, (0.0, 0.0, 4.0))
        inside = VisibilityModule._segment_hit(obstacle, (0.0, 0.0, 5.0), (0.0, 0.0, 5.5))
        assert inside is not None
        self.assertEqual(inside[0], 0.0)
        spec, _ = architecture_specs()
        ground = VisibilityModule._segment_hit(
            spec.scene_definition.terrain, (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)
        )
        assert ground is not None
        self.assertEqual(ground[1].blocker_object_id, spec.scene_definition.terrain.terrain_id)
        self.assertAlmostEqual(ground[0], 0.5)

    def test_distorted_pixel_rays_match_actual_forward_projection(self) -> None:
        scene = snapshot(test_scene(s.SphereGeometry(1.0)))
        camera = replace(
            scene.cameras[0],
            actual_distortion=s.SceneCameraDistortion(
                s.SceneDistortionModel.BROWN_CONRADY, (0.15, -0.03, 0.002), (0.002, -0.003)
            ),
        )
        rays = optical_rays(camera.actual_intrinsics, camera.actual_distortion)
        for u, v in ((0, 0), (63, 47), (32, 24)):
            ray = tuple(float(x) for x in rays[v * 64 + u])
            projection = CameraProjectionModule().project(camera, ray)
            assert projection.centroid_px is not None
            np.testing.assert_allclose(projection.centroid_px, (u, v), atol=1e-6)

    def test_exposure_integration_noise_and_ordinary_frame_truth_separation(self) -> None:
        spec = test_scene(s.SphereGeometry(1.0))
        center = snapshot(spec, 500_000)
        first = snapshot(
            replace(
                spec,
                scene_definition=replace(
                    spec.scene_definition,
                    flying_objects=(
                        replace(
                            spec.scene_definition.flying_objects[0],
                            trajectory=s.TrajectorySpec(
                                s.TrajectoryInterpolation.HOLD,
                                (s.TrajectoryKnot(0, state((-1.0, 0.0, 10.0))),),
                            ),
                        ),
                    ),
                ),
            ),
            250_000,
        )
        last = replace(center, sample_time=s.SimulationTime("structure-clock", 750_000, 0))
        frame = s.FrameImagingInput(
            center.cameras[0].station_id,
            m.SessionId("image"),
            m.CameraRole.MAIN,
            m.FrameId("frame"),
            m.CaptureTime(
                clock_domain_id="structure-clock",
                exposure_start_ns=0,
                exposure_end_ns=1_000_000,
                clock_uncertainty_ns=0,
                rolling_shutter_model="none",
            ),
            m.ConfigDigest("online"),
            (first, center, last),
        )
        imaging = replace(spec.imaging, exposure_samples=2)
        request = s.ImageGenerationRequest("test", 42, imaging, (frame,))
        batch, truth = CameraImagingModule().render(request)
        first_pixels, _ = render_gpu(first, first.cameras[0])
        last_pixels, _ = render_gpu(last, last.cameras[0])
        expected = (first_pixels.astype(np.float64) / 255.0 + last_pixels / 255.0) / 2
        np.testing.assert_array_equal(
            np.frombuffer(batch.frames[0].image.pixels, dtype=np.uint8).reshape(expected.shape),
            np.frombuffer(
                quantize_rgb8(expected, 0.0, 1.0, 0.0, 42, "sensor/station-0/main/frame"),
                dtype=np.uint8,
            ).reshape(expected.shape),
        )
        self.assertGreater(truth.frame_truth[0].targets[0].projected_width_px, 10)
        artifacts = replace(imaging.sensor_artifacts, sensor_noise_std_intensity=0.02)
        noisy = replace(request, imaging_spec=replace(imaging, sensor_artifacts=artifacts))
        self.assertEqual(CameraImagingModule().render(noisy), CameraImagingModule().render(noisy))
        self.assertNotEqual(
            batch.frames[0].image.pixels,
            CameraImagingModule().render(noisy)[0].frames[0].image.pixels,
        )
        with self.assertRaisesRegex(ValueError, "exact exposure-midpoint"):
            CameraImagingModule().render(
                replace(request, frames=(replace(frame, exposure_samples=(first, last)),))
            )

    def test_source_keeps_odd_duration_and_independent_main_aux_times(self) -> None:
        spec = test_scene(s.SphereGeometry(1.0))
        cameras = tuple(
            replace(
                c,
                exposure=replace(
                    c.exposure,
                    exposure_duration_ns=3,
                    first_exposure_start_ns=0 if c.camera_role is m.CameraRole.MAIN else 2,
                    frame_period_ns=4 if c.camera_role is m.CameraRole.MAIN else 6,
                ),
            )
            for c in spec.scene_definition.cameras
        )
        spec = replace(
            spec,
            scene_definition=replace(spec.scene_definition, cameras=cameras),
            timeline=replace(spec.timeline, end_time_ns=12),
        )
        steps = list(demo_source(spec).generate(spec))
        self.assertEqual([step.video.sample_time.time_ns for step in steps], [1, 3, 5, 9])
        self.assertEqual(
            [{f.camera_role for f in step.video.frames} for step in steps],
            [{m.CameraRole.MAIN}, {m.CameraRole.AUXILIARY}, {m.CameraRole.MAIN}, set(m.CameraRole)],
        )
        for step in steps:
            for frame in step.video.frames:
                self.assertEqual(
                    frame.capture_time.exposure_end_ns - frame.capture_time.exposure_start_ns, 3
                )
                self.assertEqual(
                    (frame.capture_time.exposure_end_ns + frame.capture_time.exposure_start_ns)
                    // 2,
                    step.video.sample_time.time_ns,
                )


class PreviewTests(unittest.TestCase):
    def test_preview_encodes_only_requested_station_and_reuses_paused_images(self) -> None:
        runtime = PreviewRuntime(demo_spec(station_count=2, fps=2))
        runtime.control("pause")
        runtime.start()
        self.addCleanup(runtime.close)
        with patch("chongming.preview.encode_video", wraps=encode_video) as encode:
            runtime.control("step")
            with runtime._condition:
                self.assertTrue(
                    runtime._condition.wait_for(lambda: runtime._published is not None, timeout=5)
                )
            self.assertEqual(encode.call_count, 0)
            assert runtime._latest_step is not None
            self.assertEqual(len(runtime._latest_step.video.frames), 4)
            stations = runtime._station_ids
            first = runtime.snapshot(stations[0])["snapshot"]
            self.assertEqual(len(first["video"]), 2)
            self.assertEqual({frame["station_id"] for frame in first["video"]}, {stations[0]})
            self.assertEqual(encode.call_count, 2)
            self.assertEqual(runtime.snapshot(stations[0])["snapshot"], first)
            self.assertEqual(encode.call_count, 2)
            unchanged = runtime.snapshot(stations[0], after_sequence=first["sequence"])
            self.assertEqual(unchanged["state"], "paused")
            self.assertIsNone(unchanged["snapshot"])
            self.assertEqual(encode.call_count, 2)
            second = runtime.snapshot(stations[1])["snapshot"]
            self.assertEqual(second["sequence"], first["sequence"])
            self.assertEqual({frame["station_id"] for frame in second["video"]}, {stations[1]})
            self.assertEqual(encode.call_count, 4)
            with self.assertRaisesRegex(ValueError, "station"):
                runtime.snapshot("unknown-station")
            runtime.control("step")
            with runtime._condition:
                self.assertTrue(
                    runtime._condition.wait_for(
                        lambda: (
                            runtime._published is not None and runtime._published["sequence"] == 2
                        ),
                        timeout=5,
                    )
                )
            self.assertEqual(encode.call_count, 4)
            self.assertEqual(runtime.snapshot(stations[0])["snapshot"]["sequence"], 2)
            self.assertEqual(encode.call_count, 6)

    def wait_snapshot(self, runtime: PreviewRuntime, predicate) -> dict:
        deadline = time.monotonic() + 5
        with runtime._condition:
            ready = runtime._condition.wait_for(
                lambda: predicate(runtime.snapshot()), timeout=max(0.0, deadline - time.monotonic())
            )
        self.assertTrue(ready, runtime.snapshot())
        return runtime.snapshot()

    def test_demo_main_aux_frames_and_lossless_preview_transport(self) -> None:
        spec = demo_spec(station_count=2)
        source = demo_source(spec)
        step = next(iter(source.generate(spec)))
        self.assertEqual(len(step.video.frames), 4)
        self.assertEqual(len(step.truth.logical_scene.flying_objects), 3)
        payload = preview_payload(step, 1, 1)
        for frame, encoded in zip(step.video.frames, payload["video"], strict=True):
            decoded = Image.open(io.BytesIO(base64.b64decode(encoded["png"])))
            self.assertEqual(frame.image.pixel_format, m.PixelFormat.RGB8)
            self.assertEqual(decoded.mode, "RGB")
            self.assertEqual(decoded.tobytes(), frame.image.pixels)
            self.assertNotIn("truth_object_id", encoded)
        rgb = np.frombuffer(step.video.frames[0].image.pixels, dtype=np.uint8).reshape(-1, 3)
        self.assertTrue(np.any(rgb[:, 0] != rgb[:, 2]))
        self.assertNotEqual(source.station_sessions, demo_source(spec).station_sessions)
        for camera in step.truth.logical_scene.cameras:
            rotation = np.asarray(camera.optical_state.rotation_enu_from_body)
            np.testing.assert_allclose(rotation.T @ rotation, np.eye(3), atol=1e-12)
            self.assertAlmostEqual(np.linalg.det(rotation), 1.0)
            if camera.camera_role is m.CameraRole.AUXILIARY:
                self.assertEqual(rotation[2, 2], 0.0)
        np.testing.assert_allclose(
            np.asarray(optical_rotation(0.5, 0.2)).T @ optical_rotation(0.5, 0.2),
            np.eye(3),
            atol=1e-12,
        )

    def test_pause_step_restart_and_http_readers_do_not_advance_time(self) -> None:
        spec = demo_spec(fps=2)
        runtime = PreviewRuntime(spec)
        server = preview_server(runtime, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        runtime.control("pause")
        runtime.start()
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            runtime.control("step")
            first = self.wait_snapshot(runtime, lambda d: d["snapshot"] is not None)
            session = first["snapshot"]["video"][0]["session_id"]
            for _ in range(3):
                with urlopen(url + "/api/preview", timeout=2) as response:
                    self.assertEqual(json.load(response)["snapshot"]["sequence"], 1)
            with urlopen(url + "/api/preview?after=1", timeout=2) as response:
                unchanged = json.load(response)
                self.assertEqual(unchanged["state"], "paused")
                self.assertIsNone(unchanged["snapshot"])
            with self.assertRaises(HTTPError) as invalid_station:
                urlopen(url + "/api/preview?station=absent", timeout=2)
            self.assertEqual(invalid_station.exception.code, 400)
            runtime.control("step")
            second = self.wait_snapshot(runtime, lambda d: d["snapshot"]["sequence"] == 2)
            self.assertEqual(
                second["snapshot"]["time_ns"] - first["snapshot"]["time_ns"], 500_000_000
            )
            body = json.dumps({"action": "restart"}).encode()
            with urlopen(
                Request(
                    url + "/api/control", data=body, headers={"Content-Type": "application/json"}
                ),
                timeout=2,
            ) as response:
                self.assertEqual(response.status, 200)
            restarted = self.wait_snapshot(runtime, lambda d: d["snapshot"]["sequence"] == 3)
            self.assertEqual(restarted["snapshot"]["time_ns"], first["snapshot"]["time_ns"])
            self.assertNotEqual(restarted["snapshot"]["video"][0]["session_id"], session)
            self.assertEqual(
                restarted["snapshot"]["video"][0]["png"], first["snapshot"]["video"][0]["png"]
            )
            with self.assertRaises(HTTPError) as failure:
                urlopen(
                    Request(
                        url + "/api/control",
                        data=body,
                        headers={"Origin": "https://unrelated.example"},
                    ),
                    timeout=2,
                )
            self.assertEqual(failure.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            runtime.close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
