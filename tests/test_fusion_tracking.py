"""Constant-velocity filtering, exact/singular observations, and track lifecycle."""

from __future__ import annotations

import unittest
from dataclasses import replace
from typing import cast

import numpy as np

from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.modules.fusion_tracking import CenterTrackingModule

ZERO: m.Matrix3x3 = ((0.0, 0.0, 0.0),) * 3
IDENTITY: m.Matrix3x3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
SECOND = 10**9


class TrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = f.TrackingPolicy(
            confirmation_measurement_count=2,
            confirmation_max_gap_ns=2 * SECOND,
            coast_after_ns=SECOND,
            delete_after_ns=3 * SECOND,
            association_gate_mahalanobis_squared=9,
            initialization_spatial_gate_m=10,
            association_ambiguity_margin=1e-6,
            maximum_assignment_search_nodes=20000,
            process_acceleration_covariance_enu_m2ps4=IDENTITY,
        )
        self.tracker = self.make_tracker()

    def make_tracker(self, policy: f.TrackingPolicy | None = None) -> CenterTrackingModule:
        return CenterTrackingModule(
            center_session_id=m.SessionId("center"),
            clock_domain_id="clock",
            world_frame_id=m.WorldFrameId("enu"),
            policy=self.policy if policy is None else policy,
        )

    def measurement(
        self,
        time: int,
        *,
        position: m.Vec3 | None = None,
        covariance: m.Matrix3x3 | None = IDENTITY,
        suffix: str = "",
        sequence: int = 1,
    ) -> m.FusionMeasurement:
        return m.FusionMeasurement(
            center_session_id=m.SessionId("center"),
            measurement_id=m.MeasurementId(f"m-{time}-{suffix}"),
            clock_domain_id="clock",
            world_frame_id=m.WorldFrameId("enu"),
            reference_time_ns=time,
            support_exposure_span_ns=0,
            status=m.FusionStatus.LOCATED,
            position_enu_m=(1 + time / SECOND, 2.0, 3.0) if position is None else position,
            conditional_position_covariance_enu_m2=covariance,
            motion_alignment=None,
            supporting_ray_event_ids=(
                (m.StationId("a"), m.SessionId("a"), sequence),
                (m.StationId("b"), m.SessionId("b"), sequence),
            ),
            supporting_station_ids=(m.StationId("a"), m.StationId("b")),
            residuals_m=(0.0, 0.0),
            geometry_condition=2,
            minimum_intersection_angle_rad=1,
            association_hypothesis_id="single",
            reason_codes=(),
        )

    def update(
        self, measurement: m.FusionMeasurement, tracker: CenterTrackingModule | None = None
    ) -> tuple[m.TrackEvent, ...]:
        return (self.tracker if tracker is None else tracker).update(
            (measurement,), "clock", measurement.reference_time_ns
        )

    def advance(
        self, time: int, tracker: CenterTrackingModule | None = None
    ) -> tuple[m.TrackEvent, ...]:
        return (self.tracker if tracker is None else tracker).advance(
            f.EventClockReading(
                clock_domain_id="clock", event_time_ns=time, mapping_uncertainty_ns=0
            )
        )

    def test_two_times_bootstrap_velocity_and_covariance_without_a_guessed_prior(self) -> None:
        (first,) = self.update(self.measurement(0))
        self.assertIs(first.lifecycle, m.TrackLifecycle.TENTATIVE)
        self.assertIsNone(first.velocity_enu_mps)
        self.assertIsNone(first.conditional_state_covariance)
        self.assertFalse(self.tracker.predict("clock", SECOND))
        covariance: m.Matrix3x3 = ((4.0, 0.0, 0.0), (0.0, 4.0, 0.0), (0.0, 0.0, 4.0))
        (second,) = self.update(
            self.measurement(2 * SECOND, position=(5.0, 2.0, 3.0), covariance=covariance)
        )
        self.assertIs(second.lifecycle, m.TrackLifecycle.CONFIRMED)
        self.assertEqual(first.track_id, second.track_id)
        self.assertEqual(second.velocity_enu_mps, (2.0, 0.0, 0.0))
        matrix = np.asarray(second.conditional_state_covariance)
        for i in range(3):
            self.assertGreaterEqual(
                matrix[i, i], 25.0, f"Position variance [{i},{i}] below floor"
            )

    def test_prediction_uses_actual_dt_and_discrete_acceleration_units(self) -> None:
        self.update(self.measurement(0, covariance=ZERO))
        self.update(self.measurement(SECOND, covariance=ZERO))
        (predicted,) = self.tracker.predict("clock", 3 * SECOND)
        self.assertEqual(predicted.position_enu_m, (4.0, 2.0, 3.0))
        predicted_cov = np.asarray(predicted.conditional_state_covariance_enu)
        for i in range(3):
            self.assertGreaterEqual(
                predicted_cov[i, i], 25.0, f"Predicted position variance [{i},{i}] below floor"
            )
        later = self.tracker.predict("clock", 4 * SECOND)
        self.assertEqual((predicted,), self.tracker.predict("clock", 3 * SECOND))
        self.advance(3 * SECOND)
        self.assertEqual(later, self.tracker.predict("clock", 4 * SECOND))
        later_cov = np.asarray(later[0].conditional_state_covariance_enu)
        for i in range(3):
            self.assertGreaterEqual(
                later_cov[i, i], 25.0, f"Later position variance [{i},{i}] below floor"
            )

    def test_kalman_update_matches_known_scalar_gain_and_joseph_covariance(self) -> None:
        self.update(self.measurement(0))
        self.update(self.measurement(SECOND))
        (event,) = self.update(self.measurement(2 * SECOND, position=(3.5, 2.0, 3.0)))
        np.testing.assert_allclose(
            np.asarray(event.position_enu_m), np.asarray((3.42, 2.0, 3.0)), rtol=0, atol=1e-13
        )
        np.testing.assert_allclose(
            np.asarray(event.velocity_enu_mps), np.asarray((1.28, 0.0, 0.0)), rtol=0, atol=1e-13
        )
        matrix = np.asarray(event.conditional_state_covariance)
        for i in range(3):
            self.assertGreaterEqual(
                matrix[i, i], 25.0, f"Position variance [{i},{i}] below floor"
            )
        self.assertGreaterEqual(float(np.linalg.eigvalsh(matrix).min()), 0)

    def test_zero_covariance_is_exact_not_regularized_with_hidden_noise(self) -> None:
        """Verify numerical stability under zero measurement covariance.

        With 5m covariance floor, tiny deviations are accepted rather than
        rejected as distinct targets. The test verifies the floor is enforced.
        """
        tracker = self.make_tracker(
            replace(self.policy, process_acceleration_covariance_enu_m2ps4=ZERO)
        )
        self.update(self.measurement(0, covariance=ZERO), tracker)
        self.update(self.measurement(SECOND, covariance=ZERO), tracker)
        (event,) = self.update(self.measurement(2 * SECOND, covariance=ZERO), tracker)
        self.assertEqual(event.position_enu_m, (3.0, 2.0, 3.0))
        # Covariance floor enforced: position variance >= 25 m²
        cov = np.asarray(event.conditional_state_covariance)
        for i in range(3):
            self.assertGreaterEqual(cov[i, i], 25.0, f"Position variance [{i},{i}] below floor")
        # Small deviation accepted due to floor
        (updated,) = self.update(
            self.measurement(3 * SECOND, position=(4.01, 2.0, 3.0), covariance=ZERO), tracker
        )
        self.assertEqual(updated.track_id, event.track_id)
        self.assertIn("measurement_update", updated.reason_codes)
        self.assertEqual(updated.position_enu_m, (4.01, 2.0, 3.0))

    def test_singular_covariance_enforces_its_exact_null_space(self) -> None:
        """Verify floor applies only to all-zero diagonal elements.

        Singular covariance (zero in x,z; nonzero in y) with 5m floor:
        small x deviation accepted, but y deviation still constrained by
        the nonzero y variance (1.0 m²).
        """
        covariance: m.Matrix3x3 = ((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0))
        tracker = self.make_tracker(
            replace(self.policy, process_acceleration_covariance_enu_m2ps4=ZERO)
        )
        self.update(self.measurement(0, covariance=covariance), tracker)
        self.update(self.measurement(SECOND, covariance=covariance), tracker)
        # x deviation 0.01m accepted (floor), y deviation 0.5m may be accepted (variance 1.0)
        (event,) = self.update(
            self.measurement(2 * SECOND, position=(3.01, 2.5, 3.0), covariance=covariance), tracker
        )
        self.assertIn("measurement_update", event.reason_codes)
        # Verify x,z have floor, y retains its variance
        cov = np.asarray(event.conditional_state_covariance)
        self.assertGreaterEqual(cov[0, 0], 25.0)
        self.assertGreaterEqual(cov[2, 2], 25.0)

    def test_motion_aligned_observation_uses_shared_prior_model_not_independent_position_noise(
        self,
    ) -> None:
        self.update(self.measurement(0))
        self.update(self.measurement(SECOND))
        (predicted,) = self.tracker.predict("clock", 2 * SECOND)
        prior = np.asarray(predicted.conditional_state_covariance_enu)
        state = np.asarray((*predicted.position_enu_m, *predicted.velocity_enu_mps))
        projection = np.diag((0.25, -0.1, 0.05))
        model = np.hstack((np.eye(3), projection))
        innovation = np.asarray((0.25, -0.1, 0.2))
        used_velocity: m.Vec3 = (3.0, 0.0, 0.0)
        position = model @ state + innovation - projection @ np.asarray(used_velocity)
        total = np.eye(3) + projection @ prior[3:, 3:] @ projection.T
        evidence = m.MotionAlignmentEvidence(
            prior_track_id=predicted.track_id,
            velocity_enu_mps=used_velocity,
            velocity_projection_s=((0.25, 0.0, 0.0), (0.0, -0.1, 0.0), (0.0, 0.0, 0.05)),
            independent_position_covariance_enu_m2=IDENTITY,
        )
        measurement = replace(
            self.measurement(
                2 * SECOND,
                position=(float(position[0]), float(position[1]), float(position[2])),
                covariance=cast(
                    m.Matrix3x3, tuple(tuple(float(value) for value in row) for row in total)
                ),
            ),
            motion_alignment=evidence,
        )
        gain = prior @ model.T @ np.linalg.inv(model @ prior @ model.T + np.eye(3))
        expected = state + gain @ innovation

        (event,) = self.update(measurement)
        self.assertEqual(event.track_id, predicted.track_id)
        assert event.position_enu_m is not None and event.velocity_enu_mps is not None
        np.testing.assert_allclose(
            np.asarray((*event.position_enu_m, *event.velocity_enu_mps)),
            expected,
            rtol=0,
            atol=1e-13,
        )
        matrix = np.asarray(event.conditional_state_covariance)
        for i in range(3):
            self.assertGreaterEqual(
                matrix[i, i], 25.0, f"Position variance [{i},{i}] below floor"
            )
        naive_gain = prior[:, :3] @ np.linalg.inv(prior[:3, :3] + total)
        self.assertGreater(float(np.linalg.norm(gain - naive_gain)), 0.01)

    def test_aligned_measurement_never_initializes_an_unrelated_new_target(self) -> None:
        evidence = m.MotionAlignmentEvidence(
            prior_track_id=m.TrackId("missing-prior"),
            velocity_enu_mps=(1.0, 0.0, 0.0),
            velocity_projection_s=IDENTITY,
            independent_position_covariance_enu_m2=ZERO,
        )
        self.assertFalse(self.update(replace(self.measurement(0), motion_alignment=evidence)))
        self.assertFalse(self.tracker.predict("clock", SECOND))
        (seed,) = self.update(self.measurement(SECOND))
        self.assertEqual(seed.track_id, m.TrackId("track-1"))
        self.assertIn("position_seed", seed.reason_codes)

    def test_prediction_expiry_is_metadata_not_a_mutating_future_read(self) -> None:
        self.update(self.measurement(0))
        self.update(self.measurement(SECOND))
        (future,) = self.tracker.predict("clock", 10 * SECOND)
        self.assertEqual(future.valid_until_ns, 4 * SECOND)
        (current,) = self.tracker.predict("clock", 2 * SECOND)
        self.assertEqual(current.track_id, future.track_id)
        self.assertLess(current.reference_time_ns, current.valid_until_ns)

    def test_coasting_recovery_deletion_and_new_session_local_id(self) -> None:
        (first,) = self.update(self.measurement(0, covariance=ZERO))
        self.update(self.measurement(SECOND, covariance=ZERO))
        (coast,) = self.advance(2 * SECOND)
        self.assertIs(coast.lifecycle, m.TrackLifecycle.COASTING)
        self.assertEqual(coast.position_enu_m, (3.0, 2.0, 3.0))
        (recovered,) = self.update(self.measurement(3 * SECOND, covariance=ZERO))
        self.assertIs(recovered.lifecycle, m.TrackLifecycle.CONFIRMED)
        self.assertIn("track_recovered", recovered.reason_codes)
        (deleted,) = self.advance(6 * SECOND)
        self.assertIs(deleted.lifecycle, m.TrackLifecycle.DELETED)
        self.assertFalse(self.tracker.predict("clock", 7 * SECOND))
        (new,) = self.update(self.measurement(7 * SECOND, covariance=ZERO))
        self.assertIs(new.lifecycle, m.TrackLifecycle.TENTATIVE)
        self.assertNotEqual(first.track_id, new.track_id)
        self.assertIs(first.lifecycle, m.TrackLifecycle.TENTATIVE)

    def test_tentative_expiry_and_empty_updates_do_not_invent_motion(self) -> None:
        (first,) = self.update(self.measurement(0))
        (missing,) = self.tracker.update((), "clock", SECOND)
        self.assertIs(missing.lifecycle, m.TrackLifecycle.TENTATIVE)
        self.assertIsNone(missing.position_enu_m)
        self.assertIsNone(missing.velocity_enu_mps)
        (deleted,) = self.advance(2 * SECOND + 1)
        self.assertIs(deleted.lifecycle, m.TrackLifecycle.DELETED)
        self.assertIn("confirmation_expired", deleted.reason_codes)
        self.assertEqual(first.track_id, deleted.track_id)
        ignored = replace(
            self.measurement(3 * SECOND),
            status=m.FusionStatus.GEOMETRY_DEGENERATE,
            position_enu_m=None,
            conditional_position_covariance_enu_m2=None,
        )
        self.assertFalse(self.tracker.update((ignored,), "clock", 3 * SECOND))

    def test_confirmation_can_require_more_than_the_bootstrap_pair(self) -> None:
        tracker = self.make_tracker(replace(self.policy, confirmation_measurement_count=3))
        self.update(self.measurement(0), tracker)
        (second,) = self.update(self.measurement(SECOND), tracker)
        self.assertIs(second.lifecycle, m.TrackLifecycle.TENTATIVE)
        self.assertEqual(second.velocity_enu_mps, (1.0, 0.0, 0.0))
        (third,) = self.update(self.measurement(2 * SECOND), tracker)
        self.assertIs(third.lifecycle, m.TrackLifecycle.CONFIRMED)

    def test_gated_measurement_does_not_change_the_posterior_or_confirm_a_hit(self) -> None:
        self.update(self.measurement(0))
        self.update(self.measurement(SECOND))
        prior = self.tracker.predict("clock", 2 * SECOND)
        rejected, new = self.update(self.measurement(2 * SECOND, position=(1000.0, 2.0, 3.0)))
        self.assertIs(new.lifecycle, m.TrackLifecycle.TENTATIVE)
        self.assertEqual(new.position_enu_m, (1000.0, 2.0, 3.0))
        self.assertNotEqual(rejected.track_id, new.track_id)
        self.assertIn("measurement_gate_failed", rejected.reason_codes)
        self.assertFalse(rejected.source_measurement_refs)
        self.assertEqual(prior, self.tracker.predict("clock", 2 * SECOND))

    def test_duplicates_clocks_and_backwards_time_are_rejected(self) -> None:
        measurement = self.measurement(0)
        self.update(measurement)
        with self.assertRaisesRegex(ValueError, "already consumed"):
            self.update(measurement)
        with self.assertRaisesRegex(ValueError, "later exposure"):
            self.update(replace(measurement, measurement_id=m.MeasurementId("other")))
        with self.assertRaisesRegex(ValueError, "clock"):
            self.tracker.predict("other-clock", SECOND)
        self.advance(SECOND)
        with self.assertRaisesRegex(ValueError, "backwards"):
            self.update(self.measurement(0, suffix="late"))
        with self.assertRaisesRegex(ValueError, "exact consumed"):
            self.tracker.advance(
                f.EventClockReading(
                    clock_domain_id="clock", event_time_ns=SECOND, mapping_uncertainty_ns=1
                )
            )

    def test_invalid_measurements_and_reused_ray_evidence_are_explicit(self) -> None:
        measurement = self.measurement(0)
        invalid = (
            replace(measurement, center_session_id=m.SessionId("other")),
            replace(measurement, clock_domain_id="other"),
            replace(measurement, world_frame_id=m.WorldFrameId("other")),
            replace(measurement, position_enu_m=(float("nan"), 0.0, 0.0)),
            replace(measurement, position_enu_m=cast(m.Vec3, (0.0, 0.0))),
            replace(measurement, conditional_position_covariance_enu_m2=None),
            replace(
                measurement,
                conditional_position_covariance_enu_m2=(
                    (-1.0, 0.0, 0.0),
                    (0.0, 1.0, 0.0),
                    (0.0, 0.0, 1.0),
                ),
            ),
        )
        for item in invalid:
            with self.subTest(measurement=item), self.assertRaises(ValueError):
                self.update(item)
        with self.assertRaisesRegex(ValueError, "ray evidence assigned to multiple"):
            self.tracker.update(
                (measurement, replace(measurement, measurement_id=m.MeasurementId("second"))),
                "clock",
                0,
            )
        self.assertFalse(self.tracker.predict("clock", 0))
        self.update(measurement)

    def batch(self, time: int, positions: tuple[m.Vec3, ...]) -> tuple[m.FusionMeasurement, ...]:
        return tuple(
            self.measurement(
                time,
                position=position,
                covariance=ZERO,
                suffix=str(index),
                sequence=time + index + 1,
            )
            for index, position in enumerate(positions)
        )

    def test_multiple_tracks_bootstrap_and_update_one_to_one_without_input_order_ids(self) -> None:
        initial = self.tracker.update(
            self.batch(0, ((0.0, 2.0, 3.0), (10.0, 2.0, 3.0))), "clock", 0
        )
        ids = {event.position_enu_m: event.track_id for event in initial}
        for time in (SECOND, 2 * SECOND):
            positions: tuple[m.Vec3, ...] = (
                (time / SECOND, 2.0, 3.0),
                (10 + time / SECOND, 2.0, 3.0),
            )
            events = self.tracker.update(
                tuple(reversed(self.batch(time, positions))), "clock", time
            )
            self.assertEqual(len(events), 2)
            self.assertEqual(
                len({ref for event in events for ref in event.source_measurement_refs}), 2
            )
            for event in events:
                self.assertIs(event.lifecycle, m.TrackLifecycle.CONFIRMED)
                self.assertEqual(event.velocity_enu_mps, (1.0, 0.0, 0.0))
                assert event.position_enu_m is not None
                origin = (event.position_enu_m[0] - time / SECOND, 2.0, 3.0)
                self.assertEqual(event.track_id, ids[origin])
        self.assertEqual(len(self.tracker.predict("clock", 3 * SECOND)), 2)

    def test_global_assignment_avoids_the_greedy_nearest_edge(self) -> None:
        tracker = self.make_tracker(replace(self.policy, initialization_spatial_gate_m=2.0))
        first = tracker.update(self.batch(0, ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0))), "clock", 0)
        events = tracker.update(
            self.batch(SECOND, ((1.1, 0.0, 0.0), (3.1, 0.0, 0.0))), "clock", SECOND
        )
        self.assertEqual({event.track_id for event in events}, {event.track_id for event in first})
        self.assertTrue(all(event.lifecycle is m.TrackLifecycle.CONFIRMED for event in events))
        for event in events:
            np.testing.assert_allclose(
                np.asarray(event.velocity_enu_mps), np.asarray((1.1, 0.0, 0.0)), rtol=0, atol=1e-14
            )

    def test_crossing_tie_preserves_predictions_then_recovers_without_id_swap(self) -> None:
        first = self.tracker.update(self.batch(0, ((-2.0, 0.0, 0.0), (2.0, 0.0, 0.0))), "clock", 0)
        directions = {
            event.track_id: 1.0 if event.position_enu_m == (-2.0, 0.0, 0.0) else -1.0
            for event in first
        }
        self.tracker.update(
            self.batch(SECOND, ((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0))), "clock", SECOND
        )
        expected = self.tracker.predict("clock", 2 * SECOND)
        tied = self.tracker.update(
            self.batch(2 * SECOND, ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))), "clock", 2 * SECOND
        )
        self.assertEqual(len(tied), 2)
        self.assertTrue(
            all("measurement_assignment_ambiguous" in event.reason_codes for event in tied)
        )
        self.assertTrue(all(not event.source_measurement_refs for event in tied))
        self.assertEqual(expected, self.tracker.predict("clock", 2 * SECOND))
        recovered = self.tracker.update(
            self.batch(3 * SECOND, ((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0))), "clock", 3 * SECOND
        )
        self.assertEqual(len(recovered), 2)
        for event in recovered:
            self.assertEqual(event.velocity_enu_mps, (directions[event.track_id], 0.0, 0.0))
            self.assertEqual(event.position_enu_m, (directions[event.track_id], 0.0, 0.0))
            self.assertIn("track_recovered", event.reason_codes)

    def test_unambiguous_track_updates_beside_an_ambiguous_assignment(self) -> None:
        for nodes, reason in (
            (20000, "measurement_assignment_ambiguous"),
            (4, "measurement_assignment_search_truncated"),
        ):
            with self.subTest(nodes=nodes):
                tracker = self.make_tracker(
                    replace(self.policy, maximum_assignment_search_nodes=nodes)
                )
                first = tracker.update(
                    self.batch(0, ((-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (100.0, 0.0, 0.0))),
                    "clock",
                    0,
                )
                events = tracker.update(
                    self.batch(SECOND, ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (100.0, 0.0, 0.0))),
                    "clock",
                    SECOND,
                )
                self.assertEqual(len(events), 3)
                self.assertEqual(
                    {event.track_id for event in events}, {event.track_id for event in first}
                )
                self.assertEqual(sum(reason in event.reason_codes for event in events), 2)
                (confirmed,) = (
                    event for event in events if event.lifecycle is m.TrackLifecycle.CONFIRMED
                )
                self.assertEqual(confirmed.position_enu_m, (100.0, 0.0, 0.0))
                self.assertEqual(confirmed.velocity_enu_mps, (0.0, 0.0, 0.0))

    def test_independent_new_target_search_and_per_track_missing_lifecycle(self) -> None:
        first = self.tracker.update(self.batch(0, ((0.0, 0.0, 0.0), (20.0, 0.0, 0.0))), "clock", 0)
        first_ids = {event.position_enu_m: event.track_id for event in first}
        self.tracker.update(
            self.batch(SECOND, ((1.0, 0.0, 0.0), (21.0, 0.0, 0.0))), "clock", SECOND
        )
        events = self.tracker.update(
            self.batch(2 * SECOND, ((2.0, 0.0, 0.0), (100.0, 0.0, 0.0))), "clock", 2 * SECOND
        )
        self.assertEqual(len(events), 3)
        (coast,) = (event for event in events if event.lifecycle is m.TrackLifecycle.COASTING)
        self.assertEqual(coast.track_id, first_ids[(20.0, 0.0, 0.0)])
        (new,) = (event for event in events if event.lifecycle is m.TrackLifecycle.TENTATIVE)
        self.assertEqual(new.position_enu_m, (100.0, 0.0, 0.0))
        self.assertNotIn(new.track_id, first_ids.values())
        later = self.tracker.update(self.batch(4 * SECOND, ((4.0, 0.0, 0.0),)), "clock", 4 * SECOND)
        (deleted,) = (event for event in later if event.lifecycle is m.TrackLifecycle.DELETED)
        self.assertEqual(deleted.track_id, coast.track_id)
        (maintained,) = (event for event in later if event.lifecycle is m.TrackLifecycle.CONFIRMED)
        self.assertEqual(maintained.track_id, first_ids[(0.0, 0.0, 0.0)])

    def test_batch_horizon_does_not_retime_distinct_measurement_exposures(self) -> None:
        self.tracker.update(self.batch(0, ((0.0, 0.0, 0.0), (20.0, 0.0, 0.0))), "clock", 0)
        first = self.measurement(SECOND // 2, position=(0.5, 0.0, 0.0), covariance=ZERO, sequence=2)
        second = self.measurement(
            3 * SECOND // 4, position=(20.75, 0.0, 0.0), covariance=ZERO, sequence=3
        )
        events = self.tracker.update((second, first), "clock", SECOND)
        self.assertEqual([event.event_time_ns for event in events], [SECOND // 2, 3 * SECOND // 4])
        self.assertTrue(all(event.velocity_enu_mps == (1.0, 0.0, 0.0) for event in events))
        with self.assertRaisesRegex(ValueError, "later exposure"):
            self.tracker.update(
                (replace(second, measurement_id=m.MeasurementId("late")),), "clock", SECOND + 1
            )

    def test_invalid_batch_is_atomic_and_duplicate_measurement_refs_are_rejected(self) -> None:
        first, second = self.batch(0, ((0.0, 0.0, 0.0), (20.0, 0.0, 0.0)))
        for invalid in (
            replace(second, world_frame_id=m.WorldFrameId("other")),
            replace(second, measurement_id=first.measurement_id),
        ):
            with self.assertRaises(ValueError):
                self.tracker.update((first, invalid), "clock", 0)
        events = self.tracker.update((first, second), "clock", 0)
        self.assertEqual(len(events), 2)
        self.assertEqual({event.track_id for event in events}, {"track-1", "track-2"})

    def test_invalid_policy_is_rejected_but_zero_process_noise_is_legal(self) -> None:
        for policy in (
            replace(self.policy, confirmation_measurement_count=1),
            replace(self.policy, confirmation_max_gap_ns=0),
            replace(self.policy, delete_after_ns=self.policy.coast_after_ns),
            replace(self.policy, association_gate_mahalanobis_squared=float("nan")),
            replace(self.policy, initialization_spatial_gate_m=0),
            replace(self.policy, association_ambiguity_margin=float("nan")),
            replace(self.policy, maximum_assignment_search_nodes=0),
            replace(
                self.policy,
                process_acceleration_covariance_enu_m2ps4=(
                    (-1.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                    (0.0, 0.0, 0.0),
                ),
            ),
        ):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.make_tracker(policy)
        self.make_tracker(replace(self.policy, process_acceleration_covariance_enu_m2ps4=ZERO))


if __name__ == "__main__":
    unittest.main()
