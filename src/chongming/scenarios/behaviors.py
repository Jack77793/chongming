"""Closed, versioned first-party behavior registry; no configuration imports."""

import math
from dataclasses import replace

from chongming.contracts.model import Matrix3x3
from chongming.contracts.scene import FlightBehaviorSpec, KinematicState

REGISTRY = {
    ("constant-velocity", "1"): frozenset({"vx", "vy", "vz"}),
    ("orbit", "1"): frozenset(
        {
            "center_e",
            "center_n",
            "center_u",
            "radius_e",
            "radius_n",
            "period_s",
            "phase_rad",
            "vertical_amplitude_m",
        }
    ),
}


def _velocity_aligned_rotation(velocity: tuple[float, float, float]) -> Matrix3x3:
    """Compute rotation matrix where body y-axis (nose) aligns with velocity direction.

    Aircraft body frame: x right, y forward (nose), z up.
    Returns rotation_enu_from_body such that body y points along velocity.
    When velocity is near-vertical, keeps body x pointing east.
    """
    vx, vy, vz = velocity
    speed = math.sqrt(vx * vx + vy * vy + vz * vz)

    if speed < 1e-6:
        # Stationary or near-zero velocity: return identity (nose points north, wings level)
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

    # Body y-axis (nose) in ENU frame: normalized velocity
    forward = (vx / speed, vy / speed, vz / speed)

    # Choose a reference "up" direction to construct right-handed frame
    # When forward is near-vertical, use east as reference; otherwise use world up
    if abs(forward[2]) > 0.99:
        # Near-vertical: use east (1, 0, 0) as reference
        ref_up = (1.0, 0.0, 0.0)
    else:
        # Use world up (0, 0, 1) as reference
        ref_up = (0.0, 0.0, 1.0)

    # Body x-axis (right wing): ref_up cross forward, normalized
    rx = ref_up[1] * forward[2] - ref_up[2] * forward[1]
    ry = ref_up[2] * forward[0] - ref_up[0] * forward[2]
    rz = ref_up[0] * forward[1] - ref_up[1] * forward[0]
    right_len = math.sqrt(rx * rx + ry * ry + rz * rz)
    right = (rx / right_len, ry / right_len, rz / right_len)

    # Body z-axis (up): forward cross right (already normalized)
    up = (
        forward[1] * right[2] - forward[2] * right[1],
        forward[2] * right[0] - forward[0] * right[2],
        forward[0] * right[1] - forward[1] * right[0],
    )

    # Rotation matrix: columns are body axes expressed in ENU
    return (right, forward, up)


def validate_behavior(spec: FlightBehaviorSpec) -> None:
    allowed = REGISTRY.get((spec.behavior_id, spec.implementation_version))
    if allowed is None:
        raise ValueError(
            f"unknown behavior/version: {spec.behavior_id}/{spec.implementation_version}"
        )
    values = dict(spec.parameters)
    if len(values) != len(spec.parameters) or set(values) != allowed:
        raise ValueError(
            f"behavior {spec.behavior_id}/{spec.implementation_version} has invalid parameters"
        )
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in values.values()
    ):
        raise ValueError("behavior velocity parameters must be finite numbers")
    if not spec.random_stream:
        raise ValueError("behavior requires a named deterministic stream")


def sample_behavior(
    spec: FlightBehaviorSpec,
    initial: KinematicState,
    elapsed_ns: int,
) -> KinematicState:
    """Version 1 is analytic and consumes no random numbers or wall time.

    The first trajectory knot supplies the initial pose/time; subsequent knots
    are ignored when a behavior is selected. Future stochastic entries must use
    the scenario seed and the declared stream, never global RNG state.
    """
    validate_behavior(spec)
    values = dict(spec.parameters)
    dt = elapsed_ns / 1e9
    if spec.behavior_id == "constant-velocity":
        velocity = (float(values["vx"]), float(values["vy"]), float(values["vz"]))
        position = tuple(initial.position_enu_m[i] + velocity[i] * dt for i in range(3))
    elif spec.behavior_id == "orbit":
        period = float(values["period_s"])
        if period <= 0:
            raise ValueError("orbit period must be positive")
        omega = 2 * math.pi / period
        phase = float(values["phase_rad"]) + omega * dt
        re, rn = float(values["radius_e"]), float(values["radius_n"])
        amplitude = float(values["vertical_amplitude_m"])
        velocity = (
            re * omega * math.cos(phase),
            -rn * omega * math.sin(phase),
            2 * amplitude * omega * math.cos(2 * phase),
        )
        position = (
            float(values["center_e"]) + re * math.sin(phase),
            float(values["center_n"]) + rn * math.cos(phase),
            float(values["center_u"]) + amplitude * math.sin(2 * phase),
        )
    else:
        raise ValueError("registered behavior implementation is missing")

    # Compute heading-aligned rotation: aircraft body y-axis (nose) points along velocity.
    rotation = _velocity_aligned_rotation(velocity)

    return replace(
        initial,
        position_enu_m=(position[0], position[1], position[2]),
        velocity_enu_mps=velocity,
        rotation_enu_from_body=rotation,
    )
