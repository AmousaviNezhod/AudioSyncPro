# AudioSyncPro — Algorithm Decision Document

## 1. Goals

- Sub-sample accurate audio synchronization of 2–16+ timeline clips.
- Robust to noise, reverb, EQ, AGC, phase, sample-rate mismatch, partial overlap, and short/long recordings.
- Deterministic, explainable, and dependency-light (only numpy + bundled ffmpeg).
- Fast enough for real editing sessions (seconds to a few minutes for typical multicam shoots).
- Clear confidence and diagnostics for every result.

## 2. Pipeline overview

```
MediaInspection → AudioExtraction → ChannelMix/Resample → Preprocessing →
CoarseSearch → FineSearch → Confidence → DriftEstimate →
MulticlipOptimization → PlanBuilder → Diagnostics
```

Each stage is an independent, typed, testable Python module.

## 3. Stage-by-stage decisions

### 3.1 Media inspection (`decoder.inspect_media`)

**Decision:** Always run `ffprobe -v error -print_format json -show_streams -show_format` before decoding.

**Why:**
- We need duration, sample rate, channel count, channel layout, and codec to choose extraction parameters.
- We need to detect sample-rate mismatch early so the resampler can reconcile it to a common analysis rate.
- We can optionally read `creation_time` / `com.apple.quicktime.creationdate` metadata for a coarse timeline hint (shotcut-multicam-sync idea).

### 3.2 Audio extraction (`decoder.extract_audio`)

**Decision:** Decode the first audio stream to mono `float32` PCM using the bundled `ffmpeg`. Support `-ss` start and `-t` duration for partial reads.

**Why:**
- Full file decode is unnecessary and dangerous for long files. A bounded search window keeps memory bounded.
- `float32` gives enough dynamic range for correlation and normalization.
- Mono mix via `ffmpeg -ac 1` is acceptable; phase-cancellation risk is mitigated by later preprocessing (pre-emphasis + normalization).

**Performance choices:**
- Default analysis sample rate: 16000 Hz for fine search; 4000 Hz for coarse search.
- Partial read limits: `fast` preset analyses up to 15 s; `balanced` 60 s; `accurate` 300 s; `custom` user-defined.

### 3.3 Channel and sample-rate normalization (`resampler`)

**Decision:**
- If source is multi-channel, mix to mono using mean.
- Resample to the analysis sample rate once, in numpy, by linear interpolation or decimation/filtering.

**Why:**
- We avoid running ffmpeg twice by extracting at a high enough rate and then decimating for coarse search.
- For downsample factor > 10, a simple low-pass (windowed sinc or scipy not available) is approximated by averaging non-overlapping blocks (decimation with an implicit boxcar). This is acceptable for a coarse search.

### 3.4 Preprocessing (`preprocessing`)

**Decision:** Apply, in order:
1. DC removal (subtract mean).
2. Pre-emphasis filter `y[n] = x[n] - 0.97*x[n-1]`.
3. RMS normalization to a target RMS with a noise floor (avoid boosting pure silence).

**Why:**
- Pre-emphasis boosts transients (claps, speech onsets) that are robust across microphones.
- RMS normalization makes correlation invariant to AGC and gain differences.
- Noise floor prevents the correlation of silent/very quiet clips from dominating.

**What we rejected:**
- Full spectral whitening (too heavy, too many parameters).
- Librosa `PCEN` (needs librosa).

### 3.5 Coarse search (`features` + `algorithms.envelope_correlation`, `algorithms.spectral_correlation`)

**Decision:** Use two complementary coarse methods and keep the strongest candidate:
1. **Envelope correlation:** downsample to 400–1000 Hz (energy envelope), compute FFT-based cross-correlation, find peak. Robust to EQ, AGC, and mild reverb; poor at sub-sample accuracy.
2. **Mel-spectral correlation:** log-mel spectrogram at ~16 frames/sec, standardize each band across time, sum GCC-PHAT across mel bands. Robust to EQ and noise.

**Search range:** bounded by `maxOffsetSeconds` (default 30 s) or by creation-time coarse offset ± window.

**Why:**
- Envelope gives fast, long-offset location.
- Mel-spectral gives robustness to microphone frequency differences.
- Coarse only needs to place the candidate within the fine-search window (e.g., ±0.5 s).

### 3.6 Fine search (`algorithms.gcc_phat`)

**Decision:** Use FFT-based GCC-PHAT on a high-rate window around the coarse offset.

**Parameters:**
- Analysis rate: 16000 Hz.
- Correlation window: the overlapping region of the two clips, but bounded by `maxAnalyzeDuration`.
- Search margin: ± `maxFineOffset` (default 2 s or user `maxOffset`).
- Sub-sample refinement by parabolic interpolation of the GCC peak.

**Why:**
- GCC-PHAT whitens the cross-spectrum, making it robust to reverberation and non-linear spectral differences.
- FFT is fast and deterministic.
- Parabolic interpolation gives sub-sample accuracy without upsampling.

**What we rejected:**
- Direct brute-force sample shifting (synaudio style) because it is O(n^2) for long windows.
- Upsampling-based sub-sample refinement (too slow; parabolic is adequate and well studied).

### 3.7 Confidence (`confidence`)

**Decision:** Compute four per-pair metrics and combine them:
1. `pearson` — Pearson correlation of the raw waveforms aligned at the estimated integer lag, clamped to [0, 1].
2. `peak_ratio` — `peak / second_peak` of the GCC magnitude surface.
3. `z_score` — `(peak - mean) / std` of the GCC surface (shotcut-multicam-sync idea).
4. `overlap_ratio` — overlap duration / min(clip duration, analyzed duration).

Final `confidence = 0.35*pearson + 0.25*peak_ratio_norm + 0.25*z_score_norm + 0.15*overlap_ratio`.

**Thresholds:**
- `matchThreshold` 0.45 default (same as current). If final confidence < threshold, reject the pair.
- Provide `confidence` and `peakRatio` and `zScore` in diagnostics.

**Why:**
- A single metric is fragile. Pearson can be high by chance on short windows; peak ratio catches ambiguous surfaces.
- z_score measures how much the peak stands out relative to the rest of the search.
- Overlap ratio penalizes matches with very little shared content.

### 3.8 Outlier rejection

**Decision:**
- Reject edges where `confidence < matchThreshold`.
- Reject edges where `peakRatio < 1.3` (second peak within 77% of best).
- Reject edges where `zScore < 3.0` (peak not statistically significant).
- Reject edges where `overlapRatio < 0.1` (less than 10% shared audio).

### 3.9 Multiclip optimization (`multicam`)

**Decision:**
- Build full pairwise graph.
- Find connected components (potential sync groups).
- Per component, choose the reference clip as the one with the highest sum of pairwise confidences and the largest analyzed energy.
- Solve a weighted least-squares problem for the global offsets: minimize `Σ w_ij * (x_j - x_i - o_ij)^2`.
- Weights `w_ij = confidence_ij`.
- Fix the reference clip at `x_ref = 0`.
- Use `np.linalg.lstsq` for the linear system.
- After solving, compute residuals and drop edges with residual > 3× median residual, then re-solve.

**Why:**
- Path-sum (current `get_path_offset`) propagates error along chains.
- Weighted least squares produces the maximum-likelihood estimate for Gaussian errors and is deterministic.
- Iterative residual rejection makes the result robust to a few bad pairwise matches.

### 3.10 Drift estimation (`drift`)

**Decision:**
- If the overlapping region is longer than `driftMinDuration` (default 60 s), split it into overlapping windows (default 10 s, hop 5 s).
- For each window, run a small fine GCC-PHAT search around the current global offset.
- Collect `(window_center_time, offset)` pairs.
- Fit `offset(t) = offset0 + drift * t` using weighted least squares.
- Return `drift_ppm = drift * 1_000_000`, `fit_error` (RMSE), and `confidence`.
- Do **not** apply time-stretching automatically. The bridge receives `driftPpm` and `driftEstimateConfidence` for diagnostics and future retiming support.

**Why:**
- Drift is a real problem for long recordings from independent clocks (cameras, recorders). We must at least detect it.
- Time-stretching changes the clip duration and requires audio retiming; we keep that separate from alignment for safety.

### 3.11 Premiere plan builder (`plan`)

**Decision:**
- Keep the existing ExtendScript host contract but make operation generation cleaner.
- Compute new start times relative to the global reference and shift the whole group so no clip starts before time zero.
- If `placeOnTracks` is true, place each matched group on its own track (or maintain audio/video track typing).
- Orphans are placed end-to-end on a leftover track after the last group end.
- Include a `diagnostics` block in the server response so the UI can log confidence, method, drift, etc.

### 3.12 Sample-rate and timebase handling

**Decision:**
- All internal calculations use seconds and samples at the analysis sample rate.
- Offsets returned to the bridge are in seconds (`float64`).
- The ExtendScript host uses `Time.seconds` and avoids integer ticks unless necessary.
- Do not silently round to video frames. If the user needs frame-locked alignment, add an option later.

## 4. Algorithm selection rationale

| Problem | Chosen approach | Rejected alternatives | Reason |
|---|---|---|---|
| Pairwise offset | GCC-PHAT + parabolic interp | Brute-force Pearson | FFT speed + PHAT robustness |
| Coarse long-offset search | Envelope + mel-spectral | Full-rate FFT | Memory/speed |
| Robustness to EQ/reverb | Mel-spectral GCC-PHAT | Raw Pearson | Spectral shape matters more than raw waveform |
| Multi-clip | Weighted least-squares graph | Chain/path sum | Global consistency, less error propagation |
| Confidence | Combined Pearson/peak/z/overlap | Single correlation | Prevents false positives |
| Drift | Windowed fine offsets + regression | Ignore | Long recordings need detection |
| Performance | Partial reads + downsampling + threading | Full-file decode | Memory/scalability |

## 5. Future options

- Optional fingerprinting stage for very long (hours) recordings where full correlation is too expensive.
- Optional time-stretching for drift correction once the bridge supports audio retiming.
- GPU FFT via CuPy if the user environment permits.
- SIMD/C extension for the inner correlation loop (not needed while numpy/BLAS is fast enough).
