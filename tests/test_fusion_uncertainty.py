"""Independent residual noise, exact constraints, robust fitting and shared errors."""

from __future__ import annotations

import json
import unittest
from dataclasses import asdict, replace
from typing import cast

import numpy as np

from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.modules.fusion import CalibrationWeightingModule
from chongming.modules.fusion_estimation import residual_covariances
from chongming.modules.fusion_geometry import (
    GeometryRefinementModule,
    _line_fit,
    _ray_voxels,
    _uncertain_voxels,
)
from chongming.modules.fusion_motion import matrix3
from tests import test_fusion_geometry as helpers

ZERO: m.Matrix3x3 = ((0.0, 0.0, 0.0),) * 3
IDENTITY: m.Matrix3x3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class UncertainFusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = helpers.CleanFusionTests(methodName="runTest")
        self.fixture.setUp()
        # This suite permits the deliberate 1.5 m outlier; the separate quality
        # test verifies that robust downweighting cannot bypass a tighter limit.
        self.geometry = replace(
            self.fixture.geometry,
            policy=replace(self.fixture.geometry.policy, maximum_residual_m=2),
        )

    def noisy(
        self,
        ray: f.AdmittedRay,
        *,
        variance: float = 0.01,
        angular: m.Matrix2x2 = ((0.0, 0.0), (0.0, 0.0)),
        origin: m.Matrix3x3 | None = None,
        usage: m.CalibrationUsage = m.CalibrationUsage.VALID,
    ) -> f.AdmittedRay:
        return replace(
            ray,
            event=replace(
                ray.event,
                calibration_usage=usage,
                uncertainty=replace(
                    ray.event.uncertainty,
                    independent_angular_covariance_rad2=angular,
                    independent_origin_covariance_m2=matrix3(variance * np.eye(3))
                    if origin is None
                    else origin,
                ),
            ),
        )

    def result(
        self, rays: tuple[f.AdmittedRay, ...], geometry: GeometryRefinementModule | None = None
    ) -> f.GeometryResult:
        module = self.geometry if geometry is None else geometry
        window = self.fixture.window(rays)
        return module.refine(window, self.fixture.association(window), ()).results[0]

    def test_residual_covariance_uses_tangent_axes_depth_and_origin_cross_terms(self) -> None:
        angular: m.Matrix2x2 = ((0.04, 0.01), (0.01, 0.09))
        origin: m.Matrix3x3 = ((1.0, 0.2, 0.1), (0.2, 2.0, -0.3), (0.1, -0.3, 3.0))
        ray = self.noisy(self.fixture.rays()[0], angular=angular, origin=origin)
        basis = np.asarray(
            (ray.event.uncertainty.tangent_u_enu_unit, ray.event.uncertainty.tangent_v_enu_unit)
        )
        for depth in (5.0, 10.0):
            (actual,) = residual_covariances((ray,), np.asarray([depth]), (0.25,))
            expected = (
                depth**2 * np.asarray(angular) + basis @ np.asarray(origin) @ basis.T
            ) / 0.25
            np.testing.assert_allclose(actual, expected, rtol=1e-14, atol=1e-14)

    def test_weighted_solution_and_covariance_match_an_independent_normal_equation(self) -> None:
        rays = tuple(
            self.noisy(ray, variance=variance)
            for ray, variance in zip(
                (
                    self.fixture.ray("a", (-2.0, -1.0, 0.0)),
                    self.fixture.ray("b", (2.0, -1.0, 0.0)),
                    self.fixture.ray("c", (0.0, 3.0, 0.1), target=(1.37, 0.42, 0.59)),
                ),
                (1e-4, 1e-4, 1.0),
                strict=True,
            )
        )
        normal, rhs = np.zeros((3, 3)), np.zeros(3)
        for ray, variance in zip(rays, (1e-4, 1e-4, 1.0), strict=True):
            direction = np.asarray(ray.event.ray_direction_enu_unit)
            projector = np.eye(3) - np.outer(direction, direction)
            normal += projector / variance
            rhs += projector @ np.asarray(ray.event.ray_origin_enu_m) / variance
        result = self.result(rays)
        expected = np.linalg.solve(normal, rhs)
        self.fixture.assert_position(result.measurement, cast(m.Vec3, tuple(expected)), atol=1e-12)
        np.testing.assert_allclose(
            np.asarray(result.measurement.conditional_position_covariance_enu_m2),
            np.linalg.inv(normal),
            rtol=1e-12,
            atol=1e-15,
        )
        plain, *_ = _line_fit(rays)
        self.assertLess(
            np.linalg.norm(expected - helpers.TARGET), np.linalg.norm(plain - helpers.TARGET) / 100
        )
        self.assertTrue(result.solver_converged)
        self.assertTrue(all(value is not None for value in result.normalized_residuals))

    def test_singular_noise_retains_exact_axes_without_regularization(self) -> None:
        rays = (
            self.noisy(
                self.fixture.ray("a", (0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0)),
                origin=((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0)),
            ),
            self.noisy(
                self.fixture.ray("b", (1.0, -1.0, 0.0), direction=(0.0, 1.0, 0.0)),
                origin=((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            ),
        )
        result = self.result(rays)
        self.fixture.assert_position(result.measurement, (1.0, 0.0, 0.0))
        np.testing.assert_allclose(
            np.asarray(result.measurement.conditional_position_covariance_enu_m2),
            np.diag((1.0, 1.0, 0.0)),
            rtol=0,
            atol=1e-15,
        )
        exact = self.result(self.fixture.rays())
        self.assertEqual(exact.measurement.conditional_position_covariance_enu_m2, ZERO)

    def test_weighted_condition_rejects_an_almost_unconstrained_axis(self) -> None:
        rays = (
            self.noisy(
                self.fixture.ray("a", (0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0)), variance=1e-6
            ),
            self.noisy(
                self.fixture.ray("b", (0.1, 0.0, 0.0), direction=(1.0, 0.0, 0.0)), variance=1e-6
            ),
            self.noisy(
                self.fixture.ray("c", (1.0, -1.0, 0.0), direction=(0.0, 1.0, 0.0)), variance=1e6
            ),
        )
        result = self.result(rays)
        self.assertIn(f.GeometryReason.POOR_CONDITION, result.reasons)
        self.assertIsNone(result.measurement.position_enu_m)
        self.assertIsNone(result.measurement.conditional_position_covariance_enu_m2)

    def test_one_exact_ray_constrains_two_axes_of_a_noisy_fit(self) -> None:
        first = self.fixture.ray("a", (0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0))
        other = self.noisy(self.fixture.ray("b", (0.0, -1.0, 0.0), target=(1.2, 0.2, 0.3)))
        result = self.result((first, other))
        self.assertIs(result.measurement.status, m.FusionStatus.LOCATED)
        point = np.asarray(result.measurement.position_enu_m)
        covariance = np.asarray(result.measurement.conditional_position_covariance_enu_m2)
        np.testing.assert_allclose(point[1:], 0, rtol=0, atol=1e-15)
        np.testing.assert_allclose(covariance[1:], 0, rtol=0, atol=1e-15)
        self.assertGreater(covariance[0, 0], 0)

    def test_conflicting_exact_rays_are_rejected_even_with_huber(self) -> None:
        rays = (
            self.fixture.ray("a", (0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0)),
            self.fixture.ray("b", (1.0, -1.0, 0.2), direction=(0.0, 1.0, 0.0)),
        )
        for loss in ("least_squares", "huber"):
            module = replace(
                self.geometry, policy=replace(self.geometry.policy, robust_loss_definition=loss)
            )
            result = self.result(rays, module)
            self.assertIn(f.GeometryReason.INCONSISTENT_EXACT_CONSTRAINTS, result.reasons)
            self.assertIsNone(result.measurement.position_enu_m)
            self.assertIsNone(result.measurement.conditional_position_covariance_enu_m2)
            self.assertIn(None, result.normalized_residuals)
            json.dumps(asdict(result), allow_nan=False)

    def outlier_rays(self) -> tuple[f.AdmittedRay, ...]:
        return tuple(
            self.noisy(ray, variance=0.0025)
            for ray in (
                *self.fixture.rays(),
                self.fixture.ray("d", (0.0, -1.0, 2.0), target=(1.87, 0.42, 0.59)),
            )
        )

    def test_huber_downweights_an_outlier_and_reports_local_covariance(self) -> None:
        rays = self.outlier_rays()
        plain = self.result(rays)
        robust = self.result(
            rays,
            replace(
                self.geometry, policy=replace(self.geometry.policy, robust_loss_definition="huber")
            ),
        )
        self.assertIs(robust.measurement.status, m.FusionStatus.LOCATED)
        plain_error = np.linalg.norm(np.asarray(plain.measurement.position_enu_m) - helpers.TARGET)
        robust_error = np.linalg.norm(
            np.asarray(robust.measurement.position_enu_m) - helpers.TARGET
        )
        self.assertGreater(plain_error, 0.3)
        self.assertLess(robust_error, 0.1)
        self.assertLess(robust.robust_weights[-1], 0.1)
        self.assertTrue(all(weight == 1 for weight in robust.robust_weights[:-1]))
        covariance = np.asarray(robust.measurement.conditional_position_covariance_enu_m2)
        plain_covariance = np.asarray(plain.measurement.conditional_position_covariance_enu_m2)
        self.assertGreaterEqual(
            float(np.linalg.eigvalsh(covariance - plain_covariance).min()), -1e-14
        )
        self.assertTrue(robust.solver_converged)
        self.assertGreater(robust.solver_iterations, 1)

    def test_association_exposes_weighted_solver_failures_without_selecting_groups(self) -> None:
        window = self.fixture.window(self.outlier_rays())
        candidates = self.fixture.voxels.propose(window, ())
        associator = replace(
            self.fixture.associator,
            policy=replace(self.fixture.associator.policy, minimum_independent_stations=4),
            motion_policy=replace(
                self.geometry.policy, robust_loss_definition="huber", maximum_solver_iterations=1
            ),
        )
        association = associator.associate(window, candidates, ())
        self.assertFalse(association.selected_groups)
        self.assertTrue(
            any(
                f.AssociationConflictReason.SOLVER_NOT_CONVERGED in conflict.reasons
                for conflict in association.conflicts
            )
        )
        covariance: m.Matrix3x3 = ((0.01, 0.0, 0.0), (0.0, 0.01, 0.0), (0.0, 0.0, 0.0))
        rays = (
            self.noisy(
                self.fixture.ray("a", (0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0)), origin=covariance
            ),
            self.noisy(
                self.fixture.ray("b", (1.0, -1.0, 0.2), direction=(0.0, 1.0, 0.0)),
                origin=covariance,
            ),
        )
        window = self.fixture.window(rays)
        association = self.fixture.associator.associate(
            window, self.fixture.voxels.propose(window, ()), ()
        )
        self.assertFalse(association.selected_groups)
        self.assertTrue(
            any(
                f.AssociationConflictReason.INCONSISTENT_EXACT_CONSTRAINTS in conflict.reasons
                for conflict in association.conflicts
            )
        )

    def test_near_unit_admitted_directions_keep_metric_residual_and_range_units(self) -> None:
        rays = tuple(
            self.noisy(
                replace(
                    ray,
                    event=replace(
                        ray.event,
                        ray_direction_enu_unit=cast(
                            m.Vec3, tuple(1.0000005 * np.asarray(ray.event.ray_direction_enu_unit))
                        ),
                    ),
                )
            )
            for ray in self.fixture.rays()
        )
        result = self.result(rays)
        self.fixture.assert_position(result.measurement, helpers.TARGET)
        np.testing.assert_allclose(result.measurement.residuals_m, 0, rtol=0, atol=1e-14)

    def test_huber_and_weighted_fit_are_permutation_invariant(self) -> None:
        rays = self.outlier_rays()
        for loss in ("least_squares", "huber"):
            module = replace(
                self.geometry, policy=replace(self.geometry.policy, robust_loss_definition=loss)
            )
            forward = self.result(rays, module)
            reverse = self.result(tuple(reversed(rays)), module)
            np.testing.assert_allclose(
                np.asarray(forward.measurement.position_enu_m),
                np.asarray(reverse.measurement.position_enu_m),
                rtol=0,
                atol=1e-12,
            )
            np.testing.assert_allclose(
                np.asarray(forward.measurement.conditional_position_covariance_enu_m2),
                np.asarray(reverse.measurement.conditional_position_covariance_enu_m2),
                rtol=0,
                atol=1e-14,
            )

    def test_iteration_exhaustion_and_quality_failure_do_not_publish_a_position(self) -> None:
        rays = self.outlier_rays()
        for policy, reason in (
            (
                replace(
                    self.geometry.policy,
                    robust_loss_definition="huber",
                    maximum_solver_iterations=1,
                ),
                f.GeometryReason.SOLVER_NOT_CONVERGED,
            ),
            (
                replace(
                    self.geometry.policy, robust_loss_definition="huber", maximum_residual_m=0.1
                ),
                f.GeometryReason.EXCESSIVE_RESIDUAL,
            ),
        ):
            result = self.result(rays, replace(self.geometry, policy=policy))
            self.assertIn(reason, result.reasons)
            self.assertIsNone(result.measurement.position_enu_m)
            self.assertIsNone(result.measurement.conditional_position_covariance_enu_m2)

    def test_usage_penalties_inflate_conditional_covariance_and_keep_shared_references(
        self,
    ) -> None:
        clean = self.fixture.rays()
        valid = self.result(tuple(self.noisy(ray) for ray in clean))
        for usage, factor in ((m.CalibrationUsage.FROZEN, 4.0), (m.CalibrationUsage.STALE, 10.0)):
            rays = tuple(self.noisy(ray, usage=usage) for ray in clean)
            result = self.result(rays)
            self.fixture.assert_position(result.measurement, helpers.TARGET)
            np.testing.assert_allclose(
                np.asarray(result.measurement.conditional_position_covariance_enu_m2),
                factor * np.asarray(valid.measurement.conditional_position_covariance_enu_m2),
                rtol=1e-13,
                atol=1e-15,
            )
            self.assertEqual(
                tuple(weight.weight_scale for weight in result.ray_weights), (1 / factor,) * 3
            )
            self.assertEqual(
                result.shared_calibration_solution_refs,
                tuple(ray.event.calibration_solution_ref for ray in clean),
            )
            self.assertTrue(all(usage.value in item.reason_codes[0] for item in result.ray_weights))

    def test_usage_penalty_changes_relative_influence_not_station_vote_count(self) -> None:
        rays = self.outlier_rays()
        valid = self.result(rays)
        frozen = self.result(
            (
                *rays[:-1],
                replace(
                    rays[-1],
                    event=replace(rays[-1].event, calibration_usage=m.CalibrationUsage.FROZEN),
                ),
            )
        )
        self.assertLess(
            np.linalg.norm(np.asarray(frozen.measurement.position_enu_m) - helpers.TARGET),
            np.linalg.norm(np.asarray(valid.measurement.position_enu_m) - helpers.TARGET),
        )
        window = self.fixture.window(
            tuple(self.noisy(ray, usage=m.CalibrationUsage.FROZEN) for ray in self.fixture.rays())
        )
        candidates = self.fixture.voxels.propose(window, ())
        self.assertTrue(candidates.candidates)
        self.assertTrue(any(candidate.score == 3 for candidate in candidates.candidates))
        self.assertTrue(
            all(
                candidate.score == len(candidate.independent_station_support)
                for candidate in candidates.candidates
            )
        )

    def test_shared_pose_covariance_is_not_added_as_independent_ray_noise(self) -> None:
        rays = tuple(self.noisy(ray) for ray in self.fixture.rays())
        original = self.result(rays)
        changed = tuple(
            replace(
                ray,
                config=replace(
                    ray.config,
                    installation_uncertainty=replace(
                        ray.config.installation_uncertainty,
                        position_covariance_enu_m2=matrix3(1e6 * np.eye(3)),
                        orientation_covariance_enu_rad2=IDENTITY,
                    ),
                ),
                calibration_solution=replace(
                    ray.calibration_solution, correction_covariance_enu_rad2=IDENTITY
                ),
            )
            for ray in rays
        )
        result = self.result(changed)
        self.assertEqual(
            result.measurement.conditional_position_covariance_enu_m2,
            original.measurement.conditional_position_covariance_enu_m2,
        )
        self.assertEqual(
            result.shared_installation_state_refs, original.shared_installation_state_refs
        )
        self.assertEqual(
            result.shared_calibration_solution_refs, original.shared_calibration_solution_refs
        )

    def test_excluded_calibration_cannot_supply_an_independent_station(self) -> None:
        first, second, _ = self.fixture.rays()
        second = replace(
            second, event=replace(second.event, calibration_usage=m.CalibrationUsage.STALE)
        )
        for action, scale in (
            (f.CalibrationAction.REJECT, 1.0),
            (f.CalibrationAction.QUARANTINE, 1.0),
            (f.CalibrationAction.ACCEPT, 0.0),
        ):
            weights = CalibrationWeightingModule(
                rules=tuple(
                    replace(rule, action=action, geometry_weight_scale=scale)
                    if rule.usage is m.CalibrationUsage.STALE
                    else rule
                    for rule in self.fixture.weights.rules
                )
            )
            window = self.fixture.window((first, second))
            voxels = replace(self.fixture.voxels, weights=weights)
            candidates = voxels.propose(window, ())
            self.assertIs(candidates.status, f.CandidateBatchStatus.INSUFFICIENT_STATIONS)
            self.assertFalse(candidates.candidates)
            self.assertIn("calibration_policy_excluded_rays", candidates.reason_codes)
            association = replace(self.fixture.associator, weights=weights).associate(
                window, self.fixture.voxels.propose(window, ()), ()
            )
            self.assertFalse(association.selected_groups)
            result = self.result((first, second), replace(self.geometry, weights=weights))
            self.assertIn(f.GeometryReason.CALIBRATION_POLICY_EXCLUDED, result.reasons)
            self.assertIsNone(result.measurement.position_enu_m)

    def tube_window(self) -> f.SealedObservationWindow:
        return self.fixture.window(
            (
                self.noisy(
                    self.fixture.ray("a", (0.0, 0.5, 0.48), direction=(1.0, 0.0, 0.0)),
                    variance=0.0004,
                ),
                self.noisy(
                    self.fixture.ray("b", (0.5, 0.0, 0.52), direction=(0.0, 1.0, 0.0)),
                    variance=0.0004,
                ),
            )
        )

    def test_uncertainty_tubes_recover_support_when_central_rays_share_no_cell(self) -> None:
        window = self.tube_window()
        search = replace(
            self.fixture.search,
            min_enu_m=(0.4, 0.4, 0.4),
            max_enu_m=(0.6, 0.6, 0.6),
            step_enu_m=(0.01, 0.01, 0.01),
        )
        self.assertFalse(_ray_voxels(window.rays[0], search) & _ray_voxels(window.rays[1], search))
        voxels = replace(
            self.fixture.voxels, policy=replace(self.fixture.candidate_policy, search=search)
        )
        original = asdict(window)
        candidates = voxels.propose(window, ())
        self.assertTrue(candidates.candidates)
        association = self.fixture.associator.associate(window, candidates, ())
        self.assertEqual(len(association.selected_groups), 1)
        (result,) = self.geometry.refine(window, association, ()).results
        self.fixture.assert_position(result.measurement, (0.5, 0.5, 0.5))
        self.assertEqual(asdict(window), original)
        self.assertTrue(all(candidate.score == 2 for candidate in candidates.candidates))
        disabled = replace(voxels, policy=replace(voxels.policy, uncertainty_sigma=0))
        self.assertFalse(disabled.propose(window, ()).candidates)

    def test_tube_can_enter_the_volume_when_the_central_ray_never_does(self) -> None:
        ray = self.noisy(
            self.fixture.ray("a", (-1.0, -0.02, 0.5), direction=(1.0, 0.0, 0.0)), variance=0.0004
        )
        search = replace(
            self.fixture.search,
            min_enu_m=(0.0, 0.0, 0.0),
            max_enu_m=(1.0, 1.0, 1.0),
            step_enu_m=(0.1, 0.1, 0.1),
        )
        self.assertFalse(_ray_voxels(ray, search))
        cells, truncated = _uncertain_voxels(
            ray, search, self.fixture.candidate_policy, 1.0, aligned_origin=None
        )
        self.assertFalse(truncated)
        self.assertIn((5, 0, 5), cells)

    def test_angular_tube_expands_with_range_without_any_origin_noise(self) -> None:
        ray = self.noisy(
            self.fixture.ray("a", (-1.0, -0.02, 0.5), direction=(1.0, 0.0, 0.0)),
            variance=0,
            angular=((0.0004, 0.0), (0.0, 0.0004)),
        )
        search = replace(
            self.fixture.search,
            min_enu_m=(0.0, 0.0, 0.0),
            max_enu_m=(1.0, 1.0, 1.0),
            step_enu_m=(0.1, 0.1, 0.1),
        )
        self.assertFalse(_ray_voxels(ray, search))
        cells, truncated = _uncertain_voxels(
            ray, search, self.fixture.candidate_policy, 1.0, aligned_origin=None
        )
        self.assertFalse(truncated)
        self.assertIn((5, 0, 5), cells)
        clean = replace(
            ray,
            event=replace(
                ray.event,
                uncertainty=replace(
                    ray.event.uncertainty,
                    independent_angular_covariance_rad2=((0.0, 0.0), (0.0, 0.0)),
                ),
            ),
        )
        self.assertEqual(
            _uncertain_voxels(
                clean, search, self.fixture.candidate_policy, 1.0, aligned_origin=None
            ),
            (set(), False),
        )

    def test_uncertainty_expansion_budget_never_claims_complete_association(self) -> None:
        window = self.tube_window()
        voxels = replace(
            self.fixture.voxels,
            policy=replace(self.fixture.candidate_policy, maximum_uncertainty_voxels_per_ray=1),
        )
        candidates = voxels.propose(window, ())
        self.assertIn("uncertainty_expansion_limit_reached", candidates.reason_codes)
        association = self.fixture.associator.associate(window, candidates, ())
        self.assertIs(association.status, f.AssociationBatchStatus.ASSOCIATION_AMBIGUOUS)
        self.assertFalse(association.selected_groups)
        self.assertIn(
            f.AssociationConflictReason.CANDIDATE_SEARCH_TRUNCATED, association.conflicts[0].reasons
        )

    def test_tiny_and_broad_tubes_stop_before_allocating_a_fine_grid(self) -> None:
        search = replace(
            self.fixture.search,
            min_enu_m=(0.0, 0.0, 0.0),
            max_enu_m=(1.0, 1.0, 1.0),
            step_enu_m=(1e-30, 1e-30, 1e-30),
        )
        policy = replace(self.fixture.candidate_policy, maximum_uncertainty_voxels_per_ray=3)
        for variance in (1e-6, 1.0):
            ray = self.noisy(
                self.fixture.ray("a", (-1.0, 0.5, 0.5), direction=(1.0, 0.0, 0.0)),
                variance=variance,
            )
            cells, truncated = _uncertain_voxels(ray, search, policy, 1.0, aligned_origin=None)
            self.assertTrue(truncated)
            self.assertLessEqual(len(cells), 3)

    def test_positive_covariance_scale_is_not_replaced_with_a_noise_floor(self) -> None:
        # Orthogonal rays give the analytic covariance diag(s2, s2, s2/2),
        # avoiding a self-referential numerical baseline for this scale test.
        for variance in (1e-24, 1e24):
            rays = (
                self.noisy(
                    self.fixture.ray("a", (0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0)),
                    variance=variance,
                ),
                self.noisy(
                    self.fixture.ray("b", (1.0, -1.0, 0.0), direction=(0.0, 1.0, 0.0)),
                    variance=variance,
                ),
            )
            result = self.result(rays)
            self.fixture.assert_position(result.measurement, (1.0, 0.0, 0.0))
            np.testing.assert_allclose(
                np.asarray(result.measurement.conditional_position_covariance_enu_m2),
                np.diag((variance, variance, variance / 2)),
                rtol=1e-13,
                atol=0,
            )

    def test_invalid_weights_and_solver_parameters_fail_explicitly(self) -> None:
        for rules in (
            (),
            (*self.fixture.weights.rules, self.fixture.weights.rules[0]),
            tuple(replace(rule, geometry_weight_scale=-1) for rule in self.fixture.weights.rules),
        ):
            with self.assertRaisesRegex(ValueError, "weighting rules"):
                CalibrationWeightingModule(rules=rules)
        for policy in (
            replace(self.geometry.policy, maximum_solver_iterations=0),
            replace(self.geometry.policy, convergence_tolerance_m=0),
            replace(self.geometry.policy, robust_loss_scale_sigma=float("nan")),
        ):
            with self.assertRaisesRegex(ValueError, "geometry policy"):
                replace(self.geometry, policy=policy)
        for candidate_policy in (
            replace(self.fixture.candidate_policy, uncertainty_sigma=-1),
            replace(self.fixture.candidate_policy, maximum_uncertainty_voxels_per_ray=0),
        ):
            with self.assertRaisesRegex(ValueError, "candidate policy"):
                replace(self.fixture.voxels, policy=candidate_policy)

    def test_monte_carlo_angular_errors_match_the_local_covariance(self) -> None:
        rng = np.random.default_rng(1708)
        angular: m.Matrix2x2 = ((4e-8, 1e-8), (1e-8, 9e-8))
        rays = tuple(self.noisy(ray, variance=0, angular=angular) for ray in self.fixture.rays())
        expected = np.asarray(self.result(rays).measurement.conditional_position_covariance_enu_m2)
        angular_root = np.linalg.cholesky(angular)
        errors = []
        for _ in range(600):
            sampled = []
            for ray in rays:
                uncertainty = ray.event.uncertainty
                basis = np.asarray((uncertainty.tangent_u_enu_unit, uncertainty.tangent_v_enu_unit))
                direction = np.asarray(ray.event.ray_direction_enu_unit)
                angle = angular_root @ rng.normal(size=2)
                tangent = angle @ basis
                rotation_vector = np.cross(direction, tangent)
                theta = float(np.linalg.norm(rotation_vector))
                x, y, z = rotation_vector / theta
                skew = np.asarray(((0.0, -z, y), (z, 0.0, -x), (-y, x, 0.0)))
                rotation = np.eye(3) + np.sin(theta) * skew + (1 - np.cos(theta)) * (skew @ skew)
                rotated_basis = basis @ rotation.T
                sampled.append(
                    replace(
                        ray,
                        event=replace(
                            ray.event,
                            ray_direction_enu_unit=cast(m.Vec3, tuple(rotation @ direction)),
                            uncertainty=replace(
                                uncertainty,
                                tangent_u_enu_unit=cast(m.Vec3, tuple(rotated_basis[0])),
                                tangent_v_enu_unit=cast(m.Vec3, tuple(rotated_basis[1])),
                            ),
                        ),
                    )
                )
            result = self.result(tuple(sampled))
            self.assertIs(result.measurement.status, m.FusionStatus.LOCATED)
            errors.append(np.asarray(result.measurement.position_enu_m) - helpers.TARGET)
        whitened = np.linalg.solve(np.linalg.cholesky(expected), np.asarray(errors).T)
        np.testing.assert_allclose(np.cov(whitened), np.eye(3), rtol=0, atol=0.2)
        self.assertLess(float(np.max(np.abs(whitened.mean(axis=1)))), 0.15)

    def test_monte_carlo_origin_errors_match_conditional_position_covariance(self) -> None:
        rng = np.random.default_rng(1707)
        rays = tuple(self.noisy(ray, variance=1e-6) for ray in self.fixture.rays())
        expected = np.asarray(self.result(rays).measurement.conditional_position_covariance_enu_m2)
        estimates = []
        for _ in range(400):
            sampled = tuple(
                replace(
                    ray,
                    event=replace(
                        ray.event,
                        ray_origin_enu_m=cast(
                            m.Vec3,
                            tuple(np.asarray(ray.event.ray_origin_enu_m) + rng.normal(0, 0.001, 3)),
                        ),
                    ),
                )
                for ray in rays
            )
            result = self.result(sampled)
            self.assertIs(result.measurement.status, m.FusionStatus.LOCATED)
            estimates.append(result.measurement.position_enu_m)
        errors = np.asarray(estimates) - helpers.TARGET
        root = np.linalg.cholesky(expected)
        whitened = np.linalg.solve(root, errors.T)
        np.testing.assert_allclose(np.cov(whitened), np.eye(3), rtol=0, atol=0.2)
        self.assertLess(float(np.max(np.abs(whitened.mean(axis=1)))), 0.15)


if __name__ == "__main__":
    unittest.main()
