from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .algorithms.envelope_correlation import envelope_correlation
from .algorithms.gcc_phat import gcc_phat_pairwise
from .algorithms.spectral_correlation import mel_spectral_gcc_phat
from .confidence import compute_confidence, is_match_accepted, overlap_ratio, peak_metrics
from .decoder import analyze_clip, resolve_ffmpeg_path
from .drift import estimate_drift
from .multicam import build_weighted_graph, find_connected_components, optimize_offsets
from .plan import build_plan
from .preprocessing import preprocess
from .resampler import downsample_decimate
from .types import PairwiseResult, SyncDiagnostics, SyncResult, TimelineClip


def _prepare_samples(samples: np.ndarray, sample_rate: int, coarse_rate: int) -> Tuple[np.ndarray, np.ndarray]:
    """Return preprocessed full-rate and coarse-rate arrays."""
    x = preprocess(samples, target_rms=0.1, pre_emphasis_coeff=0.97)
    if sample_rate == coarse_rate:
        return x, x.copy()
    x_coarse = downsample_decimate(x, sample_rate, coarse_rate)
    return x, x_coarse


def _pairwise_sync(
    ref: TimelineClip,
    target: TimelineClip,
    settings,
    stage: str = "coarse_fine",
) -> PairwiseResult:
    from .types import SyncSettings, seconds_to_samples

    if isinstance(settings, dict):
        settings = SyncSettings(**settings)

    sr = settings.effective_sample_rate()
    coarse_sr = settings.effective_coarse_sample_rate()
    max_offset = settings.max_offset_seconds
    fine_search = settings.fine_search_seconds

    ref_full, ref_coarse = _prepare_samples(ref.samples, sr, coarse_sr)
    tgt_full, tgt_coarse = _prepare_samples(target.samples, sr, coarse_sr)

    start = time.perf_counter()
    coarse_offset = None
    coarse_conf = 0.0
    coarse_peak = 0.0
    method = "gcc_phat"

    # Stage A: coarse search using envelope/spectral if clips long.
    if stage in ("coarse_fine", "coarse_only") and len(ref_full) / sr > 2.0:
        env_res = envelope_correlation(ref_full, tgt_full, sr, max_offset_seconds=max_offset)
        mel_res = mel_spectral_gcc_phat(
            ref_full,
            tgt_full,
            sr,
            max_offset_seconds=max_offset,
            n_fft=1024,
            n_mels=40,
        )
        # Pick the better coarse result by Pearson confidence.
        if mel_res["correlation"] > env_res["correlation"]:
            coarse_offset = mel_res["offset_seconds"]
            coarse_conf = mel_res["correlation"]
            coarse_peak = mel_res["raw_peak"]
        else:
            coarse_offset = env_res["offset_seconds"]
            coarse_conf = env_res["correlation"]
            coarse_peak = env_res["raw_peak"]

    # Stage B: fine GCC-PHAT.
    fine_res = gcc_phat_pairwise(
        ref_full,
        tgt_full,
        sr,
        max_offset_seconds=max_offset,
        coarse_offset_seconds=coarse_offset,
        fine_search_seconds=fine_search,
    )

    # Refine candidate list for diagnostics.
    peak_value = fine_res["raw_peak"]
    best_idx = int(round(fine_res["lag_samples"]))
    # For peak ratio, build a correlation surface around the peak is hard.
    # Use a synthetic surface from GCC: search vector.
    # Recompute full GCC to compute metrics.
    try:
        from .algorithms.gcc_phat import gcc_phat
        full_res = gcc_phat(ref_full, tgt_full, sr, max_offset_seconds=max_offset)
        lag = int(round(full_res["lag_samples"]))
    except Exception:
        full_res = fine_res
        lag = 0

    # For diagnostics, use a brute-force window around estimated lag.
    n = max(len(ref_full), len(tgt_full))
    search_vals = np.zeros(2 * max_offset * sr + 1, dtype=np.float64) if False else None
    peak_ratio = 1.0
    z_score = 0.0

    # Better: compute Pearson correlation surface around the coarse/fine peak.
    from .algorithms.gcc_phat import pearson_at_lag
    half = max(1, min(max_offset * sr, 500))
    corr_vals = []
    for k in range(lag - half, lag + half + 1):
        corr_vals.append(abs(pearson_at_lag(ref_full, tgt_full, k)))
    corr_arr = np.array(corr_vals, dtype=np.float64)
    if corr_arr.size:
        peak_idx = int(np.argmax(corr_arr))
        peak_ratio, z_score = peak_metrics(corr_arr, peak_idx)

    overlap = overlap_ratio(len(ref_full), len(tgt_full), fine_res["lag_samples"])
    confidence, conf_diag = compute_confidence(fine_res["correlation"], peak_ratio, z_score, overlap)

    accepted = is_match_accepted(
        confidence,
        peak_ratio,
        z_score,
        overlap,
        {
            "match_threshold": settings.match_threshold,
            "min_peak_ratio": settings.min_peak_ratio,
            "min_z_score": settings.min_z_score,
            "min_overlap_ratio": settings.min_overlap_ratio,
        },
    )

    processing_time_ms = (time.perf_counter() - start) * 1000.0

    drift_est = None
    if settings.enable_drift and accepted and overlap > 0.2 and min(len(ref_full), len(tgt_full)) / sr > settings.drift_min_duration:
        drift_est = estimate_drift(
            ref_full,
            tgt_full,
            sr,
            fine_res["offset_seconds"],
            min_duration=settings.drift_min_duration,
            window_seconds=settings.drift_window_seconds,
            hop_seconds=settings.drift_hop_seconds,
        )

    return PairwiseResult(
        ref_index=ref.id,
        target_index=target.id,
        offset_seconds=fine_res["offset_seconds"],
        confidence=confidence,
        pearson=fine_res["correlation"],
        peak_ratio=peak_ratio,
        z_score=z_score,
        overlap_ratio=overlap,
        method=method,
        correlation_peak=fine_res["correlation"],
        raw_peak=fine_res["raw_peak"],
        overlap_seconds=overlap * min(len(ref_full), len(tgt_full)) / sr,
        processing_time_ms=processing_time_ms,
        drift_estimate=drift_est,
        diagnostics={
            "coarse_offset": coarse_offset,
            "coarse_confidence": coarse_conf,
            "fine_lag_samples": fine_res["lag_samples"],
            "fine_confidence": fine_res["correlation"],
            "raw_peak": fine_res["raw_peak"],
            "peak_ratio": peak_ratio,
            "z_score": z_score,
            "overlap_ratio": overlap,
            "confidence_breakdown": conf_diag,
            "accepted": accepted,
        },
    )


def _pairwise_results_for_group(
    clips: List[TimelineClip],
    settings,
) -> List[PairwiseResult]:
    results = []
    for i in range(len(clips)):
        for j in range(i + 1, len(clips)):
            try:
                r = _pairwise_sync(clips[i], clips[j], settings)
                results.append(r)
                if r.confidence >= settings.match_threshold:
                    # Add symmetric edge if match accepted.
                    sym = PairwiseResult(
                        ref_index=r.target_index,
                        target_index=r.ref_index,
                        offset_seconds=-r.offset_seconds,
                        confidence=r.confidence,
                        pearson=r.pearson,
                        peak_ratio=r.peak_ratio,
                        z_score=r.z_score,
                        overlap_ratio=r.overlap_ratio,
                        method=r.method,
                        correlation_peak=r.correlation_peak,
                        raw_peak=r.raw_peak,
                        overlap_seconds=r.overlap_seconds,
                        processing_time_ms=r.processing_time_ms,
                        drift_estimate=None,
                        diagnostics=r.diagnostics,
                    )
                    results.append(sym)
            except Exception:
                continue
    return results


def _group_clips(clips: List[TimelineClip], results: List[PairwiseResult]) -> Tuple[List[List[int]], List[int]]:
    by_id = {c.id: c for c in clips}
    indices = list(by_id.keys())
    n = len(indices)
    id_to_pos = {idx: i for i, idx in enumerate(indices)}
    adj = [[] for _ in range(n)]
    for r in results:
        if r.confidence >= 0.45:
            i = id_to_pos.get(r.ref_index)
            j = id_to_pos.get(r.target_index)
            if i is not None and j is not None:
                adj[i].append(j)

    visited = [False] * n
    groups = []
    for i in range(n):
        if visited[i]:
            continue
        comp = []
        stack = [i]
        visited[i] = True
        while stack:
            v = stack.pop()
            comp.append(indices[v])
            for u in adj[v]:
                if not visited[u]:
                    visited[u] = True
                    stack.append(u)
        groups.append(comp)

    # Singletons = orphans.
    groups, orphans = [g for g in groups if len(g) > 1], [g[0] for g in groups if len(g) == 1]
    return groups, orphans


def process_sync_request(request: dict) -> dict:
    start = time.perf_counter()
    clips_in = request.get("clips", [])
    settings_dict = request.get("settings", {})

    from .types import SyncSettings

    settings = SyncSettings.from_dict(settings_dict)
    ffmpeg_path = resolve_ffmpeg_path(request.get("ffmpegPath", settings.ffmpeg_path))

    # Analyze clips.
    analyzed = []
    for clip in clips_in:
        try:
            analyzed.append(analyze_clip(clip, ffmpeg_path, settings))
        except Exception as e:
            analyzed.append(TimelineClip(
                id=clip.get("id", 0),
                name=clip.get("name", ""),
                media_path=clip.get("mediaPath", ""),
                start_seconds=float(clip.get("startSeconds", 0) or 0),
                duration_seconds=float(clip.get("durationSeconds", 0) or 0),
                track_index=int(clip.get("trackIndex", -1)),
                clip_index=int(clip.get("clipIndex", -1)),
                is_audio=bool(clip.get("isAudio", False)),
            ))

    if len(analyzed) < 2:
        # single clip / no-op
        return {
            "success": True,
            "syncResults": [],
            "operations": [],
            "groups": [],
            "orphans": [c.id for c in analyzed],
            "diagnostics": {},
            "processingTimeMs": (time.perf_counter() - start) * 1000.0,
        }

    # Pairwise sync.
    pairwise = _pairwise_results_for_group(analyzed, settings)

    # Grouping.
    groups, orphans = _group_clips(analyzed, pairwise)

    # Global optimization per group.
    group_offsets: Dict[int, float] = {}
    for group in groups:
        comp_clips = [c for c in analyzed if c.id in group]
        comp_results = [r for r in pairwise if r.ref_index in group and r.target_index in group]
        if len(comp_clips) == 2:
            offsets = {comp_clips[0].id: 0.0}
            for c in comp_clips[1:]:
                for r in comp_results:
                    if r.target_index == c.id:
                        offsets[c.id] = r.offset_seconds
                        break
            group_offsets.update(offsets)
            continue

        _, edges = build_weighted_graph(comp_results)
        offsets, _ = optimize_offsets([c.id for c in comp_clips], edges, settings.effective_sample_rate())
        group_offsets.update(offsets)

    # Build operations.
    operations = build_plan(analyzed, group_offsets, groups, orphans, settings)

    # Sync results for UI.
    sync_results = []
    for r in pairwise:
        sync_results.append({
            "refId": r.ref_index,
            "targetId": r.target_index,
            "offsetSeconds": r.offset_seconds,
            "confidence": r.confidence,
            "method": r.method,
            "accepted": r.diagnostics.get("accepted", False),
        })

    diagnostics = {
        "clipCount": len(analyzed),
        "sampleRate": settings.effective_sample_rate(),
        "maxAnalyzeSeconds": settings.max_analyze_seconds,
        "maxOffsetSeconds": settings.max_offset_seconds,
        "pairwiseCount": len(pairwise),
        "groupCount": len(groups),
        "orphanCount": len(orphans),
        "groups": groups,
        "orphans": orphans,
    }

    return {
        "success": True,
        "syncResults": sync_results,
        "operations": operations,
        "groups": groups,
        "orphans": orphans,
        "diagnostics": diagnostics,
        "processingTimeMs": (time.perf_counter() - start) * 1000.0,
    }


def process_normalize_request(request: dict) -> dict:
    """Backward-compatible normalization handler. Gain is now computed during sync."""
    result = process_sync_request(request)
    if not result.get("success"):
        return result
    gained = sum(1 for op in result.get("operations", []) if op.get("gainDb", 0) != 0)
    result["gained"] = gained
    return result
