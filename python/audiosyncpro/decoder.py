from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import traceback
from typing import Optional, Tuple

import numpy as np

from .types import MediaInfo, TimelineClip


def _startupinfo():
    """Return Windows startupinfo that hides the console window for ffmpeg."""
    if sys.platform != "win32":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


def _creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def get_bundle_ffmpeg_path() -> Optional[str]:
    candidates = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(meipass)
    if getattr(sys, "frozen", False):
        candidates.append(os.path.dirname(sys.executable))
    if __file__:
        candidates.append(os.path.dirname(os.path.abspath(__file__)))
    for base in candidates:
        candidate = os.path.join(base, "bin", "ffmpeg.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


def resolve_ffmpeg_path(settings_or_path=None) -> str:
    explicit = ""
    if isinstance(settings_or_path, str):
        explicit = settings_or_path.strip()
    elif isinstance(settings_or_path, dict):
        explicit = (settings_or_path.get("ffmpegPath") or "").strip()

    if explicit and explicit.lower() not in ("ffmpeg", "ffmpeg.exe"):
        if os.path.isfile(explicit):
            return explicit
        if shutil.which(explicit):
            return explicit

    # If explicit is a generic placeholder, prefer bundled if PATH lookup fails.
    if explicit and shutil.which(explicit):
        return explicit

    bundled = get_bundle_ffmpeg_path()
    if bundled:
        return bundled

    if shutil.which("ffmpeg"):
        return "ffmpeg"
    return explicit or "ffmpeg"


def run_ffmpeg(
    ffmpeg_path: str,
    args: list,
    capture_stdout: bool = True,
    timeout: Optional[float] = None,
) -> Tuple[int, bytes, bytes]:
    cmd = [ffmpeg_path] + args
    startupinfo = _startupinfo()
    creationflags = _creationflags()
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE if capture_stdout else subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return -1, b"", str(e).encode("utf-8")


_FFMPEG_CHANNEL_COUNTS = {
    "mono": 1,
    "stereo": 2,
    "2.1": 3,
    "3.0": 3,
    "3.0(back)": 3,
    "4.0": 4,
    "quad": 4,
    "5.0": 5,
    "5.1": 6,
    "6.0": 6,
    "6.1": 7,
    "7.1": 8,
    "8.0": 8,
}


def _layout_to_channels(layout: str) -> int:
    return _FFMPEG_CHANNEL_COUNTS.get(layout.strip().lower(), 0)


def _parse_duration(text: str) -> float:
    """Parse HH:MM:SS.mmm duration from ffmpeg stderr."""
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", text)
    if not m:
        return 0.0
    h, m_, s = m.groups()
    return int(h) * 3600 + int(m_) * 60 + float(s)


def _parse_stream_info(text: str) -> Tuple[int, int, str, bool]:
    """Return (sample_rate, channels, channel_layout, has_audio) from ffmpeg -i output."""
    has_audio = bool(re.search(r"Stream\s+#0?:\d+:.*Audio:", text))
    sample_rate = 0
    channels = 0
    channel_layout = ""

    # Look for the first audio stream description line.
    for line in text.splitlines():
        if "Audio:" not in line or "Stream #" not in line:
            continue
        # Sample rate: e.g. "16000 Hz" or "48 kHz".
        sr_match = re.search(r"([\d.]+)\s*(Hz|kHz)", line)
        if sr_match:
            val = float(sr_match.group(1))
            unit = sr_match.group(2).lower()
            sample_rate = int(val * 1000) if unit == "khz" else int(val)

        # Channel layout token like "mono", "stereo", "5.1".
        layout_match = re.search(r",\s*([\w.()]+)\s*,\s*(s\d+|fltp|s16|s32|float|double)", line)
        if layout_match:
            candidate = layout_match.group(1).strip()
            if _layout_to_channels(candidate):
                channel_layout = candidate
                channels = _layout_to_channels(candidate)
            elif candidate.isdigit():
                channels = int(candidate)
        # Fallback: "2 channels" explicit form.
        if channels == 0:
            ch_match = re.search(r"(\d+)\s+channels?", line)
            if ch_match:
                channels = int(ch_match.group(1))
        if sample_rate:
            break

    if channels == 0:
        channels = 2 if channel_layout and _layout_to_channels(channel_layout) == 2 else 1
    if not channel_layout:
        channel_layout = "mono" if channels == 1 else "stereo"
    return sample_rate, channels, channel_layout, has_audio


def inspect_media(ffmpeg_path: str, media_path: str) -> MediaInfo:
    """Probe media using ffmpeg -i (no separate ffprobe required)."""
    args = ["-v", "info", "-nostdin", "-i", media_path, "-t", "0", "-f", "null", "-"]
    code, _, stderr = run_ffmpeg(ffmpeg_path, args, timeout=60.0)
    text = stderr.decode("utf-8", "ignore")

    if not re.search(r"Stream\s+#0?:\d+:.*Audio:", text):
        if code != 0:
            raise RuntimeError(text[:500])
        # No audio stream and no error -> treat as audio-less media.
        return MediaInfo(
            path=media_path,
            duration_seconds=0.0,
            sample_rate=0,
            channels=0,
            channel_layout="",
            has_audio=False,
            creation_time=None,
            format_tags={},
            stream_tags={},
        )

    duration = _parse_duration(text)
    sample_rate, channels, channel_layout, has_audio = _parse_stream_info(text)

    return MediaInfo(
        path=media_path,
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
        channel_layout=channel_layout,
        has_audio=has_audio,
        creation_time=None,
        format_tags={},
        stream_tags={},
    )


def extract_audio(
    ffmpeg_path: str,
    media_path: str,
    sample_rate: int = 16000,
    start_seconds: float = 0.0,
    duration_seconds: Optional[float] = None,
    mono: bool = True,
    timeout: float = 300.0,
) -> np.ndarray:
    """Extract audio to mono/specified sample rate float32 numpy array."""
    args = ["-v", "error", "-nostdin"]
    if start_seconds and start_seconds > 1e-6:
        args += ["-ss", f"{float(start_seconds):.9f}"]
    args += ["-i", media_path]
    if duration_seconds is not None and duration_seconds > 0:
        args += ["-t", f"{float(duration_seconds):.9f}"]
    if mono:
        args += ["-ac", "1"]
    args += [
        "-ar",
        str(int(sample_rate)),
        "-map",
        "0:a:0",
        "-f",
        "f32le",
        "-",
    ]
    code, stdout, stderr = run_ffmpeg(ffmpeg_path, args, timeout=timeout)
    if code != 0:
        raise RuntimeError(f"ffmpeg extract failed ({code}): {stderr.decode('utf-8', 'ignore')[:500]}")
    if not stdout:
        raise RuntimeError("ffmpeg extract returned empty audio")
    expected = (len(stdout) // 4) * 4
    if expected != len(stdout):
        stdout = stdout[:expected]
    samples = np.frombuffer(stdout, dtype=np.float32)
    if not np.all(np.isfinite(samples)):
        raise RuntimeError("ffmpeg output contained non-finite samples")
    return samples


def detect_volume(ffmpeg_path: str, media_path: str, duration_seconds: Optional[float] = None, timeout: float = 120.0) -> dict:
    """Use volumedetect to obtain mean/max dB."""
    filter_str = "volumedetect"
    args = ["-v", "info", "-nostdin", "-i", media_path]
    if duration_seconds and duration_seconds > 0:
        args += ["-t", str(float(duration_seconds))]
    args += ["-af", filter_str, "-f", "null", "-"]
    code, _, stderr = run_ffmpeg(ffmpeg_path, args, timeout=timeout)
    if code != 0:
        return {"meanVolume": None, "maxVolume": None}
    text = stderr.decode("utf-8", "ignore")
    mean_vol = None
    max_vol = None
    for line in text.splitlines():
        if "mean_volume:" in line:
            try:
                mean_vol = float(line.split("mean_volume:")[1].split()[0])
            except Exception:
                pass
        if "max_volume:" in line:
            try:
                max_vol = float(line.split("max_volume:")[1].split()[0])
            except Exception:
                pass
    return {"meanVolume": mean_vol, "maxVolume": max_vol}


def gain_for_normalization(max_volume_db: Optional[float], target_peak_db: float) -> float:
    if max_volume_db is None or not np.isfinite(max_volume_db):
        return 0.0
    gain = target_peak_db - max_volume_db
    # Avoid over-boosting already-loud clips.
    if gain > 20.0:
        gain = 20.0
    if gain < -20.0:
        gain = -20.0
    return float(gain)


def analyze_clip(clip: dict, ffmpeg_path: str, settings) -> TimelineClip:
    from .types import SyncSettings
    if isinstance(settings, dict):
        settings = SyncSettings(**settings)
    media_info = inspect_media(ffmpeg_path, clip["mediaPath"])
    sr = settings.effective_sample_rate()
    coarse_sr = settings.effective_coarse_sample_rate()
    max_analyze = settings.max_analyze_seconds
    if max_analyze <= 0:
        max_analyze = 40.0
    max_offset = settings.max_offset_seconds
    if max_offset <= 0:
        max_offset = 30.0

    # For coarse search we need the first (max_offset + max_analyze) seconds of the
    # source media so we can locate any offset within [-max_offset, +max_offset].
    duration = media_info.duration_seconds if media_info and media_info.duration_seconds > 0 else clip.get("durationSeconds")
    if duration is None or duration <= 0:
        duration = max_offset + max_analyze
    coarse_duration = min(float(duration), max_offset + max_analyze)
    if coarse_duration < 1.0:
        coarse_duration = max_offset + max_analyze

    timeout = max(300.0, coarse_duration * 0.25 + 60.0)
    coarse_samples = extract_audio(
        ffmpeg_path,
        clip["mediaPath"],
        sample_rate=coarse_sr,
        start_seconds=0.0,
        duration_seconds=coarse_duration,
        mono=True,
        timeout=timeout,
    )

    in_point = float(clip.get("inPointSeconds", 0.0) or 0.0)
    out_point = float(clip.get("outPointSeconds", 0.0) or 0.0)
    if out_point <= in_point and media_info.duration_seconds > 0:
        out_point = float(media_info.duration_seconds)

    gain_db = 0.0
    max_volume = None
    mean_volume = None
    if settings.normalize_audio:
        vol = detect_volume(ffmpeg_path, clip["mediaPath"], coarse_duration)
        max_volume = vol.get("maxVolume")
        mean_volume = vol.get("meanVolume")
        gain_db = gain_for_normalization(max_volume, settings.target_peak_db)

    return TimelineClip(
        id=clip.get("id", 0),
        name=clip.get("name", ""),
        media_path=clip["mediaPath"],
        start_seconds=float(clip.get("startSeconds", 0) or 0),
        duration_seconds=float(clip.get("durationSeconds", 0) or 0),
        track_index=int(clip.get("trackIndex", -1)),
        clip_index=int(clip.get("clipIndex", -1)),
        is_audio=bool(clip.get("isAudio", False)),
        media_info=media_info,
        media_duration_seconds=float(media_info.duration_seconds if media_info else 0.0),
        coarse_samples=coarse_samples,
        coarse_sample_rate=coarse_sr,
        in_point_seconds=in_point,
        out_point_seconds=out_point,
        gain_db=gain_db,
        max_volume=max_volume,
        mean_volume=mean_volume,
    )
