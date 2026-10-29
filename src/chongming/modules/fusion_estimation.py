"""Residual-space feasible GLS and block Huber IRLS, conditional on shared poses.

Zero covariance eigenvalues are equality constraints, not absent information.
The returned covariance is the local linear covariance under the final inflated
residual model (usage penalties and IRLS weights), not absolute pose accuracy or
an assertion of calibrated robust-estimator coverage.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from chongming.contracts import fusion as f
from chongming.contracts import model as m
from chongming.modules.fusion_motion import midpoint, origins_at
from chongming.uncertainty import covariance_factor

Array = NDArray[np.float64]


@dataclass(frozen=True)
class RayFit:
    position: Array
    covariance: Array
    velocity_projection: Array
    normalized_residuals: tuple[float | None, ...]
    robust_weights: tuple[float, ...]
    iterations: int
    converged: bool
    exact_consistent: bool
    condition: float


def residual_covariances(
    rays: tuple[f.AdmittedRay, ...], depths: Array, scales: tuple[float, ...]
) -> Array:
    """C_perp = depth**2 C_angle + T C_origin T.T, in square metres."""
    matrices = []
    for ray, depth, scale in zip(rays, depths, scales, strict=True):
        uncertainty = ray.event.uncertainty
        if uncertainty.model != "tangent_gaussian_v1":
            raise ValueError("unsupported ray uncertainty model")
        angular = covariance_factor(
            uncertainty.independent_angular_covariance_rad2, 2, "ray angular covariance"
        )
        origin = covariance_factor(
            uncertainty.independent_origin_covariance_m2, 3, "ray origin covariance"
        )
        basis = np.asarray((uncertainty.tangent_u_enu_unit, uncertainty.tangent_v_enu_unit))
        roots = np.hstack((depth * angular, basis @ origin)) / np.sqrt(scale)
        matrices.append(roots @ roots.T)
    return np.asarray(matrices)


def _spectrum(matrix: Array) -> tuple[Array, Array, NDArray[np.bool_]]:
    values, vectors = np.linalg.eigh((matrix + matrix.T) / 2)
    tolerance = 64 * np.finfo(np.float64).eps * float(np.max(np.abs(values)))
    if float(values.min()) < -tolerance:
        raise np.linalg.LinAlgError("residual covariance is not positive semidefinite")
    return values, vectors, values > tolerance


def _linear_estimator(design: Array, covariances: Array) -> tuple[Array, Array, float]:
    """Map all 2N residual coordinates to position, subject to exact constraints."""
    soft, hard = [], []
    dimension = design.shape[0]
    for index, covariance in enumerate(covariances):
        values, vectors, positive = _spectrum(covariance)
        selector = np.eye(dimension)[2 * index : 2 * index + 2]
        soft.append((vectors[:, positive] / np.sqrt(values[positive])).T @ selector)
        hard.append(vectors[:, ~positive].T @ selector)
    whitening = np.vstack(soft)
    equality = np.vstack(hard)
    exact_design = equality @ design
    if len(exact_design):
        left, singular, right = np.linalg.svd(exact_design, full_matrices=True)
        tolerance = max(exact_design.shape) * np.finfo(np.float64).eps * singular[0]
        rank = int(np.count_nonzero(singular > tolerance))
        inverse = (right[:rank].T / singular[:rank]) @ left[:, :rank].T
        particular = inverse @ equality
        null = right[rank:].T
    else:
        particular, null = np.zeros((3, dimension)), np.eye(3)
    reduced = whitening @ design @ null
    condition = 1.0
    if null.shape[1]:
        inverse, _, reduced_rank, singular = np.linalg.lstsq(
            reduced, np.eye(reduced.shape[0]), rcond=None
        )
        if reduced_rank != null.shape[1]:
            raise np.linalg.LinAlgError("insufficient independent residual information")
        condition = float((singular[0] / singular[-1]) ** 2)
        estimator = particular + null @ inverse @ whitening @ (
            np.eye(dimension) - design @ particular
        )
    else:
        estimator = particular
    roots = np.zeros((dimension, dimension))
    for index, covariance in enumerate(covariances):
        values, vectors, positive = _spectrum(covariance)
        roots[2 * index : 2 * index + 2, 2 * index : 2 * index + 2] = vectors * np.sqrt(
            np.where(positive, values, 0.0)
        )
    propagated = estimator @ roots
    return estimator, propagated @ propagated.T, condition


def _residual_norms(
    residuals: Array, covariances: Array, roundoff: float
) -> tuple[tuple[float | None, ...], bool]:
    norms: list[float | None] = []
    consistent = True
    for residual, covariance in zip(residuals, covariances, strict=True):
        values, vectors, positive = _spectrum(covariance)
        components = vectors.T @ residual
        if np.any(np.abs(components[~positive]) > roundoff):
            norms.append(None)
            consistent = False
        else:
            norms.append(float(np.linalg.norm(components[positive] / np.sqrt(values[positive]))))
    return tuple(norms), consistent


def fit_rays(
    rays: tuple[f.AdmittedRay, ...],
    policy: f.GeometryPolicy,
    scales: tuple[float, ...],
    initial: Array,
    *,
    reference_ns: int,
    velocity: m.Vec3 | None,
) -> RayFit:
    """Iterate depth-dependent noise and Huber weights without a hidden noise floor."""
    if policy.robust_loss_definition not in ("least_squares", "huber"):
        raise NotImplementedError("unsupported geometry loss")
    origins = (
        np.asarray([ray.event.ray_origin_enu_m for ray in rays], dtype=np.float64)
        if velocity is None
        else origins_at(rays, reference_ns, velocity)
    )
    directions = np.asarray([ray.event.ray_direction_enu_unit for ray in rays])
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    bases = np.asarray(
        [
            (ray.event.uncertainty.tangent_u_enu_unit, ray.event.uncertainty.tangent_v_enu_unit)
            for ray in rays
        ]
    )
    design = bases.reshape(-1, 3)
    anchor = origins.mean(axis=0)
    offsets = np.einsum("nij,nj->ni", bases, origins - anchor).reshape(-1)
    position = initial.copy()
    roundoff = (
        256
        * np.finfo(np.float64).eps
        * max(1.0, float(np.max(np.abs(origins))), float(np.max(np.abs(initial))))
    )
    converged = False
    for iteration in range(1, policy.maximum_solver_iterations + 1):
        depths = np.einsum("ni,ni->n", directions, position - origins)
        covariance = residual_covariances(rays, depths, scales)
        residuals = np.einsum("nij,nj->ni", bases, position - origins)
        normalized, _ = _residual_norms(residuals, covariance, roundoff)
        weights = np.asarray(
            [
                min(1.0, policy.robust_loss_scale_sigma / value)
                if policy.robust_loss_definition == "huber" and value is not None and value > 0
                else 1.0
                for value in normalized
            ]
        )
        estimator, conditional, condition = _linear_estimator(
            design, covariance / weights[:, None, None]
        )
        updated = anchor + estimator @ offsets
        if not np.isfinite(updated).all() or not np.isfinite(conditional).all():
            raise np.linalg.LinAlgError("nonfinite weighted ray solution")
        change = float(np.linalg.norm(updated - position))
        position = updated
        if change <= max(policy.convergence_tolerance_m, roundoff):
            converged = True
            break
    depths = np.einsum("ni,ni->n", directions, position - origins)
    covariance = residual_covariances(rays, depths, scales)
    residuals = np.einsum("nij,nj->ni", bases, position - origins)
    normalized, consistent = _residual_norms(residuals, covariance, roundoff)
    dt = np.asarray([(midpoint(ray) - reference_ns) / 1e9 for ray in rays])
    projection = estimator @ (dt[:, None, None] * bases).reshape(-1, 3)
    return RayFit(
        position=position,
        covariance=conditional,
        velocity_projection=projection,
        normalized_residuals=normalized,
        robust_weights=tuple(float(value) for value in weights),
        iterations=iteration,
        converged=converged,
        exact_consistent=consistent,
        condition=condition,
    )
