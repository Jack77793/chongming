"""Multi-target association from current ray evidence; priors only resolve gates."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from itertools import combinations, product

import numpy as np
from numpy.typing import NDArray

from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.modules.base import ModuleShell
from chongming.modules.fusion_estimation import fit_rays
from chongming.modules.fusion_geometry import (
    _STATION_SCORE,
    _event_id,
    _exposure_span,
    _line_fit,
    _reference_time,
)
from chongming.modules.fusion_matching import ExclusiveChoice, exclusive_components
from chongming.modules.fusion_motion import (
    alignment_reference,
    midpoint_span,
    origins_at,
    validate_policy,
    validate_predictions,
)
from chongming.ports import fusion as ports


@dataclass(frozen=True, kw_only=True)
class CrossStationAssociationModule(ModuleShell):
    """Select disjoint groups by station-pair support, prior matches, then error.

    Enumerate station subsets and alternative rays, not voxel centers or truth IDs.
    Only groups common to every near-optimal complete assignment are published.
    """

    business_implemented = True
    policy: f.AssociationPolicy
    motion_policy: f.GeometryPolicy
    weights: ports.GeometryWeighting

    def __post_init__(self) -> None:
        validate_policy(self.motion_policy)
        if (
            self.policy.minimum_independent_stations < 2
            or self.policy.temporal_gate_ns < 0
            or any(
                not math.isfinite(value) or value < 0
                for value in (self.policy.spatial_gate_m, self.policy.ambiguity_score_margin)
            )
            or not math.isfinite(self.policy.prediction_spatial_gate_m)
            or self.policy.prediction_spatial_gate_m <= 0
            or self.policy.maximum_hypotheses <= 0
            or self.policy.maximum_search_nodes <= 0
        ):
            raise ValueError("invalid association policy")

    def associate(
        self,
        window: f.SealedObservationWindow,
        candidates: f.CandidateBatch,
        predictions: tuple[f.TrackPrediction, ...],
    ) -> f.AssociationBatch:
        if candidates.window != window.key:
            raise ValueError("candidate and observation windows differ")
        validate_predictions(window, candidates.search.world_frame_id, predictions)
        rays = {_event_id(ray): ray for ray in window.rays}
        conflicts: list[f.AssociationConflict] = []
        groups: list[f.AssociatedRayGroup] = []
        choices: list[ExclusiveChoice] = []
        choice_groups: list[int] = []
        choice_priors: list[m.TrackId | None] = []
        examined: set[tuple[tuple[m.EventId, ...], m.TrackId | None]] = set()
        candidate_truncated = any(
            reason in candidates.reason_codes
            for reason in ("candidate_limit_reached", "uncertainty_expansion_limit_reached")
        )
        truncated = candidate_truncated
        if truncated:
            conflicts.append(
                self._conflict(
                    window,
                    (),
                    tuple(sorted(rays)),
                    (f.AssociationConflictReason.CANDIDATE_SEARCH_TRUNCATED,),
                )
            )
        for candidate in candidates.candidates if not truncated else ():
            if candidate.window != window.key:
                raise ValueError("spatial candidate belongs to another window")
            support = sorted(
                candidate.independent_station_support, key=lambda item: item.station_id
            )
            if len({item.station_id for item in support}) != len(support) or any(
                not item.alternative_ray_event_ids
                or any(
                    event_id not in rays or event_id[0] != item.station_id
                    for event_id in item.alternative_ray_event_ids
                )
                for item in support
            ):
                raise ValueError("invalid candidate station/ray support")
            if len(support) < self.policy.minimum_independent_stations:
                conflicts.append(
                    self._conflict(
                        window,
                        (),
                        tuple(
                            event_id
                            for item in support
                            for event_id in item.alternative_ray_event_ids
                        ),
                        (f.AssociationConflictReason.INSUFFICIENT_INDEPENDENT_STATIONS,),
                    )
                )
            for count in range(len(support), self.policy.minimum_independent_stations - 1, -1):
                for stations in combinations(support, count):
                    for ids in product(
                        *(sorted(set(item.alternative_ray_event_ids)) for item in stations)
                    ):
                        chosen = tuple(rays[event_id] for event_id in ids)
                        if any(self.weights.weight(ray).weight_scale <= 0 for ray in chosen):
                            continue
                        spread = midpoint_span(chosen)
                        modes: list[f.TrackPrediction | None] = []
                        if candidate.origin is f.CandidateOrigin.UNGUIDED_SEARCH:
                            modes.append(None)
                        if (
                            spread
                            and self.motion_policy.motion_alignment_model
                            is f.MotionAlignmentModel.TRACK_PREDICTION
                        ):
                            modes.extend(
                                item
                                for item in sorted(predictions, key=lambda item: item.track_id)
                                if item.conditional_state_covariance_enu is not None
                                and window.key.start_ns
                                <= item.reference_time_ns
                                < window.key.end_ns
                                and item.valid_until_ns > alignment_reference(chosen)
                                and (
                                    candidate.prior_track_id is None
                                    or candidate.prior_track_id == item.track_id
                                )
                            )
                        for prior in modes:
                            key = (ids, None if prior is None else prior.track_id)
                            if key in examined:
                                continue
                            if (
                                len(examined) >= self.policy.maximum_hypotheses
                                or len(choices) >= self.policy.maximum_hypotheses
                            ):
                                truncated = True
                                break
                            examined.add(key)
                            reference = (
                                _reference_time(chosen)
                                if prior is None
                                else alignment_reference(chosen)
                            )
                            group = f.AssociatedRayGroup(
                                hypothesis_id="rays/"
                                + hashlib.sha256(repr(key).encode()).hexdigest(),
                                candidate_id=candidate.candidate_id,
                                window=window.key,
                                reference_time_ns=reference,
                                support_exposure_span_ns=_exposure_span(chosen),
                                supporting_ray_event_ids=ids,
                                supporting_station_ids=tuple(event_id[0] for event_id in ids),
                                prior_track_id=None if prior is None else prior.track_id,
                                motion_alignment_model=f.MotionAlignmentModel.NARROW_WINDOW_WITHOUT_PRIOR
                                if prior is None
                                else f.MotionAlignmentModel.TRACK_PREDICTION,
                                score=float(count),
                                score_definition=_STATION_SCORE,
                            )
                            failure: f.AssociationConflictReason | None
                            if (
                                prior is None
                                and spread > self.motion_policy.maximum_unaligned_span_ns
                            ):
                                failure, fitted, residual = (
                                    (
                                        f.AssociationConflictReason.TEMPORAL_GATE_FAILED
                                        if spread > self.policy.temporal_gate_ns
                                        else f.AssociationConflictReason.MOTION_ALIGNMENT_UNAVAILABLE
                                    ),
                                    None,
                                    0.0,
                                )
                            else:
                                failure, fitted, residual = self._gate(
                                    chosen,
                                    candidates.search,
                                    reference,
                                    None if prior is None else prior.velocity_enu_mps,
                                )
                            if failure is not None:
                                conflicts.append(
                                    self._conflict(window, (group.hypothesis_id,), ids, (failure,))
                                )
                                continue
                            assert fitted is not None
                            hints = (
                                (prior,)
                                if prior is not None
                                else tuple(sorted(predictions, key=lambda item: item.track_id))
                                if not spread
                                else ()
                            )
                            group_index = len(groups)
                            initial_choices = len(choices)
                            groups.append(group)
                            if prior is None:
                                choices.append(
                                    ExclusiveChoice(
                                        frozenset(ids), (count * (count - 1) // 2, 0), residual
                                    )
                                )
                                choice_groups.append(group_index)
                                choice_priors.append(None)
                            for hint in hints:
                                assert hint is not None
                                dt = (reference - hint.reference_time_ns) / 1e9
                                position = np.asarray(hint.position_enu_m) + dt * np.asarray(
                                    hint.velocity_enu_mps
                                )
                                distance = float(np.linalg.norm(fitted - position))
                                if (
                                    distance <= self.policy.prediction_spatial_gate_m
                                    and hint.valid_until_ns > reference
                                    and window.key.start_ns
                                    <= hint.reference_time_ns
                                    < window.key.end_ns
                                ):
                                    if len(choices) >= self.policy.maximum_hypotheses:
                                        truncated = True
                                        break
                                    choices.append(
                                        ExclusiveChoice(
                                            frozenset((*ids, ("track", hint.track_id))),
                                            (count * (count - 1) // 2, 1),
                                            residual
                                            + (distance / self.policy.prediction_spatial_gate_m)
                                            ** 2,
                                        )
                                    )
                                    choice_groups.append(group_index)
                                    choice_priors.append(hint.track_id)
                            if prior is not None and len(choices) == initial_choices:
                                conflicts.append(
                                    self._conflict(
                                        window,
                                        (group.hypothesis_id,),
                                        ids,
                                        (f.AssociationConflictReason.PREDICTION_GATE_FAILED,),
                                    )
                                )
                            if truncated:
                                break
                        if truncated:
                            break
                    if truncated:
                        break
                if truncated:
                    break
            if truncated:
                break
        selected: list[f.AssociatedRayGroup] = []
        ambiguous = truncated
        if truncated and not candidate_truncated:
            conflicts.append(
                self._conflict(
                    window,
                    tuple(group.hypothesis_id for group in groups),
                    tuple(sorted(rays)),
                    (f.AssociationConflictReason.ASSOCIATION_SEARCH_TRUNCATED,),
                )
            )
        if not truncated:
            for component in exclusive_components(
                tuple(choices),
                margin=self.policy.ambiguity_score_margin,
                maximum_nodes=self.policy.maximum_search_nodes,
            ):
                indices = {choice_groups[index] for index in component.indices}
                if component.truncated:
                    common: set[int] = set()
                else:
                    common = set.intersection(
                        *(
                            {choice_groups[index] for index in solution}
                            for solution in component.alternatives
                        )
                    )
                for index in sorted(common):
                    priors = {
                        choice_priors[choice]
                        for solution in component.alternatives
                        for choice in solution
                        if choice_groups[choice] == index
                    }
                    prior_id = next(iter(priors)) if len(priors) == 1 else None
                    selected.append(replace(groups[index], prior_track_id=prior_id))
                possible = (
                    set()
                    if component.truncated
                    else set.union(
                        *(
                            {choice_groups[index] for index in solution}
                            for solution in component.alternatives
                        )
                    )
                )
                if component.truncated or possible != common:
                    ambiguous = True
                    ids = tuple(
                        sorted(
                            {
                                event_id
                                for index in indices
                                for event_id in groups[index].supporting_ray_event_ids
                            }
                        )
                    )
                    reasons = [
                        f.AssociationConflictReason.ASSOCIATION_SEARCH_TRUNCATED
                        if component.truncated
                        else f.AssociationConflictReason.COMPETING_TARGET_HYPOTHESES
                    ]
                    if any(
                        len({event_id for event_id in ids if event_id[0] == station}) > 1
                        for station in {event_id[0] for event_id in ids}
                    ):
                        reasons.append(
                            f.AssociationConflictReason.MULTIPLE_SUPPORT_RAYS_FROM_ONE_STATION
                        )
                    conflicts.append(
                        self._conflict(
                            window,
                            tuple(groups[index].hypothesis_id for index in sorted(indices)),
                            ids,
                            tuple(reasons),
                        )
                    )
        assigned = {event_id for group in selected for event_id in group.supporting_ray_event_ids}
        if ambiguous:
            status = (
                f.AssociationBatchStatus.PARTIALLY_ASSOCIATED
                if selected
                else f.AssociationBatchStatus.ASSOCIATION_AMBIGUOUS
            )
        elif selected:
            status = f.AssociationBatchStatus.ASSOCIATIONS_SELECTED
        elif candidates.status is f.CandidateBatchStatus.INSUFFICIENT_STATIONS or (
            conflicts
            and all(
                f.AssociationConflictReason.INSUFFICIENT_INDEPENDENT_STATIONS in conflict.reasons
                for conflict in conflicts
            )
        ):
            status = f.AssociationBatchStatus.INSUFFICIENT_STATIONS
        elif candidates.status is f.CandidateBatchStatus.NO_CANDIDATE_IN_VOLUME:
            status = f.AssociationBatchStatus.NO_CANDIDATE_IN_VOLUME
        else:
            status = f.AssociationBatchStatus.NO_VALID_ASSOCIATION
        return f.AssociationBatch(
            window=window.key,
            selected_groups=tuple(
                sorted(selected, key=lambda group: group.supporting_ray_event_ids)
            ),
            conflicts=tuple(conflicts),
            unassigned_ray_event_ids=tuple(sorted(set(rays) - assigned)),
            status=status,
        )

    def _gate(
        self,
        rays: tuple[f.AdmittedRay, ...],
        search: m.VoxelSearchSpec,
        reference_ns: int,
        velocity: m.Vec3 | None,
    ) -> tuple[f.AssociationConflictReason | None, NDArray[np.float64] | None, float]:
        if midpoint_span(rays) > self.policy.temporal_gate_ns:
            return f.AssociationConflictReason.TEMPORAL_GATE_FAILED, None, 0.0
        try:
            fitted, residuals, depths, rank, singular = _line_fit(
                rays, reference_ns=reference_ns, velocity=velocity
            )
        except np.linalg.LinAlgError:
            return f.AssociationConflictReason.SPATIAL_GATE_FAILED, None, 0.0
        if rank < 3:
            return f.AssociationConflictReason.DEGENERATE_GEOMETRY, None, 0.0
        if any(
            np.any(ray.event.uncertainty.independent_angular_covariance_rad2)
            or np.any(ray.event.uncertainty.independent_origin_covariance_m2)
            for ray in rays
        ):
            try:
                fit = fit_rays(
                    rays,
                    self.motion_policy,
                    tuple(self.weights.weight(ray).weight_scale for ray in rays),
                    fitted,
                    reference_ns=reference_ns,
                    velocity=velocity,
                )
            except np.linalg.LinAlgError:
                return f.AssociationConflictReason.SOLVER_FAILURE, None, 0.0
            if not fit.converged:
                return f.AssociationConflictReason.SOLVER_NOT_CONVERGED, None, 0.0
            if not fit.exact_consistent:
                return f.AssociationConflictReason.INCONSISTENT_EXACT_CONSTRAINTS, None, 0.0
            fitted = fit.position
            origins = (
                np.asarray([ray.event.ray_origin_enu_m for ray in rays])
                if velocity is None
                else origins_at(rays, reference_ns, velocity)
            )
            directions = np.asarray([ray.event.ray_direction_enu_unit for ray in rays])
            directions = directions / np.linalg.norm(directions, axis=1)[:, None]
            vector = fitted - origins
            depths = np.einsum("ni,ni->n", directions, vector)
            residuals = np.linalg.norm(vector - depths[:, None] * directions, axis=1)
        if float(residuals.max()) > self.policy.spatial_gate_m:
            return f.AssociationConflictReason.SPATIAL_GATE_FAILED, None, 0.0
        scale = max(
            1.0,
            *(abs(value) for ray in rays for value in ray.event.ray_origin_enu_m),
            *(abs(value) for value in fitted),
        )
        roundoff = 32 * np.finfo(np.float64).eps * scale * singular[0] / singular[-1]
        outside = (
            np.any(fitted < np.asarray(search.min_enu_m) - roundoff)
            or np.any(fitted > np.asarray(search.max_enu_m) + roundoff)
            or np.any(depths < -roundoff)
        )
        if search.forward_range_m is not None:
            near, far = search.forward_range_m
            outside |= np.any(depths < near - roundoff) or np.any(depths > far + roundoff)
        if outside:
            return f.AssociationConflictReason.SPATIAL_GATE_FAILED, None, 0.0
        cost = (
            float(np.mean((residuals / self.policy.spatial_gate_m) ** 2))
            if self.policy.spatial_gate_m
            else 0.0
        )
        return None, fitted, cost

    @staticmethod
    def _conflict(
        window: f.SealedObservationWindow,
        hypotheses: tuple[str, ...],
        ids: tuple[m.EventId, ...],
        reasons: tuple[f.AssociationConflictReason, ...],
    ) -> f.AssociationConflict:
        return f.AssociationConflict(
            window=window.key,
            competing_hypothesis_ids=hypotheses,
            ray_event_ids=ids,
            reasons=reasons,
            resolved=False,
            selected_hypothesis_id=None,
        )
