"""Virtual ray errors and held calibration, without starting Godot."""

from __future__ import annotations

import json
import math
import unittest
from dataclasses import replace
from itertools import islice
from pathlib import Path

import numpy as np

from chongming.contracts import model as m
from chongming.contracts import scene as s
from chongming.modules.device import EventSequencerModule
from chongming.modules.scene import VirtualObservationModule, _tangent_basis
from chongming.scenarios.authoring import compile_authoring
from chongming.scenarios.demo import demo_source
from chongming.scenarios.run import _json
from chongming.scenarios.serialization import load_scenario, validate_scenario_spec
from chongming.uncertainty import covariance_factor

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
ANGULAR: m.Matrix2x2 = ((4e-6, 1e-6), (1e-6, 9e-6))
ORIGIN: m.Matrix3x3 = ((0.04, 0.01, 0.0), (0.01, 0.09, 0.0), (0.0, 0.0, 0.0))


def generate(
    request: s.VirtualObservationRequest,
) -> tuple[s.VirtualObservationBatch, s.VirtualObservationTruthBatch]:
    sequencers = tuple(
        EventSequencerModule(
            station_id=binding.station_id,
            station_session_id=binding.virtual_session_id,
            schema_version="event-v1",
        )
        for binding in request.station_sessions
    )
    return VirtualObservationModule(sequencers).generate(request)


def fixture() -> tuple[s.ScenarioSpec, s.VirtualObservationRequest]:
    spec = load_scenario(SCENARIOS / "center_clean.json")
    source = demo_source(spec, video_enabled=False)
    step = next(iter(source.generate(spec)))
    return spec, s.VirtualObservationRequest(
        scenario_id=spec.scenario_id,
        random_seed=spec.random_seed,
        generation_spec=spec.virtual_observations,
        online_station_configs=spec.station_configs,
        frames=step.truth.camera_frames,
        station_sessions=source.station_sessions,
        camera_jitter=spec.perturbations.camera_jitter,
    )


def angle(left: m.Vec3, right: m.Vec3) -> float:
    return math.atan2(float(np.linalg.norm(np.cross(left, right))), float(np.dot(left, right)))


class RayErrorTests(unittest.TestCase):
    def test_clean_baseline_matches_truth_through_the_calibration_interval(self) -> None:
        document = json.loads((SCENARIOS / "center_clean.json").read_text())
        document["fps"] = 1
        spec = compile_authoring(document)
        self.assertFalse(spec.perturbations.camera_jitter)
        self.assertFalse(np.any(spec.virtual_observations.independent_angular_covariance_rad2))
        self.assertFalse(np.any(spec.virtual_observations.independent_origin_covariance_m2))
        count = 0
        for step in demo_source(spec, video_enabled=False).generate(spec):
            truth = {(ray.station_id, ray.truth_object_id): ray for ray in step.truth.rays}
            correspondences = {
                item.ray_event_id: item for item in step.truth.online_correspondences
            }
            self.assertEqual(len(step.packets.rays), 2)
            for ray in step.packets.rays:
                envelope = ray.envelope
                event_id = (envelope.station_id, envelope.station_session_id, envelope.sequence_no)
                object_id = correspondences[event_id].truth_object_id
                assert object_id is not None
                expected = truth[envelope.station_id, object_id]
                self.assertEqual(ray.ray_origin_enu_m, expected.origin_enu_m)
                self.assertLess(
                    angle(ray.ray_direction_enu_unit, expected.direction_enu_unit), 1e-12
                )
                self.assertIs(ray.calibration_usage, m.CalibrationUsage.VALID)
                count += 1
        self.assertEqual(count, 60)

    def test_noise_is_repeatable_order_independent_and_keeps_truth_unchanged(self) -> None:
        _, request = fixture()
        clean, clean_truth = generate(request)
        noisy = replace(
            request,
            generation_spec=replace(
                request.generation_spec,
                independent_angular_covariance_rad2=ANGULAR,
                independent_origin_covariance_m2=ORIGIN,
            ),
        )
        first, noisy_truth = generate(noisy)
        self.assertEqual((first, noisy_truth), generate(noisy))
        self.assertEqual(clean_truth, noisy_truth)
        self.assertEqual(clean.calibration_solutions, first.calibration_solutions)
        reversed_batch, _ = generate(replace(noisy, frames=tuple(reversed(noisy.frames))))
        by_station = {ray.envelope.station_id: ray for ray in reversed_batch.rays}
        changed_seed, _ = generate(replace(noisy, random_seed=request.random_seed + 1))
        for baseline, ray, changed in zip(clean.rays, first.rays, changed_seed.rays, strict=True):
            self.assertNotEqual(baseline.ray_origin_enu_m, ray.ray_origin_enu_m)
            self.assertGreater(
                angle(baseline.ray_direction_enu_unit, ray.ray_direction_enu_unit), 1e-8
            )
            self.assertEqual(ray, by_station[ray.envelope.station_id])
            self.assertNotEqual(ray.ray_direction_enu_unit, changed.ray_direction_enu_unit)
        angular_only, _ = generate(
            replace(
                noisy,
                generation_spec=replace(
                    noisy.generation_spec,
                    independent_origin_covariance_m2=((0.0, 0.0, 0.0),) * 3,
                ),
            )
        )
        self.assertEqual(
            [ray.ray_direction_enu_unit for ray in first.rays],
            [ray.ray_direction_enu_unit for ray in angular_only.rays],
        )

    def test_samples_match_tangent_radians_and_world_metres(self) -> None:
        _, request = fixture()
        frame = next(frame for frame in request.frames if frame.camera_role is m.CameraRole.MAIN)
        clean = generate(replace(request, frames=(frame,)))[0].rays[0]
        count = 3000
        frames = tuple(
            replace(
                frame,
                frame_id=m.FrameId(f"sample-{index}"),
                capture_time=replace(
                    frame.capture_time,
                    exposure_start_ns=index * 1_000_000,
                    exposure_end_ns=index * 1_000_000 + 1000,
                ),
            )
            for index in range(count)
        )
        batch, _ = generate(
            replace(
                request,
                frames=frames,
                generation_spec=replace(
                    request.generation_spec,
                    independent_angular_covariance_rad2=ANGULAR,
                    independent_origin_covariance_m2=ORIGIN,
                ),
            )
        )
        self.assertEqual(len(batch.rays), count)
        nominal = np.array(clean.ray_direction_enu_unit)
        basis = np.array(_tangent_basis(clean.ray_direction_enu_unit))
        directions = np.array([ray.ray_direction_enu_unit for ray in batch.rays])
        theta = np.arctan2(
            np.linalg.norm(np.cross(nominal, directions), axis=1), directions @ nominal
        )
        tangent = (directions @ basis.T) * (theta / np.sin(theta))[:, None]
        origins = np.array([ray.ray_origin_enu_m for ray in batch.rays]) - clean.ray_origin_enu_m
        for samples, covariance in ((tangent, np.array(ANGULAR)), (origins, np.array(ORIGIN))):
            np.testing.assert_allclose(
                samples.mean(axis=0), 0, atol=5 * math.sqrt(float(covariance.max()) / count)
            )
            np.testing.assert_allclose(
                np.cov(samples.T), covariance, atol=float(covariance.max()) * 0.08, rtol=0.08
            )
        self.assertTrue(np.all(origins[:, 2] == 0))
        for ray in batch.rays:
            u = ray.uncertainty.tangent_u_enu_unit
            v = ray.uncertainty.tangent_v_enu_unit
            np.testing.assert_allclose(np.cross(u, v), ray.ray_direction_enu_unit, atol=1e-12)
            self.assertAlmostEqual(float(np.dot(u, v)), 0)
            self.assertAlmostEqual(float(np.linalg.norm(ray.ray_direction_enu_unit)), 1)

    def test_invalid_covariances_fail_at_both_generation_boundaries(self) -> None:
        spec, request = fixture()
        invalid = (
            (((1e-8, 1e-9), (0.0, 1e-8)), "must be symmetric"),
            (((-1e-30, 0.0), (0.0, 0.0)), "must be positive semidefinite"),
            (((1e-8, 2e-8), (2e-8, 1e-8)), "must be positive semidefinite"),
            (((math.nan, 0.0), (0.0, 0.0)), "must be a finite 2x2 covariance"),
        )
        for covariance, reason in invalid:
            observations = replace(
                request.generation_spec, independent_angular_covariance_rad2=covariance
            )
            with self.subTest(covariance=covariance):
                validation_reason = (
                    reason if np.isfinite(covariance).all() else "expected finite number"
                )
                with self.assertRaisesRegex(ValueError, validation_reason):
                    validate_scenario_spec(replace(spec, virtual_observations=observations))
                with self.assertRaisesRegex(ValueError, reason):
                    generate(replace(request, generation_spec=observations))
        bad_origin = replace(
            spec.virtual_observations,
            independent_origin_covariance_m2=((-1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
        )
        bad_calibration = replace(
            spec.virtual_observations,
            calibration_intervals=tuple(
                replace(interval, correction_covariance_rad2=((math.inf, 0.0, 0.0),) * 3)
                for interval in spec.virtual_observations.calibration_intervals
            ),
        )
        for observations, validation_reason, generation_reason in (
            (bad_origin, "must be positive semidefinite", "must be positive semidefinite"),
            (bad_calibration, "expected finite number", "must be a finite 3x3 covariance"),
        ):
            with self.subTest(observations=observations):
                with self.assertRaisesRegex(ValueError, validation_reason):
                    validate_scenario_spec(replace(spec, virtual_observations=observations))
                with self.assertRaisesRegex(ValueError, generation_reason):
                    generate(replace(request, generation_spec=observations))
        with self.assertRaisesRegex(ValueError, "2x2"):
            covariance_factor(((1.0,),), 2, "angular covariance")

    def test_jitter_keeps_one_solution_with_frozen_evidence_and_shared_bound(self) -> None:
        spec = load_scenario(SCENARIOS / "photography.json")
        steps = list(islice(demo_source(spec, video_enabled=False).generate(spec), 50))
        solutions = [solution for step in steps for solution in step.packets.calibration_solutions]
        self.assertEqual(len(solutions), 1)
        solution = solutions[0]
        bound = float(np.linalg.eigvalsh(solution.correction_covariance_enu_rad2).min())
        self.assertGreater(bound, 0)
        errors = []
        for step in steps:
            self.assertIs(step.packets.calibration_states[0].health, m.CalibrationHealth.FROZEN)
            for ray, truth in zip(step.packets.rays, step.truth.rays, strict=True):
                error = angle(ray.ray_direction_enu_unit, truth.direction_enu_unit)
                errors.append(error)
                self.assertLessEqual(error * error, bound + 1e-15)
                self.assertIs(ray.calibration_usage, m.CalibrationUsage.FROZEN)
                self.assertIn("virtual_untracked_camera_jitter", ray.quality_flags)
                self.assertNotIn("virtual_clean_baseline", ray.quality_flags)
                self.assertFalse(np.any(ray.uncertainty.independent_angular_covariance_rad2))
                self.assertEqual(ray.calibration_solution_ref, solution.solution_ref)
                self.assertEqual(
                    ray.uncertainty.shared_calibration_solution_ref, solution.solution_ref
                )
                self.assertEqual(ray.calibration_evidence.last_trusted_time, solution.accepted_time)
            online = _json(step.packets)
            for forbidden in (
                "truth_object_id",
                "actual_camera",
                '"camera_jitter"',
                "angular_amplitude_rad",
            ):
                self.assertNotIn(forbidden, online)
        self.assertGreater(max(errors), 1e-5)

    def test_disabled_auxiliary_only_and_zero_jitter_keep_clean_main_rays(self) -> None:
        spec = load_scenario(SCENARIOS / "photography.json")
        main_jitter = tuple(
            jitter
            for jitter in spec.perturbations.camera_jitter
            if jitter.camera_role is m.CameraRole.MAIN
        )
        variants = (
            (),
            tuple(replace(jitter, camera_role=m.CameraRole.AUXILIARY) for jitter in main_jitter),
            tuple(
                replace(
                    jitter, angular_amplitude_rad=(0.0, 0.0, 0.0), pixel_amplitude_px=(0.0, 0.0)
                )
                for jitter in main_jitter
            ),
        )
        for jitter in variants:
            candidate = replace(
                spec, perturbations=replace(spec.perturbations, camera_jitter=jitter)
            )
            with self.subTest(jitter=jitter):
                for step in islice(
                    demo_source(candidate, video_enabled=False).generate(candidate), 10
                ):
                    self.assertTrue(step.packets.rays)
                    for ray, truth in zip(step.packets.rays, step.truth.rays, strict=True):
                        self.assertLess(
                            angle(ray.ray_direction_enu_unit, truth.direction_enu_unit), 1e-12
                        )
                        self.assertIs(ray.calibration_usage, m.CalibrationUsage.VALID)
                        self.assertNotIn("virtual_untracked_camera_jitter", ray.quality_flags)


if __name__ == "__main__":
    unittest.main()
