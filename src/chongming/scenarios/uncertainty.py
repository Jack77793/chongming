"""Named Gaussian streams for generation, never online algorithm input."""

import hashlib

import numpy as np

from chongming.uncertainty import covariance_factor


def gaussian_sample(
    covariance: tuple[tuple[float, ...], ...], *, seed: int, stream: str, label: str
) -> tuple[float, ...]:
    """PCG64 stream depends only on the explicit seed and semantic sample key."""
    factor = covariance_factor(covariance, len(covariance), label)
    if not np.any(factor):
        return (0.0,) * len(covariance)
    digest = hashlib.sha256(f"{seed}:{stream}".encode()).digest()
    generator = np.random.Generator(np.random.PCG64(int.from_bytes(digest, "big")))
    return tuple(float(value) for value in factor @ generator.standard_normal(len(covariance)))
