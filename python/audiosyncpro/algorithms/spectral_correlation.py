from __future__ import annotations

import math
from typing import Optional

import numpy as np

from ..features import hann_window, log_mel_spectrogram, next_power_of_two


def _standardize_bands(mel: np.ndarray) -> np.ndarray:
    """Standardize each mel band across time."""
    m = mel.mean(axis=1, keepdims=True)
    s = mel.std(axis=1, keepdims=True)
    s[s < 1e-12] = 1.0
    return (mel - m) / s


def mel_spectral_gcc_phat(
    ref: np.ndarray,
    target: np.ndarray,
    sample_rate: int,
    max_offset_seconds: float = 30.0,
    n_fft: int = 1024,
    hop_length: Optional[int] = None,
    n_mels: int = 40,
    f_min: float = 80.0,
    f_max: Optional[float] = None,
) -> dict:
    """Mel-spectrogram cross-correlation with phase transform.

    Returns offset in seconds and Pearson confidence at the integer sample lag.
    """
    ref = np.asarray(ref, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)

    if hop_length is None:
        hop_length = n_fft // 4

    mel1 = _standardize_bands(log_mel_spectrogram(
        ref, sr=float(sample_rate), n_fft=n_fft, hop_length=hop_length, n_mels=n_mels, f_min=f_min, f_max=f_max
    ))
    mel2 = _standardize_bands(log_mel_spectrogram(
        target, sr=float(sample_rate), n_fft=n_fft, hop_length=hop_length, n_mels=n_mels, f_min=f_min, f_max=f_max
    ))

    n1 = mel1.shape[1]
    n2 = mel2.shape[1]
    if n1 == 0 or n2 == 0:
        return {"lag_samples": 0.0, "offset_seconds": 0.0, "raw_peak": 0.0, "correlation": 0.0}

    frame_rate = float(sample_rate) / hop_length
    max_lag_frames = int(max_offset_seconds * frame_rate)
    hard_max = min(n1, n2) - 1
    if max_lag_frames > hard_max:
        max_lag_frames = hard_max
    if max_lag_frames < 1:
        max_lag_frames = 1

    n = next_power_of_two(n1 + n2 - 1)
    A = np.fft.rfft(mel1, n=n, axis=1)
    B = np.fft.rfft(mel2, n=n, axis=1)
    cross = (A.conj() * B).sum(axis=0)
    eps = 1e-10
    gcc = np.fft.irfft(cross / (np.abs(cross) + eps), n=n)

    search = np.concatenate((gcc[n - max_lag_frames:], gcc[: max_lag_frames + 1]))
    best_idx = int(np.argmax(np.abs(search)))
    raw_peak = float(search[best_idx])

    if best_idx < max_lag_frames:
        best_lag = best_idx - max_lag_frames
    else:
        best_lag = best_idx - max_lag_frames

    idx_in_gcc = best_lag if best_lag >= 0 else n + best_lag
    i0 = (idx_in_gcc - 1 + n) % n
    i2 = (idx_in_gcc + 1) % n
    y0 = float(gcc[i0])
    y1 = float(gcc[idx_in_gcc])
    y2 = float(gcc[i2])
    p = 0.0
    denom = y0 - 2 * y1 + y2
    if abs(denom) > 1e-12:
        cand = 0.5 * (y0 - y2) / denom
        if math.isfinite(cand) and abs(cand) < 1.0:
            p = cand

    lag_frames = float(best_lag) + p
    lag_samples = lag_frames * hop_length

    # Confidence via Pearson on raw samples at the integer sample lag.
    best_lag_int = int(round(lag_samples))
    r_len = len(ref)
    t_len = len(target)
    if best_lag_int >= 0:
        start_r = best_lag_int
        start_t = 0
        end = min(r_len, t_len + best_lag_int)
    else:
        start_r = 0
        start_t = -best_lag_int
        end = min(r_len + best_lag_int, t_len)
    pearson = 0.0
    if end > start_r and end > start_t and end - start_r > 1:
        r = ref[start_r:end]
        t = target[start_t:start_t + (end - start_r)]
        rm = r - np.mean(r)
        tm = t - np.mean(t)
        den = math.sqrt(np.sum(rm * rm) * np.sum(tm * tm))
        if den > 0:
            pearson = float(np.sum(rm * tm) / den)
    pearson = max(0.0, min(1.0, abs(pearson)))

    return {
        "lag_samples": lag_samples,
        "offset_seconds": lag_samples / sample_rate,
        "raw_peak": raw_peak,
        "correlation": pearson,
    }
