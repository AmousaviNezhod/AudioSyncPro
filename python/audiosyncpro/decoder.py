from __future__ import annotations

import json
import os
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


def run_ffprobe(ffmpeg_path: str, media_path: str, timeout: float = 60.0) -> dict:
    """Run ffprobe from the same directory as ffmpeg and return JSON."""
    # Try ffprobe next to ffmpeg executable first, then PATH.
    candidates = []
    if os.path.isfile(ffmpeg_path):
        dir_name = os.path.dirname(ffmpeg_path)
        candidates.append(os.path.join(dir_name, "ffprobe.exe"))
        candidates.append(os.path.join(dir_name, "ffprobe"))
    candidates.append("ffprobe")

    ffprobe_path = None
    for c in candidates:
        if shutil.which(c) or os.path.isfile(c):
            ffprobe_path = c
            break
    if not ffprobe_path:
        raise RuntimeError("ffprobe not found")

    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        media_path,
    ]
    startupinfo = _startupinfo()
    creationflags = _creationflags()
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "ignore")[:500])
    return json.loads(result.stdout.decode("utf-8"))


def inspect_media(ffmpeg_path: str, media_path: str) -> MediaInfo:
    info = run_ffprobe(ffmpeg_path, media_path)
    fmt = info.get("format") or {}
    streams = info.get("streams") or []

    duration = 0.0
    try:
        duration = float(fmt.get("duration", 0) or 0)
    except Exception:
        pass

    sample_rate = 0
    channels = 0
    channel_layout = ""
    has_audio = False
    creation_time = None

    for s in streams:
        if s.get("codec_type") == "audio":
            has_audio = True
            try:
                sample_rate = int(s.get("sample_rate", 0) or 0)
            except Exception:
                pass
            try:
                channels = int(s.get("channels", 0) or 0)
            except Exception:
                pass
            channel_layout = s.get("channel_layout") or ""
            if not creation_time:
                creation_time = s.get("tags", {}).get("creation_time") or s.get("tags", {}).get("com.apple.quicktime.creationdate")
            break

    if not creation_time:
        creation_time = fmt.get("tags", {}).get("creation_time") or fmt.get("tags", {}).get("com.apple.quicktime.creationdate")

    if duration == 0.0 and has_audio:
        # Fallback: estimate from bit rate if available.
        try:
            bit_rate = float(fmt.get("bit_rate", 0) or 0)
            size = float(fmt.get("size", 0) or 0)
            if bit_rate > 0 and size > 0:
                duration = (size * 8) / bit_rate
        except Exception:
            pass

    return MediaInfo(
        path=media_path,
        duration_seconds=duration,
        sample_rate=sample_rate,
        channels=channels,
        channel_layout=channel_layout or ("mono" if channels == 1 else "stereo"),
        has_audio=has_audio,
        creation_time=creation_time,
        format_tags=fmt.get("tags") or {},
        stream_tags=streams[0].get("tags") if streams else {},
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
    if start_seconds and start_seconds > 0:
        args += ["-ss", str(float(start_seconds))]
    args += ["-i", media_path]
    if duration_seconds is not None and duration_seconds > 0:
        args += ["-t", str(float(duration_seconds))]
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
    args = ["-v", "error", "-nostdin", "-i", media_path]
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
    sample_rate = settings.effective_sample_rate()
    max_analyze = settings.max_analyze_seconds
    if max_analyze <= 0:
        max_analyze = 60.0
    # Use clip duration or media duration, whichever smaller, capped at max_analyze.
    duration = clip.get("durationSeconds") or media_info.duration_seconds
    analyze_duration = min(duration, max_analyze) if duration > 0 else max_analyze
    samples = extract_audio(
        ffmpeg_path,
        clip["mediaPath"],
        sample_rate=sample_rate,
        start_seconds=0.0,
        duration_seconds=analyze_duration,
        mono=True,
    )
    gain_db = 0.0
    max_volume = None
    mean_volume = None
    if settings.normalize_audio:
        vol = detect_volume(ffmpeg_path, clip["mediaPath"], analyze_duration)
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
        samples=samples,
        gain_db=gain_db,
        max_volume=max_volume,
        mean_volume=mean_volume,
    )
