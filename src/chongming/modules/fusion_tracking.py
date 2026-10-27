"""Single-target constant-velocity Kalman tracking on consumed event time."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.modules.base import ModuleShell
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


class CenterTrackingModule(ModuleShell):
    """Two distinct exposure times initialize velocity, without a guessed prior.

    A lone position is tentative with unknown velocity/covariance. Acceleration is
    constant over each prediction interval: Q = G Cov(a) G.T, G=[dt²/2 I; dt I].
    Read predictions and lifecycle ticks never recondition or split the posterior.
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
        self._track: _Track | None = None
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
        track = self._track
        if track is None or track.state is None:
            return ()
        state, covariance = self._predict(track, reference_time_ns)
        return (
            f.TrackPrediction(
                center_session_id=self.center_session_id,
                track_id=track.track_id,
                clock_domain_id=self.clock_domain_id,
                world_frame_id=self.world_frame_id,
                reference_time_ns=reference_time_ns,
                position_enu_m=_vec3(state[:3]),
                velocity_enu_mps=_vec3(state[3:]),
                conditional_state_covariance_enu=_matrix6(covariance),
            ),
        )

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

    def _retire(self, time_ns: int) -> list[m.TrackEvent]:
        track = self._track
        if track is None:
            return []
        gap = time_ns - track.last_measurement.reference_time_ns
        unconfirmed_expired = (
            track.lifecycle is m.TrackLifecycle.TENTATIVE
            and gap > self.policy.confirmation_max_gap_ns
        )
        if gap >= self.policy.delete_after_ns or unconfirmed_expired:
            track.lifecycle = m.TrackLifecycle.DELETED
            event = self._event(
                track,
                time_ns,
                ("confirmation_expired" if unconfirmed_expired else "measurement_timeout",),
            )
            self._track = None
            return [event]
        return []

    def _missing(self, time_ns: int, *, emit: bool, reason: str) -> list[m.TrackEvent]:
        track = self._track
        if track is None:
            return []
        changed = False
        if (
            track.lifecycle is m.TrackLifecycle.CONFIRMED
            and time_ns - track.last_measurement.reference_time_ns >= self.policy.coast_after_ns
        ):
            track.lifecycle = m.TrackLifecycle.COASTING
            changed = True
        return [self._event(track, time_ns, (reason,))] if changed or emit else []

    def update(
        self,
        measurements: tuple[m.FusionMeasurement, ...],
        clock_domain_id: str,
        event_time_ns: int,
    ) -> tuple[m.TrackEvent, ...]:
        self._check_time(clock_domain_id, event_time_ns)
        located = [
            measurement
            for measurement in measurements
            if measurement.status is m.FusionStatus.LOCATED
        ]
        if len(located) > 1:
            raise NotImplementedError("single-target tracking cannot assign multiple measurements")
        measurement = located[0] if located else None
        position, covariance = None, None
        ref = None
        if measurement is not None:
            if (
                measurement.center_session_id,
                measurement.clock_domain_id,
                measurement.world_frame_id,
                measurement.reference_time_ns,
            ) != (self.center_session_id, self.clock_domain_id, self.world_frame_id, event_time_ns):
                raise ValueError("measurement session, frame, clock or reference time mismatch")
            if (
                measurement.position_enu_m is None
                or not np.isfinite(measurement.position_enu_m).all()
            ):
                raise ValueError("located measurement requires a finite position")
            if measurement.conditional_position_covariance_enu_m2 is None:
                raise ValueError("tracking requires an explicit conditional measurement covariance")
            factor = covariance_factor(
                measurement.conditional_position_covariance_enu_m2, 3, "measurement covariance (m2)"
            )
            position = np.asarray(measurement.position_enu_m, dtype=np.float64)
            covariance = factor @ factor.T
            ref = m.MeasurementRef(
                center_session_id=measurement.center_session_id,
                measurement_id=measurement.measurement_id,
            )
            if ref in self._seen:
                raise ValueError("measurement already consumed")
            if (
                self._track is not None
                and event_time_ns == self._track.last_measurement.reference_time_ns
            ):
                raise ValueError("velocity initialization/update requires a later exposure")
        self._horizon_ns = event_time_ns
        events = self._retire(event_time_ns)
        if measurement is None:
            return tuple(events + self._missing(event_time_ns, emit=True, reason="no_measurement"))
        assert position is not None and covariance is not None and ref is not None
        self._seen.add(ref)
        track = self._track
        if track is None:
            track = _Track(
                track_id=m.TrackId(f"track-{self._next_id}"),
                last_measurement=measurement,
                seed_position=position,
                seed_covariance=covariance,
            )
            self._next_id += 1
            self._track = track
            return tuple(events + [self._event(track, event_time_ns, ("position_seed",), (ref,))])
        dt = (event_time_ns - track.last_measurement.reference_time_ns) / 1e9
        if track.state is None:
            track.state = np.concatenate((position, (position - track.seed_position) / dt))
            track.covariance = np.block(
                [
                    [covariance, covariance / dt],
                    [covariance / dt, (track.seed_covariance + covariance) / dt**2],
                ]
            )
        else:
            state, prior = self._predict(track, event_time_ns)
            innovation = position - state[:3]
            residual_covariance = prior[:3, :3] + covariance
            values, vectors = np.linalg.eigh((residual_covariance + residual_covariance.T) / 2)
            tolerance = 96 * np.finfo(np.float64).eps * float(np.max(np.abs(values)))
            if float(values.min()) < -tolerance:
                raise np.linalg.LinAlgError("innovation covariance is not positive semidefinite")
            positive = values > tolerance
            components = vectors.T @ innovation
            roundoff = (
                32
                * np.finfo(np.float64).eps
                * max(1.0, float(np.max(np.abs(position))), float(np.max(np.abs(state[:3]))))
            )
            impossible = bool(np.any(np.abs(components[~positive]) > roundoff))
            distance = (
                math.inf
                if impossible
                else float(np.sum(components[positive] ** 2 / values[positive]))
            )
            if distance > self.policy.association_gate_mahalanobis_squared:
                return tuple(
                    events
                    + self._missing(event_time_ns, emit=True, reason="measurement_gate_failed")
                )
            inverse = (vectors[:, positive] / values[positive]) @ vectors[:, positive].T
            gain = prior[:, :3] @ inverse
            track.state = state + gain @ innovation
            correction = np.eye(6)
            correction[:, :3] -= gain
            posterior = correction @ prior @ correction.T + gain @ covariance @ gain.T
            track.covariance = (posterior + posterior.T) / 2
        recovered = track.lifecycle is m.TrackLifecycle.COASTING
        track.count += 1
        track.last_measurement = measurement
        if track.count >= self.policy.confirmation_measurement_count:
            track.lifecycle = m.TrackLifecycle.CONFIRMED
        reasons = (
            ("measurement_update", "track_recovered") if recovered else ("measurement_update",)
        )
        return tuple(events + [self._event(track, event_time_ns, reasons, (ref,))])

    def advance(self, event_clock: f.EventClockReading) -> tuple[m.TrackEvent, ...]:
        self._check_time(event_clock.clock_domain_id, event_clock.event_time_ns)
        if event_clock.mapping_uncertainty_ns != 0:
            raise ValueError("tracking advancement requires exact consumed-window time")
        self._horizon_ns = event_clock.event_time_ns
        return tuple(
            self._retire(event_clock.event_time_ns)
            + self._missing(event_clock.event_time_ns, emit=False, reason="no_measurement")
        )
