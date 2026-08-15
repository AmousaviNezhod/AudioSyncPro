from __future__ import annotations

import numpy as np


def remove_dc(samples: np.ndarray) -> np.ndarray:
    if len(samples) == 0:
        return samples
    return samples - np.mean(samples)


def pre_emphasis(samples: np.ndarray, coeff: float = 0.97) -> np.ndarray:
    """High-frequency emphasis filter."""
    if len(samples) == 0:
        return samples
    out = np.zeros_like(samples, dtype=np.float64)
    out[0] = float(samples[0])
    out[1:] = samples[1:] - coeff * samples[:-1]
    return out


def normalize_rms(samples: np.ndarray, target_rms: float = 0.1, min_rms: float = 1e-5) -> np.ndarray:
    """Normalize to target RMS while avoiding boosting pure noise/silence."""
    if len(samples) == 0:
        return samples
    rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
    if rms < min_rms or not np.isfinite(rms):
        return samples.astype(np.float64)
    return samples.astype(np.float64) * (target_rms / rms)


def preprocess(samples: np.ndarray, target_rms: float = 0.1, pre_emphasis_coeff: float = 0.97) -> np.ndarray:
    x = remove_dc(samples)
    x = pre_emphasis(x, pre_emphasis_coeff)
    x = normalize_rms(x, target_rms)
    return x
