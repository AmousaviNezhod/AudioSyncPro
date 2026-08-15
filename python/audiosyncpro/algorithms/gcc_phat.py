from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..features import hann_window, next_power_of_two


def pearson_at_lag(ref: np.ndarray, target: np.ndarray, lag: int) -> float:
    """Pearson correlation of ref and target shifted by integer lag (positive = target delayed)."""
    ref_len = len(ref)
    tgt_len = len(target)
    if lag >= 0:
        # target is delayed: align target[lag:] with ref[:ref_len-lag]
        start_ref = 0
        start_tgt = lag
        end_ref = min(ref_len, tgt_len - lag)
        end_tgt = start_tgt + end_ref
    else:
        # target is advanced: align target[:tgt_len+lag] with ref[-lag:]
        start_ref = -lag
        start_tgt = 0
        end_tgt = min(tgt_len, ref_len + lag)
        end_ref = start_ref + end_tgt
    count = min(end_ref - start_ref, end_tgt - start_tgt)
    if count <= 1:
        return 0.0
    r = ref[start_ref:start_ref + count]
    t = target[start_tgt:start_tgt + count]
    rm = r - np.mean(r)
    tm = t - np.mean(t)
    den = math.sqrt(np.sum(rm * rm) * np.sum(tm * tm))
    if den == 0:
        return 0.0
    return float(np.sum(rm * tm) / den)


def gcc_phat(
    ref: np.ndarray,
    target: np.ndarray,
    sample_rate: int,
    max_offset_seconds: Optional[float] = None,
) -> dict:
    """Generalized Cross Correlation with Phase Transform.

    Returns dict with:
      - lag_samples (float, sub-sample)
      - offset_seconds
      - raw_peak
      - correlation (Pearson at integer lag)
    """
    ref = np.asarray(ref, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    ref_len = len(ref)
    tgt_len = len(target)
    if ref_len == 0 or tgt_len == 0:
        raise RuntimeError("empty audio buffers")

    max_lag = int((max_offset_seconds or 5.0) * sample_rate)
    hard_max = min(ref_len, tgt_len) - 1
    if max_lag > hard_max:
        max_lag = hard_max
    if max_lag < 1:
        max_lag = 1

    # Do NOT independently window ref and target before correlation, because a
    # fixed window is not shift-invariant and would attenuate the true offset.
    # Zero-pad to the next power of two for FFT efficiency.
    n = next_power_of_two(ref_len + tgt_len - 1)
    A = np.fft.rfft(ref, n)
    B = np.fft.rfft(target, n)
    cross = A.conj() * B
    eps = 1e-12
    gcc = np.fft.irfft(cross / (np.abs(cross) + eps), n)

    # search vector: negative lags first, then positive
    search = np.concatenate((gcc[n - max_lag:], gcc[: max_lag + 1]))
    best_idx = int(np.argmax(np.abs(search)))
    raw_peak = float(search[best_idx])

    if best_idx < max_lag:
        best_lag = best_idx - max_lag
    else:
        best_lag = best_idx - max_lag

    # Parabolic interpolation around the peak.
    idx_in_gcc = best_lag if best_lag >= 0 else n + best_lag
    i0 = (idx_in_gcc - 1 + n) % n
    i2 = (idx_in_gcc + 1) % n
    y0 = float(gcc[i0])
    y1 = float(gcc[idx_in_gcc])
    y2 = float(gcc[i2])
    denom = y0 - 2 * y1 + y2
    p = 0.0
    if abs(denom) > 1e-12:
        cand = 0.5 * (y0 - y2) / denom
        if math.isfinite(cand) and abs(cand) < 1.0:
            p = cand

    k = float(best_lag) + p

    best_lag_int = int(round(best_lag))
    pearson = pearson_at_lag(ref, target, best_lag_int)
    if math.isnan(pearson):
        pearson = 0.0
    pearson = max(0.0, min(1.0, abs(pearson)))

    return {
        "lag_samples": k,
        "offset_seconds": k / sample_rate,
        "raw_peak": raw_peak,
        "correlation": pearson,
    }


def gcc_phat_pairwise(
    ref: np.ndarray,
    target: np.ndarray,
    sample_rate: int,
    max_offset_seconds: float = 30.0,
    coarse_offset_seconds: Optional[float] = None,
    fine_search_seconds: float = 2.0,
) -> dict:
    """Pairwise fine GCC-PHAT around an optional coarse offset.

    If coarse_offset_seconds is provided, crop reference and target windows
    around the expected overlap to keep FFTs small and accurate.
    """
    ref = np.asarray(ref, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    ref_len = len(ref)
    tgt_len = len(target)

    if coarse_offset_seconds is None:
        # Full search; require max_offset_seconds.
        return gcc_phat(ref, target, sample_rate, max_offset_seconds=max_offset_seconds)

    # coarse_offset: target starts at ref[coarse_offset_seconds * sr]
    # (positive = target delayed relative to ref)
    coarse_lag = int(round(coarse_offset_seconds * sample_rate))
    margin = int(round(fine_search_seconds * sample_rate))

    # Crop reference window that covers the overlap with target.
    # If target is delayed, the overlap begins at ref[coarse_lag].
    ref_start = max(0, coarse_lag - margin)
    ref_end = min(ref_len, coarse_lag + tgt_len + margin)
    if ref_end - ref_start < margin * 2:
        ref_end = min(ref_len, ref_start + margin * 4)
    ref_window = ref[ref_start:ref_end]

    # Crop target window covering overlap.
    tgt_start = max(0, -coarse_lag - margin)
    tgt_end = min(tgt_len, -coarse_lag + ref_len + margin)
    if tgt_end - tgt_start < margin * 2:
        tgt_end = min(tgt_len, tgt_start + margin * 4)
    target_window = target[tgt_start:tgt_end]

    # Window the cropped analysis windows to reduce edge discontinuities.
    if len(ref_window) > 1:
        ref_window = ref_window * hann_window(len(ref_window))
    if len(target_window) > 1:
        target_window = target_window * hann_window(len(target_window))

    result = gcc_phat(ref_window, target_window, sample_rate, max_offset_seconds=fine_search_seconds)
    # Adjust lag to global sample indices.
    result["lag_samples"] += (ref_start - tgt_start)
    result["offset_seconds"] = float(result["lag_samples"]) / sample_rate
    return result
