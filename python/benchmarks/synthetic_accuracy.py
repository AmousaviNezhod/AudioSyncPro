#!/usr/bin/env python3
"""Synthetic accuracy benchmark for AudioSyncPro.

Uses known-offset WAV files in C:\temp:
- base.wav (reference)
- delayed_500.wav (offset +0.500 s)
- delayed_1000.wav (offset +1.000 s)
- sine.wav (unrelated)

Run with: python benchmarks/synthetic_accuracy.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audiosyncpro.engine import process_sync_request


TEST_CASES = [
    {
        "name": "base vs delayed_500",
        "clips": [
            {"id": 1, "name": "base.wav", "mediaPath": r"C:\temp\base.wav", "startSeconds": 0, "durationSeconds": 5, "trackIndex": 0, "clipIndex": 0, "isAudio": True},
            {"id": 2, "name": "delayed_500.wav", "mediaPath": r"C:\temp\delayed_500.wav", "startSeconds": 0, "durationSeconds": 5, "trackIndex": 0, "clipIndex": 1, "isAudio": True},
        ],
        "expected_offset_seconds": 0.5,
        "expected_accepted": True,
    },
    {
        "name": "base vs delayed_1000",
        "clips": [
            {"id": 1, "name": "base.wav", "mediaPath": r"C:\temp\base.wav", "startSeconds": 0, "durationSeconds": 5, "trackIndex": 0, "clipIndex": 0, "isAudio": True},
            {"id": 3, "name": "delayed_1000.wav", "mediaPath": r"C:\temp\delayed_1000.wav", "startSeconds": 0, "durationSeconds": 5, "trackIndex": 0, "clipIndex": 1, "isAudio": True},
        ],
        "expected_offset_seconds": 1.0,
        "expected_accepted": True,
    },
    {
        "name": "base vs unrelated sine",
        "clips": [
            {"id": 1, "name": "base.wav", "mediaPath": r"C:\temp\base.wav", "startSeconds": 0, "durationSeconds": 5, "trackIndex": 0, "clipIndex": 0, "isAudio": True},
            {"id": 4, "name": "sine.wav", "mediaPath": r"C:\temp\sine.wav", "startSeconds": 0, "durationSeconds": 5, "trackIndex": 0, "clipIndex": 1, "isAudio": True},
        ],
        "expected_offset_seconds": None,
        "expected_accepted": False,
    },
    {
        "name": "multicam 4 clips",
        "clips": [
            {"id": 1, "name": "base.wav", "mediaPath": r"C:\temp\base.wav", "startSeconds": 0, "durationSeconds": 5, "trackIndex": 0, "clipIndex": 0, "isAudio": True},
            {"id": 2, "name": "delayed_500.wav", "mediaPath": r"C:\temp\delayed_500.wav", "startSeconds": 0, "durationSeconds": 5, "trackIndex": 0, "clipIndex": 1, "isAudio": True},
            {"id": 3, "name": "delayed_1000.wav", "mediaPath": r"C:\temp\delayed_1000.wav", "startSeconds": 0, "durationSeconds": 5, "trackIndex": 0, "clipIndex": 2, "isAudio": True},
            {"id": 4, "name": "sine.wav", "mediaPath": r"C:\temp\sine.wav", "startSeconds": 0, "durationSeconds": 5, "trackIndex": 0, "clipIndex": 3, "isAudio": True},
        ],
        "expected_offset_seconds": None,
        "expected_accepted": None,
    },
]


def run():
    settings = {"preset": "accurate", "sampleRate": 16000, "sampleSeconds": 5, "maxOffset": 30, "matchThreshold": 0.35, "placeOnTracks": True, "normalizeAudio": False}
    print("=" * 90)
    print(f"{'Case':<30} {'Expected':<12} {'Estimated':<12} {'Error':<12} {'Confidence':<12} {'Accepted':<10} {'Runtime ms':<12}")
    print("=" * 90)
    all_pass = True
    for case in TEST_CASES:
        request = {"action": "sync", "clips": case["clips"], "settings": settings, "ffmpegPath": ""}
        t0 = time.perf_counter()
        result = process_sync_request(request)
        runtime_ms = (time.perf_counter() - t0) * 1000.0

        if not result.get("success"):
            print(f"{case['name']:<30} FAILED: {result.get('error')}")
            all_pass = False
            continue

        sync_results = result.get("syncResults", [])
        # For multi-clip cases use the group/orphan summary; otherwise use the pair against the second clip.
        target_id = case["clips"][1]["id"]
        pair = next((r for r in sync_results if r.get("refId") == 1 and r.get("targetId") == target_id), None)

        if case["expected_offset_seconds"] is None and case["expected_accepted"] is None:
            # Multicam summary branch
            groups = result.get("groups", [])
            orphans = result.get("orphans", [])
            print(f"{case['name']:<30} groups={len(groups)} orphans={orphans} runtime={runtime_ms:.1f}ms")
            if len(groups) != 1 or set(groups[0]) != {1, 2, 3} or orphans != [4]:
                all_pass = False
                print("  FAIL: expected group {1,2,3}, orphan [4]")
            else:
                print("  PASS")
            continue

        if pair:
            estimated = pair.get("offsetSeconds")
            confidence = pair.get("confidence")
            accepted = pair.get("accepted", False)
            expected = case["expected_offset_seconds"]
            if expected is not None:
                error_s = abs(estimated - expected)
                status = "PASS" if error_s < 0.01 and accepted == case["expected_accepted"] else "FAIL"
            else:
                error_s = float("nan")
                status = "PASS" if accepted == case["expected_accepted"] else "FAIL"
            if status == "FAIL":
                all_pass = False
            print(f"{case['name']:<30} {str(expected):<12} {estimated:<12.6f} {error_s:<12.6f} {confidence:<12.3f} {str(accepted):<10} {runtime_ms:<12.1f} {status}")

    print("=" * 90)
    print("OVERALL:", "PASS" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(run())
