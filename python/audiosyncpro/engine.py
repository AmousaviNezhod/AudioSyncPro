from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .algorithms.envelope_correlation import envelope_correlation
from .algorithms.gcc_phat import gcc_phat, pearson_at_lag
from .algorithms.spectral_correlation import mel_spectral_gcc_phat
from .confidence import compute_confidence, is_match_accepted, overlap_ratio, peak_metrics
from .decoder import analyze_clip, extract_audio, resolve_ffmpeg_path
from .drift import estimate_drift
from .multicam import build_weighted_graph, find_connected_components, optimize_offsets
from .plan import build_plan
from .preprocessing import preprocess
from .resampler import downsample_decimate
from .types import PairwiseResult, SyncDiagnostics, SyncResult, TimelineClip


def _pairwise_sync(
    ref: TimelineClip,
    target: TimelineClip,
    settings,
    ffmpeg_path: str,
    stage: str = "coarse_fine",
) -> PairwiseResult:
    from .types import SyncSettings

    if isinstance(settings, dict):
        settings = SyncSettings(**settings)

    sr = settings.effective_sample_rate()
    coarse_sr = settings.effective_coarse_sample_rate()
    max_offset = settings.max_offset_seconds
    fine_search = settings.fine_search_seconds
    max_analyze = settings.max_analyze_seconds

    start = time.perf_counter()

    ref_c = ref.coarse_samples
    tgt_c = target.coarse_samples
    if ref_c is None or tgt_c is None or len(ref_c) == 0 or len(tgt_c) == 0:
        raise RuntimeError("missing coarse audio samples")

    ref_dur = ref.media_duration_seconds or (ref.media_info.duration_seconds if ref.media_info else ref.duration_seconds)
    tgt_dur = target.media_duration_seconds or (target.media_info.duration_seconds if target.media_info else target.duration_seconds)
    ref_in = ref.in_point_seconds
    tgt_in = target.in_point_seconds

    coarse_candidates = []
    # Stage A: coarse search on downsampled, full-duration audio.
    if stage in ("coarse_fine", "coarse_only") and len(ref_c) / coarse_sr > 2.0:
        ref_c_proc = preprocess(ref_c, target_rms=0.1, pre_emphasis_coeff=0.97)
        tgt_c_proc = preprocess(tgt_c, target_rms=0.1, pre_emphasis_coeff=0.97)

        # Primary: direct GCC-PHAT on the coarse signals (most accurate when it works).
        if len(ref_c) <= 4_000_000 and len(tgt_c) <= 4_000_000:
            try:
                gcc_coarse_res = gcc_phat(ref_c_proc, tgt_c_proc, coarse_sr, max_offset_seconds=max_offset)
                coarse_candidates.append((gcc_coarse_res["offset_seconds"], "gcc_phat", gcc_coarse_res["correlation"]))
            except Exception:
                pass

        try:
            env_res = envelope_correlation(ref_c_proc, tgt_c_proc, coarse_sr, max_offset_seconds=max_offset)
            coarse_candidates.append((env_res["offset_seconds"], "envelope", env_res["correlation"]))
        except Exception:
            env_res = None

        try:
            mel_res = mel_spectral_gcc_phat(
                ref_c_proc,
                tgt_c_proc,
                coarse_sr,
                max_offset_seconds=max_offset,
                n_fft=1024,
                n_mels=40,
            )
            coarse_candidates.append((mel_res["offset_seconds"], "mel", mel_res["correlation"]))
        except Exception:
            mel_res = None

    # Timeline positions are often a good coarse prior (e.g. time-of-day).
    timeline_offset = float(target.start_seconds - ref.start_seconds)
    if abs(timeline_offset) <= max_offset:
        coarse_candidates.append((timeline_offset, "timeline", 0.5))

    if not coarse_candidates:
        coarse_candidates = [(0.0, "gcc_phat", 0.0)]

    # Stage B: evaluate each coarse candidate by extracting the overlapping fine-rate
    # window and running GCC-PHAT. Pick the candidate with the strongest fine peak.
    fine_dur = max_analyze + 2.0 * fine_search
    fine_search_total = max(fine_search, 2.0 / coarse_sr if coarse_sr else 2.0)

    best_candidate = None
    best_score = -1.0
    chosen_coarse_offset = 0.0
    chosen_coarse_conf = 0.0
    chosen_method = "gcc_phat"
    chosen_fine_res = None
    chosen_ref_fine = None
    chosen_tgt_fine = None
    chosen_overlap_dur = 0.0

    for coarse_offset, cand_method, coarse_conf in coarse_candidates:
        try:
            ref_start = min(max(0.0, -coarse_offset) + ref_in, ref_dur)
            tgt_start = min(max(0.0, coarse_offset) + tgt_in, tgt_dur)
            ref_extract_dur = min(ref_dur - ref_start, fine_dur)
            tgt_extract_dur = min(tgt_dur - tgt_start, fine_dur)
            if ref_extract_dur < 0.5 or tgt_extract_dur < 0.5:
                ref_start = ref_in
                tgt_start = tgt_in
                ref_extract_dur = min(ref_dur - ref_start, fine_dur)
                tgt_extract_dur = min(tgt_dur - tgt_start, fine_dur)
            if ref_extract_dur < 0.1 or tgt_extract_dur < 0.1:
                continue

            overlap_dur = min(ref_extract_dur, tgt_extract_dur)
            timeout = max(300.0, overlap_dur * 10.0)
            ref_fine = extract_audio(ffmpeg_path, ref.media_path, sample_rate=sr, start_seconds=ref_start, duration_seconds=ref_extract_dur, mono=True, timeout=timeout)
            tgt_fine = extract_audio(ffmpeg_path, target.media_path, sample_rate=sr, start_seconds=tgt_start, duration_seconds=tgt_extract_dur, mono=True, timeout=timeout)
            ref_fine = preprocess(ref_fine, target_rms=0.1, pre_emphasis_coeff=0.97)
            tgt_fine = preprocess(tgt_fine, target_rms=0.1, pre_emphasis_coeff=0.97)

            fine_res = gcc_phat(ref_fine, tgt_fine, sr, max_offset_seconds=fine_search_total)
            score = fine_res["correlation"]
            if score > best_score + 1e-7:
                best_score = score
                best_candidate = coarse_offset
                chosen_coarse_offset = coarse_offset
                chosen_coarse_conf = coarse_conf
                chosen_method = f"coarse_{cand_method}_fine_gcc_phat"
                chosen_fine_res = fine_res
                chosen_ref_fine = ref_fine
                chosen_tgt_fine = tgt_fine
                chosen_overlap_dur = overlap_dur
        except Exception:
            continue

    if best_candidate is None or chosen_fine_res is None:
        raise RuntimeError("no valid coarse candidate produced a fine correlation")

    final_offset_s = chosen_coarse_offset + chosen_fine_res["offset_seconds"]

    # Confidence surface around the fine peak (local window only).
    fine_lag_int = int(round(chosen_fine_res["lag_samples"]))
    local_window_seconds = 0.05  # 50 ms around the peak is enough to judge sharpness
    half = min(max(50, int(local_window_seconds * sr)), len(chosen_ref_fine) // 2)
    corr_vals = [abs(pearson_at_lag(chosen_ref_fine, chosen_tgt_fine, k)) for k in range(fine_lag_int - half, fine_lag_int + half + 1)]
    corr_arr = np.array(corr_vals, dtype=np.float64)
    peak_ratio = 1.0
    z_score = 0.0
    if corr_arr.size:
        peak_idx = int(np.argmax(corr_arr))
        peak_ratio, z_score = peak_metrics(corr_arr, peak_idx)

    # Overlap based on full media durations and the final offset.
    if final_offset_s >= 0:
        overlap_s = max(0.0, min(ref_dur - final_offset_s, tgt_dur))
    else:
        overlap_s = max(0.0, min(tgt_dur + final_offset_s, ref_dur))
    shorter = min(ref_dur, tgt_dur)
    overlap = overlap_s / shorter if shorter > 0 else 0.0

    confidence, conf_diag = compute_confidence(chosen_fine_res["correlation"], peak_ratio, z_score, overlap)

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
        pearson=chosen_fine_res["correlation"],
    )

    processing_time_ms = (time.perf_counter() - start) * 1000.0

    drift_est = None
    if settings.enable_drift and accepted and overlap > 0.2 and chosen_overlap_dur > settings.drift_min_duration:
        try:
            drift_est = estimate_drift(
                chosen_ref_fine,
                chosen_tgt_fine,
                sr,
                chosen_fine_res["offset_seconds"],
                min_duration=settings.drift_min_duration,
                window_seconds=settings.drift_window_seconds,
                hop_seconds=settings.drift_hop_seconds,
            )
        except Exception:
            pass

    return PairwiseResult(
        ref_index=ref.id,
        target_index=target.id,
        offset_seconds=final_offset_s,
        confidence=confidence,
        pearson=chosen_fine_res["correlation"],
        peak_ratio=peak_ratio,
        z_score=z_score,
        overlap_ratio=overlap,
        method=chosen_method,
        correlation_peak=chosen_fine_res["correlation"],
        raw_peak=chosen_fine_res["raw_peak"],
        overlap_seconds=overlap_s,
        processing_time_ms=processing_time_ms,
        drift_estimate=drift_est,
        diagnostics={
            "coarse_offset": chosen_coarse_offset,
            "coarse_confidence": chosen_coarse_conf,
            "fine_lag_samples": chosen_fine_res["lag_samples"],
            "fine_confidence": chosen_fine_res["correlation"],
            "raw_peak": chosen_fine_res["raw_peak"],
            "peak_ratio": peak_ratio,
            "z_score": z_score,
            "overlap_ratio": overlap,
            "confidence_breakdown": conf_diag,
            "accepted": accepted,
            "evaluated_candidates": len(coarse_candidates),
        },
    )


def _pairwise_results_for_group(
    clips: List[TimelineClip],
    settings,
    ffmpeg_path: str,
) -> List[PairwiseResult]:
    results = []
    for i in range(len(clips)):
        for j in range(i + 1, len(clips)):
            try:
                r = _pairwise_sync(clips[i], clips[j], settings, ffmpeg_path)
                results.append(r)
                if r.diagnostics.get("accepted", False) and r.confidence >= settings.match_threshold:
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


def _group_clips(clips: List[TimelineClip], results: List[PairwiseResult], threshold: float = 0.45) -> Tuple[List[List[int]], List[int]]:
    by_id = {c.id: c for c in clips}
    indices = list(by_id.keys())
    n = len(indices)
    id_to_pos = {idx: i for i, idx in enumerate(indices)}
    adj = [[] for _ in range(n)]
    for r in results:
        if r.confidence >= threshold and r.diagnostics.get("accepted", False):
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
    clip_errors = []
    for clip in clips_in:
        try:
            analyzed.append(analyze_clip(clip, ffmpeg_path, settings))
        except Exception as e:
            import traceback as _tb
            clip_errors.append(f"{clip.get('name', clip.get('id'))}: {e}\n{_tb.format_exc()}")
            analyzed.append(TimelineClip(
                id=clip.get("id", 0),
                name=clip.get("name", ""),
                media_path=clip.get("mediaPath", ""),
                start_seconds=float(clip.get("startSeconds", 0) or 0),
                duration_seconds=float(clip.get("durationSeconds", 0) or 0),
                track_index=int(clip.get("trackIndex", -1)),
                clip_index=int(clip.get("clipIndex", -1)),
                is_audio=bool(clip.get("isAudio", False)),
                coarse_samples=None,
                coarse_sample_rate=0,
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
    pairwise = _pairwise_results_for_group(analyzed, settings, ffmpeg_path)

    # Grouping.
    groups, orphans = _group_clips(analyzed, pairwise, threshold=settings.match_threshold)

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

    # Build operations only when at least one sync group was found.
    operations = build_plan(analyzed, group_offsets, groups, orphans, settings) if groups else []

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
            "pearson": r.pearson,
            "peakRatio": r.peak_ratio,
            "zScore": r.z_score,
            "overlapRatio": r.overlap_ratio,
        })

    diagnostics = {
        "clipCount": len(analyzed),
        "sampleRate": settings.effective_sample_rate(),
        "maxAnalyzeSeconds": settings.max_analyze_seconds,
        "maxOffsetSeconds": settings.max_offset_seconds,
        "ffmpegPath": ffmpeg_path,
        "clipErrors": clip_errors,
        "coarseSamplesPresent": [bool(c.coarse_samples is not None and len(c.coarse_samples) > 0) for c in analyzed],
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
    """Normalization-only handler: compute per-clip gain and return gain operations only."""
    start = time.perf_counter()
    clips_in = request.get("clips", [])
    settings_dict = request.get("settings", {})

    from .types import SyncSettings

    # Force normalize mode, disable sync-related moves.
    settings_dict = dict(settings_dict)
    settings_dict["normalizeAudio"] = True
    settings_dict["placeOnTracks"] = False
    settings = SyncSettings.from_dict(settings_dict)
    ffmpeg_path = resolve_ffmpeg_path(request.get("ffmpegPath", settings.ffmpeg_path))

    operations = []
    errors = []
    for clip in clips_in:
        try:
            analyzed = analyze_clip(clip, ffmpeg_path, settings)
        except Exception as e:
            errors.append(f"{clip.get('name', clip.get('id'))}: {e}")
            continue
        if analyzed.gain_db == 0.0:
            continue
        operations.append({
            "type": "gain",
            "id": analyzed.id,
            "name": analyzed.name,
            "gainDb": analyzed.gain_db,
        })

    return {
        "success": True,
        "operations": operations,
        "gained": len(operations),
        "errors": errors,
        "processingTimeMs": (time.perf_counter() - start) * 1000.0,
    }
