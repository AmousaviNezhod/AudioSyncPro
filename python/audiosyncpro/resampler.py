from __future__ import annotations

import math

import numpy as np


def to_mono(samples: np.ndarray, channel_layout: str = "") -> np.ndarray:
    """If stereo/multi-channel samples are interleaved, convert to mono.

    Currently expects ffmpeg -ac 1 output (already mono). This is a
    placeholder for future explicit channel handling.
    """
    if samples.ndim == 1:
        return samples
    # If shape is (channels, samples) average over axis 0.
    if samples.ndim == 2:
        return samples.mean(axis=0)
    raise ValueError("Unsupported sample shape")


def _next_power_of_two(n: int) -> int:
    if n < 1:
        return 1
    return 1 << (n - 1).bit_length()


def resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear interpolation resampling. Good for modest ratios and coarse search."""
    if src_rate == dst_rate:
        return samples.copy()
    n = len(samples)
    out_n = int(round(n * dst_rate / src_rate))
    if out_n < 2:
        return np.array([float(samples[0])], dtype=np.float64)
    old_t = np.linspace(0, n - 1, n, dtype=np.float64)
    new_t = np.linspace(0, n - 1, out_n, dtype=np.float64)
    return np.interp(new_t, old_t, samples).astype(np.float64)


def downsample_decimate(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Fast integer-ratio downsampling by averaging blocks.

    Use only when dst_rate divides src_rate reasonably or as a coarse approximation.
    """
    if src_rate == dst_rate:
        return samples.copy()
    ratio = src_rate / dst_rate
    out_n = int(math.floor(len(samples) / ratio))
    if out_n < 1:
        return samples.copy()
    # Use a simple strided mean via reshape when possible.
    if abs(ratio - round(ratio)) < 1e-9:
        ratio = int(round(ratio))
        valid = out_n * ratio
        return samples[:valid].reshape(out_n, ratio).mean(axis=1)
    # General linear interpolation fallback.
    return resample_linear(samples, src_rate, dst_rate)
