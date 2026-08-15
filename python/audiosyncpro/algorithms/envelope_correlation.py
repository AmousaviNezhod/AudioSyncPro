from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..features import energy_envelope, next_power_of_two


def envelope_correlation(
    ref: np.ndarray,
    target: np.ndarray,
    sample_rate: int,
    max_offset_seconds: float = 30.0,
    envelope_sr: float = 400.0,
) -> dict:
    """Coarse correlation of energy envelopes."""
    ref_env = energy_envelope(np.asarray(ref, dtype=np.float64), sample_rate, target_sr=envelope_sr)
    tgt_env = energy_envelope(np.asarray(target, dtype=np.float64), sample_rate, target_sr=envelope_sr)

    ref_len = len(ref_env)
    tgt_len = len(tgt_env)
    if ref_len == 0 or tgt_len == 0:
        return {"lag_samples": 0.0, "offset_seconds": 0.0, "raw_peak": 0.0, "correlation": 0.0}

    max_lag = int(max_offset_seconds * envelope_sr)
    hard_max = min(ref_len, tgt_len) - 1
    if max_lag > hard_max:
        max_lag = hard_max
    if max_lag < 1:
        max_lag = 1

    # Remove DC and normalize.
    ref_env = ref_env - np.mean(ref_env)
    tgt_env = tgt_env - np.mean(tgt_env)
    rms_r = math.sqrt(float(np.sum(ref_env ** 2)))
    rms_t = math.sqrt(float(np.sum(tgt_env ** 2)))
    if rms_r == 0 or rms_t == 0:
        return {"lag_samples": 0.0, "offset_seconds": 0.0, "raw_peak": 0.0, "correlation": 0.0}

    n = next_power_of_two(ref_len + tgt_len - 1)
    A = np.fft.rfft(ref_env, n)
    B = np.fft.rfft(tgt_env, n)
    cross = A.conj() * B
    corr = np.fft.irfft(cross / (np.abs(cross) + 1e-12), n)

    search = np.concatenate((corr[n - max_lag:], corr[: max_lag + 1]))
    best_idx = int(np.argmax(np.abs(search)))
    raw_peak = float(search[best_idx])

    if best_idx < max_lag:
        best_lag = best_idx - max_lag
    else:
        best_lag = best_idx - max_lag

    # Parabolic interpolation.
    idx_in_corr = best_lag if best_lag >= 0 else n + best_lag
    i0 = (idx_in_corr - 1 + n) % n
    i2 = (idx_in_corr + 1) % n
    y0 = float(corr[i0])
    y1 = float(corr[idx_in_corr])
    y2 = float(corr[i2])
    p = 0.0
    denom = y0 - 2 * y1 + y2
    if abs(denom) > 1e-12:
        cand = 0.5 * (y0 - y2) / denom
        if math.isfinite(cand) and abs(cand) < 1.0:
            p = cand

    lag_samples_env = (float(best_lag) + p)
    lag_samples = lag_samples_env * (sample_rate / envelope_sr)

    # Pearson confidence at integer lag on raw waveforms.
    best_lag_int = int(round(lag_samples))
    from .gcc_phat import pearson_at_lag
    pearson = pearson_at_lag(np.asarray(ref, dtype=np.float64), np.asarray(target, dtype=np.float64), best_lag_int)
    pearson = max(0.0, min(1.0, abs(pearson)))

    return {
        "lag_samples": lag_samples,
        "offset_seconds": lag_samples / sample_rate,
        "raw_peak": raw_peak,
        "correlation": pearson,
    }
