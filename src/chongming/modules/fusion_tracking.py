"""Multi-target constant-velocity Kalman tracking on consumed event time."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.modules.base import ModuleShell
from chongming.modules.fusion_matching import ExclusiveChoice, exclusive_components
from chongming.uncertainty import covariance_factor

Array = NDArray[np.float64]


def _matrix6(matrix: Array) -> m.Matrix6x6:
    return cast(
        m.Matrix6x6,
        tuple(tuple(float(matrix[row, column]) for column in range(6)) for row in range(6)),
    )


def _vec3(vector: Array) -> m.Vec3:
    return float(vector[0]), float(vector[1]), float(vector[2])


@dataclass
class _Track:
    track_id: m.TrackId
    last_measurement: m.FusionMeasurement
    seed_position: Array
    seed_covariance: Array
    count: int = 1
    lifecycle: m.TrackLifecycle = m.TrackLifecycle.TENTATIVE
    state: Array | None = None
    covariance: Array | None = None


@dataclass(frozen=True)
class _Observation:
    measurement: m.FusionMeasurement
    position: Array
    covariance: Array
    model: Array
    ref: m.MeasurementRef


class CenterTrackingModule(ModuleShell):
    """One-to-one assignments; two exposure times initialize velocity without a prior.

    A lone seed uses only an explicit spatial gate, never a guessed velocity or
    covariance. Initialized tracks use their conditional Mahalanobis gates. Tied
    assignments do not update filters or start duplicate tentative tracks.
    """

    business_implemented = True

    def __init__(
        self,
        *,
        center_session_id: m.SessionId,
        clock_domain_id: str,
        world_frame_id: m.WorldFrameId,
        policy: f.TrackingPolicy,
    ) -> None:
        if (
            policy.confirmation_measurement_count < 2
            or policy.confirmation_max_gap_ns <= 0
            or policy.coast_after_ns < 0
            or policy.delete_after_ns <= policy.coast_after_ns
            or not math.isfinite(policy.association_gate_mahalanobis_squared)
            or policy.association_gate_mahalanobis_squared < 0
            or not math.isfinite(policy.initialization_spatial_gate_m)
            or policy.initialization_spatial_gate_m <= 0
            or not math.isfinite(policy.association_ambiguity_margin)
            or policy.association_ambiguity_margin < 0
            or policy.maximum_assignment_search_nodes <= 0
        ):
            raise ValueError("invalid tracking policy")
        self.center_session_id, self.clock_domain_id, self.world_frame_id, self.policy = (
            center_session_id,
            clock_domain_id,
            world_frame_id,
            policy,
        )
        factor = covariance_factor(
            policy.process_acceleration_covariance_enu_m2ps4,
            3,
            "process acceleration covariance (m2/s4)",
        )
        self._acceleration = factor @ factor.T
        self._tracks: dict[m.TrackId, _Track] = {}
        self._next_id = 1
        self._horizon_ns: int | None = None
        self._seen: set[m.MeasurementRef] = set()

    def _check_time(self, clock: str, time_ns: int) -> None:
        if clock != self.clock_domain_id:
            raise ValueError("tracking clock domain mismatch")
        if self._horizon_ns is not None and time_ns < self._horizon_ns:
            raise ValueError("tracking event time must not move backwards")

    def _predict(self, track: _Track, time_ns: int) -> tuple[Array, Array]:
        assert track.state is not None and track.covariance is not None
        dt = (time_ns - track.last_measurement.reference_time_ns) / 1e9
        transition = np.eye(6)
        transition[:3, 3:] = dt * np.eye(3)
        acceleration = np.vstack((0.5 * dt * dt * np.eye(3), dt * np.eye(3)))
        covariance = (
            transition @ track.covariance @ transition.T
            + acceleration @ self._acceleration @ acceleration.T
        )
        return transition @ track.state, (covariance + covariance.T) / 2

    def predict(
        self, clock_domain_id: str, reference_time_ns: int
    ) -> tuple[f.TrackPrediction, ...]:
        self._check_time(clock_domain_id, reference_time_ns)
        predictions = []
        for track in self._tracks.values():
            if track.state is not None:
                state, covariance = self._predict(track, reference_time_ns)
                predictions.append(
                    f.TrackPrediction(
                        center_session_id=self.center_session_id,
                        track_id=track.track_id,
                        clock_domain_id=self.clock_domain_id,
                        world_frame_id=self.world_frame_id,
                        reference_time_ns=reference_time_ns,
                        valid_until_ns=track.last_measurement.reference_time_ns
                        + min(
                            self.policy.delete_after_ns,
                            self.policy.confirmation_max_gap_ns + 1
                            if track.lifecycle is m.TrackLifecycle.TENTATIVE
                            else self.policy.delete_after_ns,
                        ),
                        position_enu_m=_vec3(state[:3]),
                        velocity_enu_mps=_vec3(state[3:]),
                        conditional_state_covariance_enu=_matrix6(covariance),
                    )
                )
        return tuple(predictions)

    def _event(
        self,
        track: _Track,
        time_ns: int,
        reasons: tuple[str, ...],
        sources: tuple[m.MeasurementRef, ...] = (),
    ) -> m.TrackEvent:
        if track.state is None:
            position = (
                track.last_measurement.position_enu_m
                if time_ns == track.last_measurement.reference_time_ns
                else None
            )
            velocity, covariance = None, None
        else:
            state, predicted_covariance = self._predict(track, time_ns)
            position, velocity, covariance = (
                _vec3(state[:3]),
                _vec3(state[3:]),
                _matrix6(predicted_covariance),
            )
        return m.TrackEvent(
            center_session_id=self.center_session_id,
            track_id=track.track_id,
            clock_domain_id=self.clock_domain_id,
            world_frame_id=self.world_frame_id,
            event_time_ns=time_ns,
            lifecycle=track.lifecycle,
            position_enu_m=position,
            velocity_enu_mps=velocity,
            conditional_state_covariance=covariance,
            source_measurement_refs=sources,
            reason_codes=reasons,
        )

    def _expired(self, track: _Track, time_ns: int) -> bool:
        gap = time_ns - track.last_measurement.reference_time_ns
        return gap >= self.policy.delete_after_ns or (
            track.lifecycle is m.TrackLifecycle.TENTATIVE
            and gap > self.policy.confirmation_max_gap_ns
        )

    def _retire(self, time_ns: int) -> list[m.TrackEvent]:
        events = []
        for track in tuple(self._tracks.values()):
            if self._expired(track, time_ns):
                reason = (
                    "confirmation_expired"
                    if track.lifecycle is m.TrackLifecycle.TENTATIVE
                    and time_ns - track.last_measurement.reference_time_ns
                    > self.policy.confirmation_max_gap_ns
                    else "measurement_timeout"
                )
                track.lifecycle = m.TrackLifecycle.DELETED
                events.append(self._event(track, time_ns, (reason,)))
                del self._tracks[track.track_id]
        return events

    def _missing(
        self, track: _Track, time_ns: int, *, emit: bool, reason: str
    ) -> list[m.TrackEvent]:
        changed = (
            track.lifecycle is m.TrackLifecycle.CONFIRMED
            and time_ns - track.last_measurement.reference_time_ns >= self.policy.coast_after_ns
        )
        if changed:
            track.lifecycle = m.TrackLifecycle.COASTING
        return [self._event(track, time_ns, (reason,))] if changed or emit else []

    def _innovation(
        self, track: _Track, observation: _Observation
    ) -> tuple[Array, Array, Array, Array, float]:
        state, prior = self._predict(track, observation.measurement.reference_time_ns)
        innovation = observation.position - observation.model @ state
        residual_covariance = (
            observation.model @ prior @ observation.model.T + observation.covariance
        )
        values, vectors = np.linalg.eigh((residual_covariance + residual_covariance.T) / 2)
        tolerance = 96 * np.finfo(np.float64).eps * float(np.max(np.abs(values)))
        if float(values.min()) < -tolerance:
            raise np.linalg.LinAlgError("innovation covariance is not positive semidefinite")
        positive = values > tolerance
        components = vectors.T @ innovation
        roundoff = (
            32
            * np.finfo(np.float64).eps
            * max(
                1.0, float(np.max(np.abs(observation.position))), float(np.max(np.abs(state[:3])))
            )
        )
        impossible = bool(np.any(np.abs(components[~positive]) > roundoff))
        distance = (
            math.inf if impossible else float(np.sum(components[positive] ** 2 / values[positive]))
        )
        inverse = (vectors[:, positive] / values[positive]) @ vectors[:, positive].T
        return state, prior, innovation, inverse, distance

    def _cost(self, track: _Track, observation: _Observation) -> float | None:
        time_ns = observation.measurement.reference_time_ns
        motion = observation.measurement.motion_alignment
        if motion is not None and (motion.prior_track_id != track.track_id or track.state is None):
            return None
        if self._expired(track, time_ns) or time_ns <= track.last_measurement.reference_time_ns:
            return None
        if track.state is None:
            cost = (
                float(np.sum((observation.position - track.seed_position) ** 2))
                / self.policy.initialization_spatial_gate_m**2
            )
            return cost if cost <= 1 else None
        *_, distance = self._innovation(track, observation)
        gate = self.policy.association_gate_mahalanobis_squared
        return (distance / gate if gate else 0.0) if distance <= gate else None

    def _assimilate(self, track: _Track, observation: _Observation) -> m.TrackEvent:
        measurement, position, covariance = (
            observation.measurement,
            observation.position,
            observation.covariance,
        )
        dt = (measurement.reference_time_ns - track.last_measurement.reference_time_ns) / 1e9
        if track.state is None:
            track.state = np.concatenate((position, (position - track.seed_position) / dt))
            track.covariance = np.block(
                [
                    [covariance, covariance / dt],
                    [covariance / dt, (track.seed_covariance + covariance) / dt**2],
                ]
            )
        else:
            state, prior, innovation, inverse, _ = self._innovation(track, observation)
            gain = prior @ observation.model.T @ inverse
            track.state = state + gain @ innovation
            correction = np.eye(6) - gain @ observation.model
            # Multiply square roots before forming the Joseph covariance. Exact
            # aligned observations can annihilate a singular prior; C P C.T
            # otherwise loses the remaining digits through cancellation.
            prior_root = covariance_factor(_matrix6(prior), 6, "tracking prior covariance")
            noise_root = covariance_factor(
                cast(
                    m.Matrix3x3, tuple(tuple(float(value) for value in row) for row in covariance)
                ),
                3,
                "independent measurement covariance",
            )
            roots = np.hstack((correction @ prior_root, gain @ noise_root))
            track.covariance = roots @ roots.T
        recovered = track.lifecycle is m.TrackLifecycle.COASTING
        track.count += 1
        track.last_measurement = measurement
        if track.count >= self.policy.confirmation_measurement_count:
            track.lifecycle = m.TrackLifecycle.CONFIRMED
        reasons = (
            ("measurement_update", "track_recovered") if recovered else ("measurement_update",)
        )
        return self._event(track, measurement.reference_time_ns, reasons, (observation.ref,))

    def update(
        self,
        measurements: tuple[m.FusionMeasurement, ...],
        clock_domain_id: str,
        event_time_ns: int,
    ) -> tuple[m.TrackEvent, ...]:
        """Consume one batch; individual reference times precede its event-time horizon."""
        self._check_time(clock_domain_id, event_time_ns)
        observations = []
        refs: set[m.MeasurementRef] = set()
        ray_ids: set[m.EventId] = set()
        for measurement in sorted(
            measurements, key=lambda item: (item.reference_time_ns, item.measurement_id)
        ):
            if measurement.status is not m.FusionStatus.LOCATED:
                continue
            if (
                measurement.center_session_id,
                measurement.clock_domain_id,
                measurement.world_frame_id,
            ) != (self.center_session_id, self.clock_domain_id, self.world_frame_id):
                raise ValueError("measurement session, frame or clock mismatch")
            ref = m.MeasurementRef(
                center_session_id=measurement.center_session_id,
                measurement_id=measurement.measurement_id,
            )
            if ref in self._seen or ref in refs:
                raise ValueError("measurement already consumed")
            if measurement.reference_time_ns > event_time_ns or (
                self._horizon_ns is not None and measurement.reference_time_ns <= self._horizon_ns
            ):
                raise ValueError(
                    "measurement requires a later exposure within the consumed batch horizon"
                )
            if measurement.position_enu_m is None:
                raise ValueError("located measurement requires a finite position")
            position = np.asarray(measurement.position_enu_m, dtype=np.float64)
            if position.shape != (3,) or not np.isfinite(position).all():
                raise ValueError("located measurement requires a finite ENU 3-vector")
            if measurement.conditional_position_covariance_enu_m2 is None:
                raise ValueError("tracking requires an explicit conditional measurement covariance")
            if ray_ids.intersection(measurement.supporting_ray_event_ids):
                raise ValueError("ray evidence assigned to multiple measurements")
            factor = covariance_factor(
                measurement.conditional_position_covariance_enu_m2, 3, "measurement covariance (m2)"
            )
            model = np.hstack((np.eye(3), np.zeros((3, 3))))
            motion = measurement.motion_alignment
            if motion is not None:
                projection = np.asarray(motion.velocity_projection_s, dtype=np.float64)
                velocity = np.asarray(motion.velocity_enu_mps, dtype=np.float64)
                if (
                    projection.shape != (3, 3)
                    or velocity.shape != (3,)
                    or not np.isfinite(projection).all()
                    or not np.isfinite(velocity).all()
                ):
                    raise ValueError("invalid motion alignment observation")
                # Recover the raw linear observation; the velocity prior is shared
                # with this filter, not fresh independent measurement noise.
                position = position + projection @ velocity
                model[:, 3:] = projection
                factor = covariance_factor(
                    motion.independent_position_covariance_enu_m2,
                    3,
                    "independent aligned measurement covariance (m2)",
                )
            observations.append(_Observation(measurement, position, factor @ factor.T, model, ref))
            refs.add(ref)
            ray_ids.update(measurement.supporting_ray_event_ids)
        tracks = tuple(self._tracks.values())
        choices, pairs = [], []
        for track_index, track in enumerate(tracks):
            for observation_index, observation in enumerate(observations):
                cost = self._cost(track, observation)
                if cost is not None:
                    choices.append(
                        ExclusiveChoice(
                            frozenset((("track", track_index), ("measurement", observation_index))),
                            (1, 0),
                            cost,
                        )
                    )
                    pairs.append((track_index, observation_index))
        selected: set[int] = set()
        blocked: set[int] = set()
        ambiguous_tracks: dict[int, str] = {}
        for component in exclusive_components(
            tuple(choices),
            margin=self.policy.association_ambiguity_margin,
            maximum_nodes=self.policy.maximum_assignment_search_nodes,
        ):
            certain = (
                set()
                if component.truncated
                else set.intersection(*(set(solution) for solution in component.alternatives))
            )
            selected.update(certain)
            uncertain = (
                component.indices
                if component.truncated
                else set.union(*(set(solution) for solution in component.alternatives)) - certain
            )
            reason = (
                "measurement_assignment_search_truncated"
                if component.truncated
                else "measurement_assignment_ambiguous"
            )
            for index in uncertain:
                track_index, observation_index = pairs[index]
                blocked.add(observation_index)
                ambiguous_tracks[track_index] = reason
        self._seen.update(refs)
        self._horizon_ns = event_time_ns
        events: list[m.TrackEvent] = []
        assigned_tracks: set[int] = set()
        assigned_observations: set[int] = set()
        for index in sorted(selected):
            track_index, observation_index = pairs[index]
            events.append(self._assimilate(tracks[track_index], observations[observation_index]))
            assigned_tracks.add(track_index)
            assigned_observations.add(observation_index)
        for index, observation in enumerate(observations):
            if (
                index not in assigned_observations
                and index not in blocked
                and observation.measurement.motion_alignment is None
            ):
                track = _Track(
                    track_id=m.TrackId(f"track-{self._next_id}"),
                    last_measurement=observation.measurement,
                    seed_position=observation.position,
                    seed_covariance=observation.covariance,
                )
                self._next_id += 1
                self._tracks[track.track_id] = track
                events.append(
                    self._event(
                        track,
                        observation.measurement.reference_time_ns,
                        ("position_seed",),
                        (observation.ref,),
                    )
                )
        events.extend(self._retire(event_time_ns))
        for index, track in enumerate(tracks):
            if index not in assigned_tracks and track.track_id in self._tracks:
                reason = ambiguous_tracks.get(
                    index, "measurement_gate_failed" if observations else "no_measurement"
                )
                events.extend(self._missing(track, event_time_ns, emit=True, reason=reason))
        return tuple(sorted(events, key=lambda event: (event.event_time_ns, event.track_id)))

    def advance(self, event_clock: f.EventClockReading) -> tuple[m.TrackEvent, ...]:
        self._check_time(event_clock.clock_domain_id, event_clock.event_time_ns)
        if event_clock.mapping_uncertainty_ns != 0:
            raise ValueError("tracking advancement requires exact consumed-window time")
        self._horizon_ns = event_clock.event_time_ns
        events = self._retire(event_clock.event_time_ns)
        for track in self._tracks.values():
            events.extend(
                self._missing(track, event_clock.event_time_ns, emit=False, reason="no_measurement")
            )
        return tuple(events)
