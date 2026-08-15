from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .algorithms.gcc_phat import gcc_phat
from .types import DriftEstimate


def estimate_drift(
    ref: np.ndarray,
    target: np.ndarray,
    sample_rate: int,
    base_offset_seconds: float,
    min_duration: float = 60.0,
    window_seconds: float = 10.0,
    hop_seconds: float = 5.0,
    fine_range_seconds: float = 2.0,
) -> Optional[DriftEstimate]:
    """Estimate clock drift as offset(t) = offset0 + drift*t.

    Returns a DriftEstimate if enough overlapping audio exists.
    """
    ref = np.asarray(ref, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    ref_len_s = len(ref) / sample_rate
    tgt_len_s = len(target) / sample_rate
    overlap_s = max(0.0, min(ref_len_s, base_offset_seconds + tgt_len_s) - max(0.0, base_offset_seconds))

    if overlap_s < min_duration:
        return None

    window_samples = int(round(window_seconds * sample_rate))
    hop_samples = max(1, int(round(hop_seconds * sample_rate)))
    fine_range = int(round(fine_range_seconds * sample_rate))

    base_lag = int(round(base_offset_seconds * sample_rate))
    times = []
    offsets = []
    weights = []

    start = max(0, base_lag)
    end = min(len(ref), base_lag + len(target))
    if end - start < window_samples:
        return None

    for center in range(start + window_samples // 2, end - window_samples // 2, hop_samples):
        ref_win = ref[center - window_samples // 2 : center + window_samples // 2]
        # corresponding target segment is base_offset earlier
        tgt_center = center - base_lag
        lo = max(0, tgt_center - window_samples // 2 - fine_range)
        hi = min(len(target), tgt_center + window_samples // 2 + fine_range)
        if hi - lo < window_samples + fine_range:
            continue
        tgt_win = target[lo:hi]
        try:
            res = gcc_phat(ref_win, tgt_win, sample_rate, max_offset_seconds=fine_range_seconds)
        except Exception:
            continue
        local_lag = res["lag_samples"] + (lo - (center - window_samples // 2 - fine_range))
        # local_lag relative to ref segment start; global offset = base_lag + local_lag deviation
        global_offset = base_lag + local_lag
        t_ref = center / sample_rate
        times.append(t_ref)
        offsets.append(global_offset / sample_rate)
        weights.append(max(0.0, res["correlation"]))

    if len(times) < 3:
        return None

    times = np.array(times, dtype=np.float64)
    offsets = np.array(offsets, dtype=np.float64)
    weights = np.array(weights, dtype=np.float64)
    weights[weights < 1e-6] = 1e-6

    # Weighted linear regression: offset = offset0 + drift * t
    W = np.diag(weights)
    A = np.vstack([np.ones_like(times), times]).T
    Aw = A.T @ W
    try:
        coeffs = np.linalg.lstsq(Aw @ A, Aw @ offsets, rcond=None)[0]
    except Exception:
        return None

    offset0, drift = float(coeffs[0]), float(coeffs[1])
    predicted = offset0 + drift * times
    rmse = float(np.sqrt(np.mean(weights * (offsets - predicted) ** 2) / np.mean(weights)))
    slope = drift
    ppm = slope * 1_000_000.0
    confidence = float(np.mean(weights))

    return DriftEstimate(
        slope=slope,
        ppm=ppm,
        offset0=offset0,
        fit_error=rmse,
        confidence=confidence,
        window_count=len(times),
    )
