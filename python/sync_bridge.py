#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Audio Sync Pro - Python bridge for Adobe CEP panel.

Modes:
  CLI:
    python sync_bridge.py <request.json> <response.json>

  Server (stdio, one JSON object per line):
    python sync_bridge.py --server

request.json format:
{
  "op": "sync" | "normalize",
  "clips": [...],
  "settings": {...},
  "request_id": "optional"
}

settings fields:
  ffmpegPath       : path to ffmpeg or empty for bundled/PATH
  sampleRate       : 8000 | 16000 | 22050 | 44100
  sampleSeconds    : max seconds to analyze per clip
  normalizeAudio   : bool
  targetPeak       : target dBFS for normalization
  maxOffset        : max sync offset to search in seconds
  matchThreshold   : 0..1 correlation threshold for grouping
  placeOnTracks    : bool
"""

import collections
import json
import math
import os
import re
import struct
import subprocess
import sys
import traceback

try:
    import numpy as np
    HAS_NUMPY = True
except Exception:
    HAS_NUMPY = False


def log(msg):
    """Mirror the same stderr/stdout logging so JS can capture errors."""
    print(msg, file=sys.stderr)
    sys.stderr.flush()


def get_bundle_ffmpeg_path():
    """Return the path to a bundled ffmpeg binary if present."""
    base_candidates = []
    if getattr(sys, "frozen", False):
        base_candidates.append(os.path.dirname(sys.executable))
    if __file__:
        base_candidates.append(os.path.dirname(os.path.abspath(__file__)))
    for base in base_candidates:
        candidate = os.path.join(base, "bin", "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


def resolve_ffmpeg_path(settings):
    """Pick the ffmpeg binary: explicit setting, bundled binary, or PATH."""
    explicit = (settings or {}).get("ffmpegPath", "")
    if explicit and explicit.strip():
        return explicit.strip()
    bundled = get_bundle_ffmpeg_path()
    if bundled:
        return bundled
    return "ffmpeg"


def run_ffmpeg(ffmpeg_path, args, capture_stdout=True, timeout=None):
    """Run ffmpeg with the provided args. Returns stdout or stderr output."""
    cmd = [ffmpeg_path] + args
    try:
        if capture_stdout:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
            )
        else:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
            )
        return result.returncode, result.stdout, result.stderr if capture_stdout else result.stdout
    except Exception as e:
        return -1, b"", str(e).encode("utf-8")


def extract_audio(ffmpeg_path, media_path, sample_rate, sample_seconds):
    """Extract mono f32le PCM samples."""
    args = [
        "-hide_banner",
        "-loglevel", "error",
        "-i", media_path,
        "-ar", str(sample_rate),
        "-ac", "1",
        "-t", str(sample_seconds),
        "-f", "f32le",
        "-c:a", "pcm_f32le",
        "pipe:1",
    ]
    code, out, err = run_ffmpeg(ffmpeg_path, args, timeout=180)
    if code != 0:
        raise RuntimeError("ffmpeg extract failed: " + (err or b"").decode("utf-8", "ignore")[:500])
    if not out or len(out) < 4:
        raise RuntimeError("ffmpeg returned empty audio for " + media_path)
    if HAS_NUMPY:
        samples = np.frombuffer(out, dtype=np.float32)
        if not np.isfinite(samples).all():
            raise RuntimeError("audio contains non-finite samples: " + media_path)
        return samples
    else:
        count = len(out) // 4
        return struct.unpack("%sf" % count, out[: count * 4])


def detect_volume(ffmpeg_path, media_path, sample_seconds=None):
    """Parse ffmpeg volumedetect output."""
    args = ["-hide_banner", "-loglevel", "info", "-i", media_path]
    if sample_seconds:
        args += ["-t", str(sample_seconds)]
    args += ["-af", "volumedetect", "-f", "null", "-"]
    code, out, err = run_ffmpeg(ffmpeg_path, args, timeout=180)
    text = (err or b"").decode("utf-8", "ignore")
    max_match = re.search(r"max_volume:\s*([-+]?\d+\.?\d*)\s*dB", text)
    mean_match = re.search(r"mean_volume:\s*([-+]?\d+\.?\d*)\s*dB", text)
    return {
        "maxVolume": float(max_match.group(1)) if max_match else float("-inf"),
        "meanVolume": float(mean_match.group(1)) if mean_match else float("-inf"),
    }


def gain_for_normalization(max_volume_db, target_db):
    if max_volume_db == float("-inf") or math.isnan(max_volume_db):
        return 0.0
    return target_db - max_volume_db


def normalize_signal(samples):
    """Return a zero-mean, unit-std copy."""
    if HAS_NUMPY:
        arr = np.array(samples, dtype=np.float64)
        mean = arr.mean()
        std = arr.std()
        if std < 1e-12:
            return arr - mean
        return (arr - mean) / std
    else:
        n = len(samples)
        mean = sum(samples) / n
        var = sum((x - mean) ** 2 for x in samples) / n
        std = math.sqrt(max(var, 1e-12))
        return [(x - mean) / std for x in samples]


def pre_emphasis(samples, coeff=0.95):
    """Boost high frequencies (speech/music clarity)."""
    if not HAS_NUMPY or len(samples) < 2:
        return samples
    out = np.empty_like(samples, dtype=np.float64)
    out[0] = float(samples[0])
    out[1:] = samples[1:] - coeff * samples[:-1]
    return out


def hann_window(n):
    """Create a Hann window of length n."""
    if not HAS_NUMPY:
        return [1.0] * n
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / (n - 1))


def hz_to_mel(hz):
    """Convert Hertz to mel scale (HTK formula)."""
    return 2595.0 * math.log10(1.0 + hz / 700.0)


def mel_to_hz(mel):
    """Convert mel scale to Hertz (HTK formula)."""
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def mel_filterbank(sr, n_fft, n_mels=40, f_min=80.0, f_max=None):
    """Return a (n_fft//2 + 1, n_mels) triangular mel filterbank using only numpy."""
    if f_max is None:
        f_max = sr / 2.0
    n_freq = n_fft // 2 + 1
    fft_freqs = np.linspace(0.0, sr / 2.0, n_freq)
    mel_min = hz_to_mel(f_min)
    mel_max = hz_to_mel(f_max)
    mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
    hz_points = mel_to_hz(mel_points)
    weights = np.zeros((n_freq, n_mels), dtype=np.float64)
    for i in range(n_mels):
        left = hz_points[i]
        center = hz_points[i + 1]
        right = hz_points[i + 2]
        for j in range(n_freq):
            f = fft_freqs[j]
            if f <= left or f >= right:
                continue
            if f <= center:
                if center == left:
                    continue
                weights[j, i] = (f - left) / (center - left)
            else:
                if right == center:
                    continue
                weights[j, i] = (right - f) / (right - center)
        s = weights[:, i].sum()
        if s > 0:
            weights[:, i] /= s
    return weights


def stft(signal, n_fft=512, hop_length=None, win_length=None):
    """Compute a simple magnitude STFT using numpy only."""
    if not HAS_NUMPY:
        raise RuntimeError("numpy is required for STFT")
    if win_length is None:
        win_length = n_fft
    if hop_length is None:
        hop_length = win_length // 4
    signal = np.asarray(signal, dtype=np.float64)
    if win_length > len(signal):
        win_length = len(signal)
        hop_length = max(1, win_length // 4)
    window = hann_window(win_length)
    n_frames = max(1, 1 + (len(signal) - win_length) // hop_length)
    frames = np.lib.stride_tricks.as_strided(
        signal,
        shape=(win_length, n_frames),
        strides=(signal.strides[0], signal.strides[0] * hop_length),
    )
    windowed = frames * window[:, np.newaxis]
    spec = np.fft.rfft(windowed, n=n_fft, axis=0)
    return np.abs(spec)


def pearson_at_lag(ref, target, lag):
    """Pearson correlation of the overlapping window at the given lag."""
    start_ref = max(0, -lag)
    start_tgt = max(0, lag)
    end = min(len(ref), len(target) - lag)
    if end <= start_ref or end <= start_tgt:
        return 0.0
    count = end - start_ref
    if count <= 1:
        return 0.0
    if HAS_NUMPY:
        r = ref[start_ref:end]
        t = target[start_tgt:start_tgt + count]
        rm = r - r.mean()
        tm = t - t.mean()
        den = math.sqrt((rm * rm).sum() * (tm * tm).sum())
        if den == 0:
            return 0.0
        return float((rm * tm).sum() / den)
    else:
        s_ref = sum(ref[start_ref + i] for i in range(count))
        s_tgt = sum(target[start_tgt + i] for i in range(count))
        m_ref = s_ref / count
        m_tgt = s_tgt / count
        num = 0.0
        d_ref = 0.0
        d_tgt = 0.0
        for i in range(count):
            a = ref[start_ref + i] - m_ref
            b = target[start_tgt + i] - m_tgt
            num += a * b
            d_ref += a * a
            d_tgt += b * b
        if d_ref == 0 or d_tgt == 0:
            return 0.0
        return num / math.sqrt(d_ref * d_tgt)


def next_power_of_two(n):
    if n < 1:
        return 1
    return 1 << (n - 1).bit_length()


def gcc_phat(ref_samples, target_samples, sample_rate, max_offset_seconds):
    """
    Generalized Cross-Correlation with Phase Transform (GCC-PHAT).
    More robust to noise/reverb than plain cross-correlation.
    Returns offset in seconds and a confidence score in [0,1].
    """
    if not HAS_NUMPY:
        raise RuntimeError("numpy is required for audio sync")
    if ref_samples is None or target_samples is None or len(ref_samples) == 0 or len(target_samples) == 0:
        raise RuntimeError("empty audio buffers")

    ref_len = len(ref_samples)
    tgt_len = len(target_samples)

    # Pre-emphasis + normalization + windowing reduces edge effects and boosts transients.
    ref = normalize_signal(pre_emphasis(ref_samples))
    tgt = normalize_signal(pre_emphasis(target_samples))

    win = hann_window(ref_len)
    ref = ref * win
    win = hann_window(tgt_len)
    tgt = tgt * win

    max_lag = int((max_offset_seconds or 5.0) * sample_rate)
    hard_max = min(ref_len, tgt_len) - 1
    if max_lag > hard_max:
        max_lag = hard_max
    if max_lag < 1:
        max_lag = 1

    n = next_power_of_two(ref_len + tgt_len - 1)
    A = np.fft.rfft(ref, n)
    B = np.fft.rfft(tgt, n)
    cross = A.conj() * B
    eps = 1e-12
    gcc = np.fft.irfft(cross / (np.abs(cross) + eps), n)

    # Build search vector: negative lags last, then positive lags.
    search = np.concatenate((gcc[n - max_lag:], gcc[:max_lag + 1]))
    best_idx = int(np.argmax(np.abs(search)))
    raw_peak = float(search[best_idx])

    # Map back to signed lag (positive = target is delayed relative to ref).
    if best_idx < max_lag:
        best_lag = best_idx - max_lag
    else:
        best_lag = best_idx - max_lag

    # Quadratic interpolation of the peak for sub-sample accuracy.
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

    # Confidence: Pearson correlation at the integer lag, clamped to [0,1].
    best_lag_int = int(round(best_lag))
    confidence = pearson_at_lag(ref_samples, target_samples, best_lag_int)
    if math.isnan(confidence):
        confidence = 0.0
    confidence = max(0.0, min(1.0, abs(confidence)))

    # If the GCC-PHAT peak is negative, flip confidence sign handling is already absolute.
    # Use a small penalty if GCC peak is weak compared to the second peak? Keep simple.
    return {
        "peakLagSamples": k,
        "peakValue": confidence,
        "offsetSeconds": k / sample_rate,
        "sampleRate": sample_rate,
        "rawPeak": raw_peak,
    }


def gcc_phat_spectral(ref_samples, target_samples, sample_rate, max_offset_seconds):
    """
    Mel-spectrogram cross-correlation with phase transform.
    More robust against noise, reverb and EQ differences than raw GCC-PHAT.
    """
    if not HAS_NUMPY:
        raise RuntimeError("numpy is required for audio sync")
    if ref_samples is None or target_samples is None or len(ref_samples) == 0 or len(target_samples) == 0:
        raise RuntimeError("empty audio buffers")

    ref = np.asarray(ref_samples, dtype=np.float64)
    tgt = np.asarray(target_samples, dtype=np.float64)

    # Adaptive FFT size by sample rate.
    n_fft = 512 if sample_rate < 16000 else 1024
    win_length = n_fft
    hop_length = win_length // 4

    spec1 = stft(ref, n_fft=n_fft, hop_length=hop_length, win_length=win_length)
    spec2 = stft(tgt, n_fft=n_fft, hop_length=hop_length, win_length=win_length)

    n_mels = 40
    f_min = 80.0
    f_max = min(sample_rate / 2.0, 8000.0)
    mel_basis = mel_filterbank(sample_rate, n_fft, n_mels=n_mels, f_min=f_min, f_max=f_max)

    eps = 1e-10
    # Mel power spectrograms, shape (n_mels, n_frames).
    mel1 = np.log10(np.dot(spec1.T, mel_basis).T + eps)
    mel2 = np.log10(np.dot(spec2.T, mel_basis).T + eps)

    # Standardize each mel band across time.
    def standardize(a):
        m = a.mean(axis=1, keepdims=True)
        s = a.std(axis=1, keepdims=True)
        s[s < 1e-12] = 1.0
        return (a - m) / s

    mel1 = standardize(mel1)
    mel2 = standardize(mel2)

    n1 = mel1.shape[1]
    n2 = mel2.shape[1]

    # Cross-correlate each mel band across frames and sum coherently.
    frame_rate = float(sample_rate) / hop_length
    max_lag_frames = int((max_offset_seconds or 5.0) * frame_rate)
    hard_max_frames = min(n1, n2) - 1
    if max_lag_frames > hard_max_frames:
        max_lag_frames = hard_max_frames
    if max_lag_frames < 1:
        max_lag_frames = 1

    n = next_power_of_two(n1 + n2 - 1)
    A = np.fft.rfft(mel1, n=n, axis=1)
    B = np.fft.rfft(mel2, n=n, axis=1)
    cross = (A.conj() * B).sum(axis=0)
    gcc = np.fft.irfft(cross / (np.abs(cross) + eps), n=n)

    search = np.concatenate((gcc[n - max_lag_frames:], gcc[:max_lag_frames + 1]))
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
    confidence = pearson_at_lag(ref_samples, target_samples, best_lag_int)
    if math.isnan(confidence):
        confidence = 0.0
    confidence = max(0.0, min(1.0, abs(confidence)))

    return {
        "peakLagSamples": lag_samples,
        "peakValue": confidence,
        "offsetSeconds": lag_samples / sample_rate,
        "sampleRate": sample_rate,
        "rawPeak": raw_peak,
    }


def compare_pair(ref_samples, target_samples, sample_rate, max_offset_seconds):
    """Run both raw GCC-PHAT and a mel-spectral GCC-PHAT, return the best candidate."""
    raw = gcc_phat(ref_samples, target_samples, sample_rate, max_offset_seconds)
    spectral = gcc_phat_spectral(ref_samples, target_samples, sample_rate, max_offset_seconds)

    # Evaluate Pearson confidence at the spectral offset too, for a fair comparison.
    spec_lag_int = int(round(float(spectral["peakLagSamples"])))
    spec_conf = pearson_at_lag(ref_samples, target_samples, spec_lag_int)
    if math.isnan(spec_conf):
        spec_conf = 0.0
    spec_conf = max(0.0, min(1.0, abs(spec_conf)))
    spectral["peakValue"] = float(spec_conf)

    # If the two methods agree within ~20 ms, average their offsets and boost confidence.
    agreement_threshold = max(1.0 / sample_rate, 0.02)  # 20 ms or 1 sample
    diff = abs(raw["offsetSeconds"] - spectral["offsetSeconds"])
    if diff < agreement_threshold:
        raw["peakValue"] = max(raw["peakValue"], spectral["peakValue"])
        return raw

    # Otherwise pick the candidate with the highest confidence.
    if spectral["peakValue"] > raw["peakValue"]:
        return spectral
    return raw


def cross_correlate(ref_samples, target_samples, sample_rate, max_offset_seconds):
    """Public alias that picks the stronger of raw and spectral GCC-PHAT."""
    return compare_pair(ref_samples, target_samples, sample_rate, max_offset_seconds)


def analyze_clip(ffmpeg_path, clip, settings):
    """Extract waveform and (optionally) compute gain."""
    sample_rate = int(settings.get("sampleRate", 16000))
    sample_seconds = float(settings.get("sampleSeconds", 30))
    samples = extract_audio(ffmpeg_path, clip["mediaPath"], sample_rate, sample_seconds)

    gain_db = 0.0
    max_volume = None
    mean_volume = None
    if settings.get("normalizeAudio"):
        vol = detect_volume(ffmpeg_path, clip["mediaPath"], sample_seconds)
        max_volume = vol["maxVolume"]
        mean_volume = vol["meanVolume"]
        gain_db = gain_for_normalization(max_volume, float(settings.get("targetPeak", -1.0)))

    return {
        "id": clip["id"],
        "name": clip["name"],
        "mediaPath": clip["mediaPath"],
        "startSeconds": clip.get("startSeconds", 0),
        "durationSeconds": clip.get("durationSeconds", 0),
        "trackIndex": clip.get("trackIndex", -1),
        "clipIndex": clip.get("clipIndex", -1),
        "isAudio": clip.get("isAudio", False),
        "samples": samples,
        "gainDb": gain_db,
        "maxVolume": max_volume,
        "meanVolume": mean_volume,
    }


def analyze_clips(ffmpeg_path, clips, settings):
    """Analyze all selected clips and return result objects."""
    return [analyze_clip(ffmpeg_path, clip, settings) for clip in clips]


def find_groups(results, settings):
    """Connected-components grouping by pairwise cross-correlation."""
    n = len(results)
    if n == 0:
        return {"groups": [], "orphans": []}
    if n == 1:
        return {"groups": [], "orphans": [0]}

    sample_rate = int(settings.get("sampleRate", 16000))
    max_offset = float(settings.get("maxOffset", 10.0))
    threshold = float(settings.get("matchThreshold", 0.45))

    corr_matrix = [[None] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            corr = cross_correlate(
                results[i]["samples"],
                results[j]["samples"],
                sample_rate,
                max_offset,
            )
            conf = max(0.0, min(1.0, corr["peakValue"]))
            offset = corr["offsetSeconds"]
            corr_matrix[i][j] = {"confidence": conf, "offset": offset}
            corr_matrix[j][i] = {"confidence": conf, "offset": -offset}

    adj = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if corr_matrix[i][j]["confidence"] >= threshold:
                adj[i].append(j)
                adj[j].append(i)

    visited = [False] * n
    components = []
    for i in range(n):
        if visited[i]:
            continue
        comp = []
        stack = [i]
        visited[i] = True
        while stack:
            v = stack.pop()
            comp.append(v)
            for u in adj[v]:
                if not visited[u]:
                    visited[u] = True
                    stack.append(u)
        components.append(comp)

    groups = []
    orphans = []
    for comp in components:
        if len(comp) == 1:
            orphans.append(comp[0])
            continue
        best_ref = comp[0]
        best_score = -float("inf")
        for v in comp:
            score = 0.0
            for u in comp:
                if v == u:
                    continue
                score += corr_matrix[v][u]["confidence"]
            if score > best_score:
                best_score = score
                best_ref = v

        members = []
        for idx in comp:
            if idx == best_ref:
                members.append({
                    "index": idx,
                    "offsetSeconds": 0.0,
                    "confidence": 1.0,
                    "gainDb": results[idx].get("gainDb", 0.0),
                })
            else:
                edge = corr_matrix[best_ref][idx]
                offset = edge["offset"]
                conf = edge["confidence"]
                if conf < threshold:
                    path_offset = get_path_offset(corr_matrix, adj, best_ref, idx, threshold)
                    if path_offset is not None:
                        offset = path_offset
                members.append({
                    "index": idx,
                    "offsetSeconds": offset,
                    "confidence": conf,
                    "gainDb": results[idx].get("gainDb", 0.0),
                })
        groups.append({"refIndex": best_ref, "members": members})

    return {"groups": groups, "orphans": orphans, "corr_matrix": corr_matrix, "adj": adj}


def get_path_offset(corr_matrix, adj, start, end, threshold):
    """BFS to sum signed offsets along a high-confidence path."""
    n = len(adj)
    parent = [-1] * n
    visited = [False] * n
    queue = collections.deque([start])
    visited[start] = True
    while queue:
        v = queue.popleft()
        for u in adj[v]:
            if not visited[u]:
                visited[u] = True
                parent[u] = v
                if u == end:
                    total = 0.0
                    cur = end
                    while cur != start:
                        p = parent[cur]
                        total += corr_matrix[p][cur]["offset"]
                        cur = p
                    return total
                queue.append(u)
    return None


def build_plan(results, groups_obj, settings):
    """Create move/gain operations for Premiere Pro host."""
    place_on_tracks = settings.get("placeOnTracks", True)
    operations = []
    next_track = 0
    max_group_end = 0.0

    for group in groups_obj["groups"]:
        ref = results[group["refIndex"]]
        ref_start = ref.get("startSeconds", 0)

        aligned = []
        for mem in group["members"]:
            aligned.append({
                "index": mem["index"],
                "start": ref_start - mem["offsetSeconds"],
                "member": mem,
            })

        min_start = min(a["start"] for a in aligned)
        shift = 0.0 if min_start >= 0 else -min_start

        group_end = 0.0
        for item in aligned:
            idx = item["index"]
            new_start = item["start"] + shift
            if new_start < 0:
                new_start = 0.0
            item["newStart"] = new_start
            dur = results[idx].get("durationSeconds", 0)
            end = new_start + dur
            if end > group_end:
                group_end = end

            if place_on_tracks:
                new_track = next_track
                next_track += 1
            else:
                new_track = results[idx].get("trackIndex", 0)

            operations.append({
                "type": "move",
                "id": results[idx]["id"],
                "name": results[idx]["name"],
                "trackIndex": results[idx].get("trackIndex", 0),
                "clipIndex": results[idx].get("clipIndex", 0),
                "isAudio": results[idx].get("isAudio", False),
                "mediaPath": results[idx]["mediaPath"],
                "newStartSeconds": new_start,
                "newTrackIndex": new_track,
                "gainDb": settings.get("normalizeAudio") and item["member"].get("gainDb", 0.0) or 0.0,
            })

        if group_end > max_group_end:
            max_group_end = group_end

    leftover_track = next_track
    cursor = max_group_end

    orphan_items = []
    for idx in groups_obj["orphans"]:
        orphan_items.append({
            "index": idx,
            "start": results[idx].get("startSeconds", 0),
        })
    orphan_items.sort(key=lambda x: x["start"])

    for item in orphan_items:
        idx = item["index"]
        dur = results[idx].get("durationSeconds", 0)
        new_track = leftover_track if place_on_tracks else results[idx].get("trackIndex", 0)
        operations.append({
            "type": "move",
            "id": results[idx]["id"],
            "name": results[idx]["name"],
            "trackIndex": results[idx].get("trackIndex", 0),
            "clipIndex": results[idx].get("clipIndex", 0),
            "isAudio": results[idx].get("isAudio", False),
            "mediaPath": results[idx]["mediaPath"],
            "newStartSeconds": cursor,
            "newTrackIndex": new_track,
            "gainDb": settings.get("normalizeAudio") and results[idx].get("gainDb", 0.0) or 0.0,
        })
        cursor += dur

    return {"operations": operations, "groups": groups_obj.get("groups", []), "orphans": groups_obj.get("orphans", [])}


def run_normalize(clips, settings):
    """Analyze clips and return gain operations only."""
    ffmpeg_path = resolve_ffmpeg_path(settings)
    norm_settings = dict(settings)
    norm_settings["normalizeAudio"] = True
    results = analyze_clips(ffmpeg_path, clips, norm_settings)
    operations = []
    for r in results:
        if r.get("gainDb") and r["gainDb"] != 0:
            operations.append({
                "type": "gain",
                "id": r["id"],
                "name": r["name"],
                "trackIndex": r.get("trackIndex", 0),
                "clipIndex": r.get("clipIndex", 0),
                "isAudio": r.get("isAudio", False),
                "mediaPath": r["mediaPath"],
                "gainDb": r["gainDb"],
            })
    return {"operations": operations, "groups": [], "orphans": []}


def run_sync(clips, settings):
    ffmpeg_path = resolve_ffmpeg_path(settings)
    results = analyze_clips(ffmpeg_path, clips, settings)
    groups_obj = find_groups(results, settings)
    return build_plan(results, groups_obj, settings)


def process_request(request):
    """Process one request and return the response dict."""
    clips = request.get("clips", [])
    settings = request.get("settings", {})
    op = request.get("op", "sync")

    if not HAS_NUMPY:
        raise RuntimeError("numpy is not installed. Install it with: pip install numpy")

    if op == "normalize":
        data = run_normalize(clips, settings)
    else:
        data = run_sync(clips, settings)
    return {"success": True, "data": data}


def run_server():
    """Read JSON requests from stdin, write JSON responses to stdout, one per line."""
    log("server started")
    stdin = sys.stdin if sys.stdin else open(sys.__stdin__.fileno(), "r", encoding="utf-8")
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                req_id = request.get("request_id")
                data = process_request(request)
                response = dict(data)
                if req_id is not None:
                    response["request_id"] = req_id
            except Exception as e:
                traceback.print_exc()
                response = {"success": False, "error": str(e)}
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    except Exception as e:
        log("server error: " + str(e))
    log("server exiting")


def run_cli():
    if len(sys.argv) < 3:
        log("usage: sync_bridge.py <request.json> <response.json>")
        log("       sync_bridge.py --server")
        sys.exit(1)

    request_path = sys.argv[1]
    response_path = sys.argv[2]

    try:
        with open(request_path, "r", encoding="utf-8") as f:
            request = json.load(f)
    except Exception as e:
        write_error(response_path, "Could not read request: " + str(e))
        sys.exit(1)

    try:
        response = process_request(request)
    except Exception as e:
        traceback.print_exc()
        write_error(response_path, str(e))
        sys.exit(1)

    try:
        with open(response_path, "w", encoding="utf-8") as f:
            json.dump(response, f, ensure_ascii=False, indent=2)
        print("SYNC_DONE")
    except Exception as e:
        traceback.print_exc()
        write_error(response_path, "Could not write response: " + str(e))
        sys.exit(1)


def write_error(response_path, message):
    try:
        with open(response_path, "w", encoding="utf-8") as f:
            json.dump({"success": False, "error": message}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--server":
        run_server()
    else:
        run_cli()
