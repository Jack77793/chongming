"""Asynchronous ray geometry, explicit bootstrap gates, and prior dependence."""

from __future__ import annotations

import unittest
from dataclasses import asdict, replace
from typing import cast

import numpy as np

from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.modules.fusion_association import CrossStationAssociationModule
from chongming.modules.fusion_geometry import VoxelCandidateModule, _line_fit
from chongming.modules.fusion_motion import velocity_projection
from tests import test_fusion_geometry as helpers

SECOND = 10**9
REFERENCE = 2 * SECOND
VELOCITY: m.Vec3 = (80.0, -35.0, 20.0)


class MotionFusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = helpers.CleanFusionTests(methodName="runTest")
        self.fixture.setUp()
        self.geometry = replace(
            self.fixture.geometry,
            policy=replace(
                self.fixture.geometry.policy,
                maximum_residual_m=1e-6,
                maximum_unaligned_span_ns=1_000_000,
            ),
        )
        self.associator = CrossStationAssociationModule(
            policy=replace(
                self.fixture.associator.policy, temporal_gate_ns=40_000_000, spatial_gate_m=1e-6
            ),
            motion_policy=self.geometry.policy,
        )
        self.voxels = VoxelCandidateModule(
            policy=replace(
                self.fixture.candidate_policy,
                search=replace(self.fixture.search, step_enu_m=(0.02, 0.02, 0.02)),
            ),
            motion_policy=self.geometry.policy,
        )

    def window(
        self,
        *,
        offsets: tuple[int, ...] = (-10_000_000, 0, 10_000_000),
        velocity: m.Vec3 = VELOCITY,
        at_reference: m.Vec3 = helpers.TARGET,
    ) -> f.SealedObservationWindow:
        origins: tuple[m.Vec3, ...] = ((-2.0, -1.0, 0.0), (2.0, -1.0, 0.0), (0.0, 3.0, 0.1))
        rays = []
        for index, offset in enumerate(offsets):
            target = np.asarray(at_reference) + np.asarray(velocity) * offset / SECOND
            ray = self.fixture.ray(
                chr(97 + index),
                origins[index],
                target=(float(target[0]), float(target[1]), float(target[2])),
                offset=REFERENCE - helpers.TIME + offset,
            )
            rays.append(
                replace(ray, arrival_time=replace(ray.arrival_time, time_ns=REFERENCE + 30_000_000))
            )
        baseline = self.fixture.window(tuple(rays))
        return replace(
            baseline,
            key=replace(
                baseline.key, start_ns=REFERENCE - 20_000_000, end_ns=REFERENCE + 20_000_000
            ),
            reference_time_ns=REFERENCE,
        )

    def expected_at(self, time_ns: int) -> m.Vec3:
        point = np.asarray(helpers.TARGET) + np.asarray(VELOCITY) * ((time_ns - REFERENCE) / SECOND)
        return float(point[0]), float(point[1]), float(point[2])

    def prediction(
        self,
        window: f.SealedObservationWindow,
        *,
        velocity: m.Vec3 = VELOCITY,
        track: str = "existing",
    ) -> f.TrackPrediction:
        reference = min(ray.event.capture_time.exposure_start_ns for ray in window.rays)
        return f.TrackPrediction(
            center_session_id=window.key.center_session_id,
            track_id=m.TrackId(track),
            clock_domain_id=window.key.capture_clock_domain_id,
            world_frame_id=self.fixture.search.world_frame_id,
            reference_time_ns=reference,
            valid_until_ns=REFERENCE + SECOND,
            position_enu_m=self.expected_at(reference),
            velocity_enu_mps=velocity,
            conditional_state_covariance_enu=cast(
                m.Matrix6x6,
                tuple(
                    tuple(4.0 if row == column else 0.0 for column in range(6)) for row in range(6)
                ),
            ),
        )

    def test_guided_voxels_and_continuous_fit_align_original_exposures(self) -> None:
        window = self.window()
        original = asdict(window)
        prediction = self.prediction(window)
        raw = self.voxels.propose(window, ())
        self.assertFalse(raw.candidates)
        candidates = self.voxels.propose(window, (prediction,))
        self.assertTrue(candidates.unguided_search_performed)
        self.assertTrue(
            any(
                candidate.origin is f.CandidateOrigin.TRACK_PREDICTION_GATE
                for candidate in candidates.candidates
            )
        )
        association = self.associator.associate(window, candidates, (prediction,))
        self.assertEqual(len(association.selected_groups), 1)
        group = association.selected_groups[0]
        self.assertEqual(len(group.supporting_station_ids), 3)
        self.assertIs(group.motion_alignment_model, f.MotionAlignmentModel.TRACK_PREDICTION)
        self.assertEqual(group.reference_time_ns, REFERENCE - 10_000_000)
        (result,) = self.geometry.refine(window, association, (prediction,)).results
        self.fixture.assert_position(result.measurement, self.expected_at(group.reference_time_ns))
        evidence = result.measurement.motion_alignment
        assert evidence is not None
        projection = np.asarray(evidence.velocity_projection_s)
        np.testing.assert_allclose(
            projection,
            velocity_projection(window.rays, group.reference_time_ns),
            rtol=0,
            atol=1e-15,
        )
        expected = projection @ (4 * np.eye(3)) @ projection.T
        self.assertGreater(float(np.trace(expected)), 0)
        np.testing.assert_allclose(
            np.asarray(result.measurement.conditional_position_covariance_enu_m2),
            expected,
            rtol=0,
            atol=1e-15,
        )
        raw_point, *_ = _line_fit(window.rays)
        np.testing.assert_allclose(
            np.asarray(result.measurement.position_enu_m) + projection @ np.asarray(VELOCITY),
            raw_point,
            rtol=0,
            atol=1e-12,
        )
        self.assertEqual(asdict(window), original)
        self.assertEqual(len(result.shared_installation_state_refs), 3)

    def test_new_target_cannot_average_wide_exposures_even_if_static_lines_intersect(self) -> None:
        window = self.window(velocity=(0.0, 0.0, 0.0))
        candidates = self.voxels.propose(window, ())
        self.assertTrue(candidates.candidates)
        association = self.associator.associate(window, candidates, ())
        self.assertFalse(association.selected_groups)
        self.assertTrue(
            any(
                f.AssociationConflictReason.MOTION_ALIGNMENT_UNAVAILABLE in conflict.reasons
                for conflict in association.conflicts
            )
        )
        unaligned = self.fixture.association(window)
        (result,) = self.geometry.refine(window, unaligned, ()).results
        self.assertEqual(result.reasons, (f.GeometryReason.REFERENCE_TIME_ALIGNMENT_UNAVAILABLE,))
        self.assertIsNone(result.measurement.position_enu_m)
        narrow = self.window(offsets=(0, 100_000, 200_000), velocity=(0.0, 0.0, 0.0))
        association = self.associator.associate(narrow, self.voxels.propose(narrow, ()), ())
        (result,) = self.geometry.refine(narrow, association, ()).results
        self.fixture.assert_position(result.measurement, helpers.TARGET)
        self.assertIs(
            result.motion_alignment_model, f.MotionAlignmentModel.NARROW_WINDOW_WITHOUT_PRIOR
        )
        self.assertIsNone(result.measurement.motion_alignment)

    def test_unknown_expired_or_missing_velocity_uncertainty_is_not_alignment_success(self) -> None:
        window = self.window()
        prediction = self.prediction(window)
        for prior in (
            replace(prediction, conditional_state_covariance_enu=None),
            replace(prediction, valid_until_ns=window.key.start_ns),
            replace(prediction, reference_time_ns=window.key.start_ns - 1),
        ):
            with self.subTest(prior=prior):
                candidates = self.voxels.propose(window, (prior,))
                self.assertFalse(
                    any(
                        item.origin is f.CandidateOrigin.TRACK_PREDICTION_GATE
                        for item in candidates.candidates
                    )
                )
                association = self.associator.associate(window, candidates, (prior,))
                self.assertFalse(association.selected_groups)
        associated = self.associator.associate(
            window, self.voxels.propose(window, (prediction,)), (prediction,)
        )
        (result,) = self.geometry.refine(window, associated, ()).results
        self.assertEqual(result.reasons, (f.GeometryReason.REFERENCE_TIME_ALIGNMENT_UNAVAILABLE,))
        self.assertIsNone(result.measurement.position_enu_m)

    def test_velocity_prior_does_not_bypass_geometric_or_temporal_gates(self) -> None:
        window = self.window()
        wrong = self.prediction(window, velocity=(0.0, 0.0, 0.0))
        association = self.associator.associate(
            window, self.voxels.propose(window, (wrong,)), (wrong,)
        )
        self.assertFalse(association.selected_groups)
        prediction = self.prediction(window)
        strict = replace(
            self.associator, policy=replace(self.associator.policy, temporal_gate_ns=1_000_000)
        )
        rejected = strict.associate(
            window, self.voxels.propose(window, (prediction,)), (prediction,)
        )
        self.assertFalse(rejected.selected_groups)
        self.assertTrue(
            any(
                f.AssociationConflictReason.TEMPORAL_GATE_FAILED in conflict.reasons
                for conflict in rejected.conflicts
            )
        )

    def test_different_alignment_priors_are_not_collapsed_to_one_ray_group(self) -> None:
        window = self.window(offsets=(-10_000_000, 10_000_000), velocity=(0.0, 0.0, 0.0))
        priors = []
        for index, speed in enumerate((-10.0, 10.0)):
            velocity: m.Vec3 = (speed, 0.0, 0.0)
            fitted, *_ = _line_fit(
                window.rays, reference_ns=REFERENCE - 10_000_000, velocity=velocity
            )
            priors.append(
                replace(
                    self.prediction(window, velocity=velocity, track=f"alternative-{index}"),
                    position_enu_m=(float(fitted[0]), float(fitted[1]), float(fitted[2])),
                )
            )
        candidates = self.voxels.propose(window, tuple(priors))
        association = self.associator.associate(window, candidates, tuple(priors))
        self.assertIs(association.status, f.AssociationBatchStatus.ASSOCIATION_AMBIGUOUS)
        self.assertFalse(association.selected_groups)

    def test_unguided_new_target_survives_existing_motion_search(self) -> None:
        window = self.window()
        fresh: m.Vec3 = (-2.0, 2.0, 2.5)
        extra = tuple(
            self.fixture.ray(
                ray.event.envelope.station_id,
                ray.event.ray_origin_enu_m,
                target=fresh,
                sequence=10,
                offset=REFERENCE - helpers.TIME,
            )
            for ray in window.rays
        )
        window = replace(window, rays=(*window.rays, *extra))
        prior = self.prediction(window)
        association = self.associator.associate(
            window, self.voxels.propose(window, (prior,)), (prior,)
        )
        self.assertEqual(len(association.selected_groups), 2)
        measurements = self.geometry.refine(window, association, (prior,)).results
        self.assertEqual(
            sum(item.measurement.motion_alignment is not None for item in measurements), 1
        )
        for item in measurements:
            self.fixture.assert_position(
                item.measurement,
                self.expected_at(item.measurement.reference_time_ns)
                if item.measurement.motion_alignment
                else fresh,
            )

    def test_motion_sweep_keeps_targets_inside_the_volume_at_their_support_epoch(self) -> None:
        window = self.window(at_reference=(3.6, 0.4, 0.5))
        expected: m.Vec3 = (2.8, 0.75, 0.3)
        prior = replace(self.prediction(window), position_enu_m=expected)
        candidates = self.voxels.propose(window, (prior,))
        association = self.associator.associate(window, candidates, (prior,))
        self.assertEqual(len(association.selected_groups), 1)
        self.assertEqual(len(association.selected_groups[0].supporting_station_ids), 3)
        (result,) = self.geometry.refine(window, association, (prior,)).results
        self.fixture.assert_position(result.measurement, expected)
        self.assertEqual(result.measurement.reference_time_ns, REFERENCE - 10_000_000)

    def test_negative_narrow_gate_and_unsupported_speed_model_are_explicit(self) -> None:
        with self.assertRaises(ValueError):
            replace(
                self.geometry, policy=replace(self.geometry.policy, maximum_unaligned_span_ns=-1)
            )
        with self.assertRaises(NotImplementedError):
            replace(
                self.voxels,
                motion_policy=replace(
                    self.geometry.policy,
                    motion_alignment_model=f.MotionAlignmentModel.CONSERVATIVE_SPEED_BOUND,
                ),
            )
