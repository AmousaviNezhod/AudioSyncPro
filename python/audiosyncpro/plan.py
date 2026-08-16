from __future__ import annotations

from typing import Any, Dict, List

from .types import TimelineClip


def build_plan(
    results: List[TimelineClip],
    offsets: Dict[int, float],
    groups: List[List[int]],
    orphans: List[int],
    settings,
) -> List[Dict[str, Any]]:
    """Convert global offsets into move/gain operations for the ExtendScript host."""
    if isinstance(settings, dict):
        place_on_tracks = bool(settings.get("placeOnTracks", True))
        normalize_audio = bool(settings.get("normalizeAudio", False))
    else:
        place_on_tracks = settings.place_on_tracks
        normalize_audio = settings.normalize_audio

    operations = []
    next_track = 0
    max_group_end = 0.0

    # Map clip id to data.
    by_id = {r.id: r for r in results}

    for group in groups:
        if not group:
            continue
        aligned = []
        for idx in group:
            clip = by_id.get(idx)
            if not clip:
                continue
            aligned.append({"clip": clip, "start": clip.start_seconds - offsets.get(idx, 0.0)})
        if not aligned:
            continue

        min_start = min(a["start"] for a in aligned)
        shift = 0.0 if min_start >= 0 else -min_start

        group_end = 0.0
        for item in aligned:
            clip = item["clip"]
            new_start = item["start"] + shift
            if new_start < 0:
                new_start = 0.0
            dur = clip.duration_seconds if clip.duration_seconds > 0 else 0.0
            end = new_start + dur
            if end > group_end:
                group_end = end

            if place_on_tracks:
                new_track = next_track
                next_track += 1
            else:
                new_track = clip.track_index

            gain_db = 0.0
            if normalize_audio and clip.gain_db:
                gain_db = clip.gain_db

            op = {
                "type": "move",
                "id": clip.id,
                "name": clip.name,
                "trackIndex": clip.track_index,
                "clipIndex": clip.clip_index,
                "isAudio": clip.is_audio,
                "mediaPath": clip.media_path,
                "newStartSeconds": new_start,
                "newTrackIndex": new_track,
                "gainDb": gain_db,
            }
            if place_on_tracks:
                op["newAudioTrackIndex"] = new_track
            operations.append(op)

        if group_end > max_group_end:
            max_group_end = group_end

    leftover_track = next_track
    cursor = max_group_end
    orphan_items = []
    for idx in orphans:
        clip = by_id.get(idx)
        if clip:
            orphan_items.append({"clip": clip, "start": clip.start_seconds})
    orphan_items.sort(key=lambda x: x["start"])

    for item in orphan_items:
        clip = item["clip"]
        dur = clip.duration_seconds if clip.duration_seconds > 0 else 0.0
        new_track = leftover_track if place_on_tracks else clip.track_index
        gain_db = clip.gain_db if normalize_audio and clip.gain_db else 0.0
        op = {
            "type": "move",
            "id": clip.id,
            "name": clip.name,
            "trackIndex": clip.track_index,
            "clipIndex": clip.clip_index,
            "isAudio": clip.is_audio,
            "mediaPath": clip.media_path,
            "newStartSeconds": cursor,
            "newTrackIndex": new_track,
            "gainDb": gain_db,
        }
        if place_on_tracks:
            op["newAudioTrackIndex"] = new_track
        operations.append(op)
        cursor += dur
        if place_on_tracks:
            leftover_track += 1

    return operations
