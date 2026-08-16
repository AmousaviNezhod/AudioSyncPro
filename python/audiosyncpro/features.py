from __future__ import annotations

import math

import numpy as np


def hann_window(n: int) -> np.ndarray:
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / (n - 1))


def next_power_of_two(n: int) -> int:
    if n < 1:
        return 1
    return 1 << (n - 1).bit_length()


def mel_filterbank(sr: float, n_fft: int, n_mels: int = 40, f_min: float = 80.0, f_max: Optional[float] = None) -> np.ndarray:
    """Build a mel filterbank matrix (n_freqs, n_mels)."""
    if f_max is None:
        f_max = sr / 2.0

    def hz_to_mel(hz: float) -> float:
        return 2595.0 * math.log10(1.0 + hz / 700.0)

    def mel_to_hz(mel: float) -> float:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    min_mel = hz_to_mel(f_min)
    max_mel = hz_to_mel(f_max)
    mel_points = np.linspace(min_mel, max_mel, n_mels + 2)
    hz_points = np.array([mel_to_hz(m) for m in mel_points])

    fft_freqs = np.linspace(0.0, sr / 2.0, int(n_fft // 2) + 1)
    weights = np.zeros((len(fft_freqs), n_mels), dtype=np.float64)

    for m in range(n_mels):
        f_left = hz_points[m]
        f_center = hz_points[m + 1]
        f_right = hz_points[m + 2]
        # Rising
        rise = (fft_freqs - f_left) / (f_center - f_left)
        # Falling
        fall = (f_right - fft_freqs) / (f_right - f_center)
        weights[:, m] = np.maximum(0.0, np.minimum(rise, fall))

    # Normalize each mel band by its bin count / bandwidth to keep energy comparable.
    norm = weights.sum(axis=0, keepdims=True)
    norm[norm == 0] = 1.0
    return weights / norm


def stft(samples: np.ndarray, n_fft: int, hop_length: int, win_length: Optional[int] = None) -> np.ndarray:
    """Short-time Fourier transform, magnitude, shape (n_frames, n_freqs)."""
    if win_length is None:
        win_length = n_fft
    win = hann_window(win_length)
    pad = win_length // 2
    x = np.pad(samples.astype(np.float64), (pad, pad), mode="constant")
    n_frames = 1 + (len(x) - win_length) // hop_length
    if n_frames < 1:
        return np.zeros((0, n_fft // 2 + 1), dtype=np.float64)
    frames = np.lib.stride_tricks.as_strided(
        x,
        shape=(n_frames, win_length),
        strides=(hop_length * x.itemsize, x.itemsize),
        writeable=False,
    ).copy()
    frames *= win
    spec = np.fft.rfft(frames, n=n_fft, axis=1)
    return np.abs(spec).astype(np.float64)


def log_mel_spectrogram(
    samples: np.ndarray,
    sr: float,
    n_fft: int = 1024,
    hop_length: Optional[int] = None,
    n_mels: int = 40,
    f_min: float = 80.0,
    f_max: Optional[float] = None,
) -> np.ndarray:
    if hop_length is None:
        hop_length = win_length = n_fft // 4
    else:
        win_length = n_fft
    spec = stft(samples, n_fft=n_fft, hop_length=hop_length, win_length=win_length)
    mel_basis = mel_filterbank(sr, n_fft, n_mels=n_mels, f_min=f_min, f_max=f_max)
    mel = np.dot(spec, mel_basis).T  # (n_mels, n_frames)
    eps = 1e-10
    return np.log10(mel + eps)


def energy_envelope(samples: np.ndarray, sr: float, target_sr: float = 400.0, window_ms: float = 25.0) -> np.ndarray:
    """Compute energy envelope and downsample to target_sr (vectorized)."""
    x = np.asarray(samples, dtype=np.float64)
    if x.size == 0:
        return np.zeros(0, dtype=np.float64)
    window_samples = max(1, int(round(window_ms * sr / 1000.0)))
    hop_samples = max(1, int(round(sr / target_sr)))
    half = window_samples // 2
    x2 = x ** 2
    cum = np.concatenate(([0.0], np.cumsum(x2, dtype=np.float64)))
    n = len(x)
    starts = np.arange(0, n, hop_samples)
    left = starts - half
    right = left + window_samples
    left = np.clip(left, 0, n)
    right = np.clip(right, 0, n)
    counts = right - left
    counts = np.where(counts > 0, counts, 1)
    power = (cum[right] - cum[left]) / counts
    return np.sqrt(power + 1e-12)
