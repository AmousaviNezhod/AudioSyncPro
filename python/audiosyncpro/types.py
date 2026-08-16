from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MediaInfo:
    path: str
    duration_seconds: float
    sample_rate: int
    channels: int
    channel_layout: str
    has_audio: bool
    creation_time: Optional[str] = None
    format_tags: Dict[str, str] = field(default_factory=dict)
    stream_tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class TimelineClip:
    id: int
    name: str
    media_path: str
    start_seconds: float
    duration_seconds: float
    track_index: int
    clip_index: int
    is_audio: bool
    media_info: Optional[MediaInfo] = None
    # populated during analysis
    samples: Optional[Any] = None  # np.ndarray (fine-rate window)
    coarse_samples: Optional[Any] = None  # np.ndarray at coarse_sample_rate
    coarse_sample_rate: int = 0
    media_duration_seconds: float = 0.0
    in_point_seconds: float = 0.0
    out_point_seconds: float = 0.0
    gain_db: float = 0.0
    max_volume: Optional[float] = None
    mean_volume: Optional[float] = None


@dataclass
class SyncSettings:
    preset: str = "balanced"  # fast, balanced, accurate, custom
    sample_rate: int = 16000
    coarse_sample_rate: int = 1000
    max_analyze_seconds: float = 40.0
    max_offset_seconds: float = 30.0
    fine_search_seconds: float = 2.0
    match_threshold: float = 0.45
    min_peak_ratio: float = 1.005
    min_z_score: float = 3.0
    min_overlap_ratio: float = 0.05
    normalize_audio: bool = False
    target_peak_db: float = -1.0
    use_creation_time_coarse: bool = False
    enable_drift: bool = True
    drift_min_duration: float = 60.0
    drift_window_seconds: float = 10.0
    drift_hop_seconds: float = 5.0
    place_on_tracks: bool = False
    ffmpeg_path: str = "ffmpeg"

    def effective_sample_rate(self) -> int:
        return int(self.sample_rate)

    def effective_coarse_sample_rate(self) -> int:
        """Return a coarse sample rate that keeps the coarse search buffer under ~4M samples."""
        desired = int(self.coarse_sample_rate)
        max_offset = max(0.0, float(self.max_offset_seconds))
        max_analyze = max(1.0, float(self.max_analyze_seconds))
        max_samples = 4_000_000
        min_coarse = 100
        total_seconds = 2.0 * max_offset + max_analyze
        if total_seconds * desired > max_samples and total_seconds > 0:
            return max(min_coarse, int(max_samples // total_seconds))
        return desired

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SyncSettings":
        """Build settings from UI/CLI dict, accepting both old and new key naming."""
        if not d:
            return cls()

        def _get(*keys, default=None, coerce=None):
            for k in keys:
                if k in d and d[k] is not None:
                    v = d[k]
                    if coerce:
                        try:
                            v = coerce(v)
                        except Exception:
                            continue
                    return v
            return default

        def _bool(v):
            return str(v).lower() in ("true", "1", "yes", "on")

        # Preset mapping from UI values.
        preset_in = _get("preset", "mode", default="balanced")
        preset_map = {
            "fast": "fast",
            "سریع": "fast",
            "balanced": "balanced",
            "متعادل": "balanced",
            "accurate": "accurate",
            "دقیق": "accurate",
            "custom": "custom",
            "سفارشی": "custom",
        }
        preset = preset_map.get(str(preset_in).lower(), str(preset_in).lower())

        # Preset defaults aligned with the UI presets.
        preset_defaults = {
            "fast": {"sample_rate": 8000, "coarse_sample_rate": 1000, "max_analyze_seconds": 20.0, "max_offset_seconds": 10.0, "fine_search_seconds": 1.0, "match_threshold": 0.35, "min_peak_ratio": 1.005},
            "balanced": {"sample_rate": 16000, "coarse_sample_rate": 1000, "max_analyze_seconds": 40.0, "max_offset_seconds": 30.0, "fine_search_seconds": 2.0, "match_threshold": 0.40, "min_peak_ratio": 1.005},
            "accurate": {"sample_rate": 22050, "coarse_sample_rate": 2000, "max_analyze_seconds": 80.0, "max_offset_seconds": 60.0, "fine_search_seconds": 3.0, "match_threshold": 0.45, "min_peak_ratio": 1.005},
            "custom": {},
        }
        defaults = preset_defaults.get(preset, preset_defaults["balanced"])

        return cls(
            preset=preset,
            sample_rate=_get("sampleRate", "sample_rate", default=defaults.get("sample_rate", 16000), coerce=int),
            coarse_sample_rate=_get("coarseSampleRate", "coarse_sample_rate", default=defaults.get("coarse_sample_rate", 4000), coerce=int),
            max_analyze_seconds=_get("sampleSeconds", "maxAnalyzeSeconds", "max_analyze_seconds", default=defaults.get("max_analyze_seconds", 40.0), coerce=float),
            max_offset_seconds=_get("maxOffset", "max_offset_seconds", default=defaults.get("max_offset_seconds", 30.0), coerce=float),
            fine_search_seconds=_get("fineSearchSeconds", "fine_search_seconds", default=defaults.get("fine_search_seconds", 2.0), coerce=float),
            match_threshold=_get("matchThreshold", "match_threshold", default=defaults.get("match_threshold", 0.45), coerce=float),
            min_peak_ratio=_get("minPeakRatio", "min_peak_ratio", default=defaults.get("min_peak_ratio", 1.005), coerce=float),
            min_z_score=_get("minZScore", "min_z_score", default=3.0, coerce=float),
            min_overlap_ratio=_get("minOverlapRatio", "min_overlap_ratio", default=0.1, coerce=float),
            normalize_audio=_get("normalizeAudio", "normalize_audio", default=False, coerce=_bool),
            target_peak_db=_get("targetPeak", "target_peak_db", default=-1.0, coerce=float),
            use_creation_time_coarse=_get("useCreationTimeCoarse", "use_creation_time_coarse", default=False, coerce=_bool),
            enable_drift=_get("enableDrift", "enable_drift", default=True, coerce=_bool),
            drift_min_duration=_get("driftMinDuration", "drift_min_duration", default=60.0, coerce=float),
            drift_window_seconds=_get("driftWindowSeconds", "drift_window_seconds", default=10.0, coerce=float),
            drift_hop_seconds=_get("driftHopSeconds", "drift_hop_seconds", default=5.0, coerce=float),
            place_on_tracks=_get("placeOnTracks", "place_on_tracks", default=True, coerce=_bool),
            ffmpeg_path=_get("ffmpegPath", "ffmpeg_path", default="ffmpeg"),
        )


@dataclass
class PeakInfo:
    lag_samples: float
    value: float
    offset_seconds: float
    sample_rate: int


@dataclass
class PairwiseResult:
    ref_index: int
    target_index: int
    offset_seconds: float
    confidence: float
    pearson: float
    peak_ratio: float
    z_score: float
    overlap_ratio: float
    method: str
    correlation_peak: float
    raw_peak: float
    overlap_seconds: float
    processing_time_ms: float
    drift_estimate: Optional[DriftEstimate] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DriftEstimate:
    slope: float  # seconds drift per second of reference time
    ppm: float  # parts per million
    offset0: float  # intercept in seconds
    fit_error: float  # RMSE of regression
    confidence: float
    window_count: int

    @property
    def drift_ppm(self) -> float:
        return self.ppm


@dataclass
class SyncResult:
    clip_id: Any
    offset_seconds: float
    offset_samples: int
    confidence: float
    correlation_peak: float
    peak_ratio: float
    overlap_seconds: float
    drift_ppm: Optional[float]
    processing_time_ms: float
    method: str
    status: str


@dataclass
class SyncDiagnostics:
    preprocessing: Dict[str, Any] = field(default_factory=dict)
    sample_rate: int = 0
    channel_layout: str = ""
    analyzed_duration: float = 0.0
    search_range_seconds: float = 0.0
    coarse_peaks: List[Dict[str, Any]] = field(default_factory=list)
    fine_peaks: List[Dict[str, Any]] = field(default_factory=list)
    selected_peak: Dict[str, Any] = field(default_factory=dict)
    correlation_score: float = 0.0
    confidence_score: float = 0.0
    drift_estimate: Optional[Dict[str, Any]] = None
    processing_time_ms: float = 0.0
    memory_bytes: Optional[int] = None


def clip_duration_or_default(clip: TimelineClip, default: float = 0.0) -> float:
    return clip.duration_seconds if clip.duration_seconds and math.isfinite(clip.duration_seconds) else default


def samples_to_seconds(n: int, sample_rate: int) -> float:
    if sample_rate == 0:
        return 0.0
    return float(n) / sample_rate


def seconds_to_samples(t: float, sample_rate: int) -> int:
    if sample_rate == 0:
        return 0
    return int(round(t * sample_rate))
