from __future__ import annotations

import math
from typing import Any, Dict, Tuple

import numpy as np


def peak_metrics(corr: np.ndarray, best_idx: int) -> Tuple[float, float]:
    """Return (peak_ratio, z_score) for a correlation surface around the best index."""
    if corr.size == 0:
        return 0.0, 0.0
    peak = float(corr[best_idx])
    abs_corr = np.abs(corr)
    sorted_vals = np.sort(abs_corr)
    # second peak: highest value not at best_idx
    second_peak = 0.0
    for i in range(len(abs_corr) - 2, -1, -1):
        if sorted_vals[i] < abs_corr[best_idx] or (i < len(abs_corr) - 1 and sorted_vals[i] == abs_corr[best_idx]):
            second_peak = float(sorted_vals[i])
            break
    if second_peak == 0:
        second_peak = float(sorted_vals[max(0, len(sorted_vals) - 2)]) if len(sorted_vals) > 1 else 1e-9

    peak_ratio = abs(peak) / (second_peak + 1e-12)

    mean = float(np.mean(abs_corr))
    std = float(np.std(abs_corr))
    z_score = (abs(peak) - mean) / (std + 1e-12)

    return peak_ratio, z_score


def overlap_ratio(ref_len: int, target_len: int, lag_samples: float) -> float:
    """Fraction of the shorter clip that overlaps the longer clip at the given lag."""
    if lag_samples >= 0:
        overlap = max(0.0, min(ref_len - lag_samples, target_len))
    else:
        overlap = max(0.0, min(target_len + lag_samples, ref_len))
    shorter = min(ref_len, target_len)
    if shorter <= 0:
        return 0.0
    return float(overlap / shorter)


def compute_confidence(
    pearson: float,
    peak_ratio: float,
    z_score: float,
    overlap: float,
) -> Tuple[float, Dict[str, Any]]:
    """Combine metrics into a single confidence score and return diagnostics."""
    pearson_c = float(max(0.0, min(1.0, pearson)))
    # Normalize peak_ratio: 1.3 -> 0, 5 -> 1.
    pr_c = max(0.0, min(1.0, (peak_ratio - 1.3) / 3.7))
    # Normalize z_score: 3 -> 0, 10 -> 1.
    z_c = max(0.0, min(1.0, (z_score - 3.0) / 7.0))
    ov_c = float(max(0.0, min(1.0, overlap)))

    confidence = 0.35 * pearson_c + 0.25 * pr_c + 0.25 * z_c + 0.15 * ov_c
    return confidence, {
        "pearson": pearson_c,
        "peak_ratio": peak_ratio,
        "z_score": z_score,
        "overlap_ratio": ov_c,
        "combined": confidence,
    }


def is_match_accepted(
    confidence: float,
    peak_ratio: float,
    z_score: float,
    overlap: float,
    thresholds: Dict[str, float],
) -> bool:
    if confidence < thresholds.get("match_threshold", 0.45):
        return False
    if peak_ratio < thresholds.get("min_peak_ratio", 1.3):
        return False
    if z_score < thresholds.get("min_z_score", 3.0):
        return False
    if overlap < thresholds.get("min_overlap_ratio", 0.1):
        return False
    return True
