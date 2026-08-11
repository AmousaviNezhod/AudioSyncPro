#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Audio Sync Pro - Python bridge for Adobe CEP panel.

Usage:
    python sync_bridge.py <request.json> <response.json>

request.json format:
{
  "op": "sync" | "normalize",
  "clips": [
    {
      "id": int,
      "name": str,
      "mediaPath": str,
      "startSeconds": float,
      "durationSeconds": float,
      "trackIndex": int,
      "clipIndex": int,
      "isAudio": bool
    }
  ],
  "settings": {
    "ffmpegPath": "ffmpeg",
    "sampleRate": 16000,
    "sampleSeconds": 30,
    "normalizeAudio": true,
    "targetPeak": -1.0,
    "maxOffset": 10.0,
    "matchThreshold": 0.45,
    "placeOnTracks": true
  }
}

response.json format:
{
  "success": true,
  "data": {
    "operations": [...],
    "groups": [...]
  }
}
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


def get_bundle_ffmpeg_path():
    """Return the path to a bundled ffmpeg binary if present."""
    # When running from a PyInstaller build, sys.executable is the .exe file.
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


def next_power_of_two(n):
    if n < 1:
        return 1
    return 1 << (n - 1).bit_length()


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
    code, out, err = run_ffmpeg(ffmpeg_path, args, timeout=120)
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
    code, out, err = run_ffmpeg(ffmpeg_path, args, timeout=120)
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


def pearson_at_lag(ref, target, lag):
    """Pearson correlation of the overlapping window at the given (integer) lag."""
    start_ref = max(0, -lag)
    start_tgt = max(0, lag)
    end = min(len(ref), len(target) - lag)
    if end <= start_ref:
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


def cross_correlate(ref_samples, target_samples, sample_rate, max_offset_seconds):
    """Find the lag (target relative to ref) with the highest correlation."""
    if ref_samples is None or target_samples is None or len(ref_samples) == 0 or len(target_samples) == 0:
        raise RuntimeError("empty audio buffers")
    if not HAS_NUMPY:
        raise RuntimeError("numpy is required for audio sync; install it with: pip install numpy")

    ref_len = len(ref_samples)
    tgt_len = len(target_samples)

    ref = normalize_signal(ref_samples)
    tgt = normalize_signal(target_samples)

    max_lag = int((max_offset_seconds or 5.0) * sample_rate)
    hard_max = min(ref_len, tgt_len) - 1
    if max_lag > hard_max:
        max_lag = hard_max
    if max_lag < 1:
        max_lag = 1

    n = next_power_of_two(ref_len + tgt_len - 1)

    # rfft correlation: R[k] = sum_i ref[i] * target[i + k]
    A = np.fft.rfft(ref, n)
    B = np.fft.rfft(tgt, n)
    C = np.fft.irfft(A.conj() * B, n)

    # Search positive and negative lags.
    pos = C[: max_lag + 1]
    neg = C[n - max_lag :]
    best_pos = int(np.argmax(pos))
    best_neg = int(np.argmax(neg))
    if pos[best_pos] >= neg[best_neg]:
        best_i = best_pos
        raw_corr = float(pos[best_pos])
    else:
        best_i = n - max_lag + best_neg
        raw_corr = float(neg[best_neg])

    # Quadratic interpolation around peak.
    i0 = (best_i - 1 + n) % n
    i2 = (best_i + 1) % n
    y0 = float(C[i0])
    y1 = float(C[best_i])
    y2 = float(C[i2])
    denom = y0 - 2 * y1 + y2
    p = 0.0
    if abs(denom) > 1e-12:
        cand = 0.5 * (y0 - y2) / denom
        if math.isfinite(cand) and abs(cand) < 1.0:
            p = cand

    k = best_i if best_i <= max_lag else best_i - n
    k += p

    best_lag_int = best_i if best_i <= max_lag else best_i - n
    best_pearson = pearson_at_lag(ref_samples, target_samples, best_lag_int)
    if best_pearson < 0:
        best_pearson = 0.0

    return {
        "peakLagSamples": k,
        "peakValue": best_pearson,
        "offsetSeconds": k / sample_rate,
        "sampleRate": sample_rate,
    }


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
    results = []
    for clip in clips:
        results.append(analyze_clip(ffmpeg_path, clip, settings))
    return results


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
    total_pairs = n * (n - 1) // 2
    done = 0
    for i in range(n):
        for j in range(i + 1, n):
            corr = cross_correlate(
                results[i]["samples"],
                results[j]["samples"],
                sample_rate,
                max_offset,
            )
            done += 1
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
        # Pick reference clip with highest total confidence to other members.
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

    # Orphans: sequence end-to-end on one track after all sync groups.
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


def main():
    if len(sys.argv) < 3:
        log("usage: sync_bridge.py <request.json> <response.json>")
        sys.exit(1)

    request_path = sys.argv[1]
    response_path = sys.argv[2]

    try:
        with open(request_path, "r", encoding="utf-8") as f:
            request = json.load(f)
    except Exception as e:
        write_error(response_path, "Could not read request: " + str(e))
        sys.exit(1)

    clips = request.get("clips", [])
    settings = request.get("settings", {})
    op = request.get("op", "sync")

    try:
        if not HAS_NUMPY:
            raise RuntimeError("numpy is not installed. Install it with: pip install numpy")

        if op == "normalize":
            data = run_normalize(clips, settings)
        else:
            data = run_sync(clips, settings)

        response = {"success": True, "data": data}
        with open(response_path, "w", encoding="utf-8") as f:
            json.dump(response, f, ensure_ascii=False, indent=2)
        print("SYNC_DONE")
    except Exception as e:
        traceback.print_exc()
        write_error(response_path, str(e))
        sys.exit(1)


def write_error(response_path, message):
    try:
        with open(response_path, "w", encoding="utf-8") as f:
            json.dump({"success": False, "error": message}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    main()
