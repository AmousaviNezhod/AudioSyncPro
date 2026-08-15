# External Repository Comparison — AudioSyncPro Engineering Study

This document records the findings from inspecting the four requested open-source audio synchronization projects and additional literature.

## 1. synaudio (https://github.com/eshaz/synaudio)

**License:** GNU Lesser General Public License v3 (LGPL-3.0).

**Algorithm:**
- Pure Pearson correlation coefficient (covariance / std product) between two PCM buffers.
- Hand-optimized WebAssembly with SIMD (128-bit) and scalar fallbacks; compiled from `src/correlate.c`.
- Coarse-to-fine search: `initialGranularity` skips samples in a first pass, then a fine pass around the best coarse offset with step = 1.
- `correlationSampleSize` limits how many samples are compared (default 11025 ≈ 0.25 s @ 44.1 kHz).
- Multi-clip: builds a directed graph of pairwise results, removes cycles by keeping the stronger edge, then traverses from roots to produce groups/offsets.
- `syncWorkerConcurrent` splits the base buffer into chunks and runs workers in parallel.

**Key files:**
- `src/SynAudio.js` — orchestration, graph logic.
- `src/correlate.c` — C/WASM inner loop (Pearson, mean subtraction, SIMD).
- `test/SynAudio.test.js` — synthetic test cases.

**What is useful:**
- The idea of a bounded `correlationSampleSize` to keep computation constant regardless of file length.
- The idea of `initialGranularity` as a coarse-to-fine search.
- The graph-based multi-clip grouping (cycle removal, root detection, traversal).
- SIMD/Worker strategy is relevant only if we ever port to JS/WASM; for Python we get SIMD through numpy/BLAS.

**Limitations observed:**
- Uses full-resolution raw PCM; no spectral/feature robustness to EQ, reverb, or phase.
- Pearson is not shift-invariant to large DC offsets; but mean is subtracted.
- No drift estimation; assumes a fixed sample offset.
- No confidence beyond the correlation coefficient.
- Graph traversal can still propagate error if the graph is a chain.

**Reuse decision:**
- Do NOT copy the WebAssembly/C code (LGPL copyleft concerns for a static PyInstaller binary).
- Borrow the **coarse-to-fine granularity** and **graph cycle-removal/grouping** concepts, reimplemented cleanly in Python.

## 2. AudioAlign (https://github.com/protyposis/AudioAlign)

**License:** GNU Affero General Public License v3 (AGPL-3.0).

**Algorithm:**
- GUI around the Aurio library.
- Supports multiple fingerprinting strategies:
  - Chromaprint (AcoustID)
  - Echoprint (deprecated Spotify fingerprinting)
  - Haitsma-Kalker (robust hash-based fingerprinting)
  - Wang fingerprinting
- Also exposes cross-correlation matching (`CrossCorrelationResult.xaml.cs`).
- Likely uses DTW / fingerprint matching for long alignment.

**Key files:**
- `AudioAlign/Models/*FingerprintingModel.cs`
- `AudioAlign/CrossCorrelationResult.xaml.cs`

**What is useful:**
- Fingerprinting is a valid approach when recordings are long and differ in clock, but it requires a large reference database or very distinctive content.
- The multi-strategy matching idea (multiple fingerprint models) shows that no single algorithm dominates all content.

**Limitations:**
- AGPL is *strong copyleft* and network-use triggers; not safe to embed in a proprietary CEP extension without fully licensing the extension under AGPL.
- Core library (Aurio) is not in the repo; must be fetched separately, also AGPL.
- Fingerprinting is overkill for short timeline clips and less precise than cross-correlation for sub-frame sync.

**Reuse decision:**
- Do NOT reuse any code or Aurio. Use only as evidence that **multi-strategy comparison** and **fingerprinting** exist; for this product, a deterministic cross-correlation/GCC-PHAT pipeline is sufficient and safer.

## 3. skelly_synchronize (https://github.com/freemocap/skelly_synchronize)

**License:** GNU Affero General Public License v3 (AGPL-3.0).

**Algorithm:**
- Two modes:
  1. Audio cross-correlation via `scipy.signal.correlate(..., method='fft')`.
  2. Brightness flash detection in video frames (`cv2` + numpy).
- Normalizes audio (z-score) before correlation.
- For audio mode: extracts WAV from each video with ffmpeg, loads with `librosa`, correlates each pair to the first file, normalizes lags so the latest-starting video has lag 0, then trims all to same length.

**Key files:**
- `skelly_synchronize/skelly_synchronize.py`
- `core_processes/correlation_functions.py`
- `core_processes/audio_utilities.py`
- `core_processes/video_functions/ffmpeg_functions.py`

**What is useful:**
- Confirms that ffmpeg-based audio extraction + scipy/numpy FFT correlation is a practical baseline.
- Demonstrates normalizing lags to a common timeline (latest-start = 0).

**Limitations:**
- Single reference (first file) creates sequential dependence.
- No confidence scoring or weak-match rejection.
- Hard dependency on librosa, soundfile, scipy, cv2 (heavy).
- AGPL again.

**Reuse decision:**
- Do NOT copy code. Borrow the workflow idea (extract → correlate → normalize lag → trim) but implement with our own bounded search, confidence, and graph optimization.

## 4. shotcut-multicam-sync (https://github.com/vitaly-zdanevich/shotcut-multicam-sync)

**License:** MIT License.

**Algorithm:**
- Generates a Shotcut MLT multicam project from one master audio and folders of clips.
- **Coarse placement from file `creation_time` metadata** (tries both start-of-recording and end-of-recording interpretations).
- **Fine placement by normalized FFT cross-correlation** within a bounded window around the coarse position.
- Confidence via `z_score` = (peak - mean) / std of the correlation surface.
- If `z_score < min_z`, falls back to a whole-file search or skips the clip.
- Handles negative offsets and pre-roll trimming.

**Key files:**
- `multicam_mlt.py` (single file, 515 lines)

**What is useful:**
- The **coarse + fine** two-stage pipeline is exactly what we want.
- Using **creation-time metadata** as a coarse hint is powerful for multicam but depends on metadata accuracy.
- The `z_score` confidence metric is simple and effective.
- Bounded search window (`--window 120`) and limited correlation duration (`--max-correlate 90`) are good performance controls.
- Sample-rate default 8 kHz for analysis is a sensible speed/accuracy tradeoff.

**Limitations:**
- Single master reference; all clips compared only to the master.
- No graph/global consistency for multiple cameras.
- No drift estimation.
- No handling of AGC, EQ, reverb, or phase.

**Reuse decision:**
- The MIT license is compatible, but the code is shotcut-specific (MLT XML). We will not copy it.
- Adopt the **z_score confidence**, **coarse-to-fine search**, and **creation-time coarse hint** ideas.

## 5. Additional algorithms considered

- **GCC-PHAT** (Generalized Cross-Correlation with Phase Transform): robust to reverberation and spectral coloration because it whitens the cross-spectrum. We already use it and will keep it.
- **Mel-spectral cross-correlation**: used in `audio-offset-finder` (BBC) and in the current `sync_bridge.py`. Good for robustness to EQ and moderate reverb.
- **Peak ratio / second-peak test**: standard in speaker verification and TDOA; we will adopt for confidence.
- **Weighted least-squares graph optimization**: standard in sensor network localization; we will adopt for multi-clip global consistency.
- **Drift estimation via windowed GCC-PHAT + linear regression**: standard in long-audio synchronization (e.g., speech archives, cassette digitization); we will implement.

## Summary of algorithm strategy

| Capability | Source of idea | Implementation approach |
|---|---|---|
| Coarse-to-fine search | synaudio, shotcut-multicam-sync | Downsampled/feature correlation first, then GCC-PHAT on a bounded high-rate window |
| Bounded search / partial extraction | shotcut-multicam-sync | `ffprobe` metadata + `-ss`/`-t` windowing in ffmpeg |
| GCC-PHAT | DSP literature / current code | numpy FFT-based PHAT with sub-sample parabolic interpolation |
| Mel-spectral correlation | BBC audio-offset-finder / current code | log-mel STFT, per-band standardization, GCC-PHAT across frames |
| Confidence (z_score, peak ratio) | shotcut-multicam-sync, TDOA literature | Pearson + peak/second-peak + overlap fraction |
| Multi-clip grouping | synaudio graph, sensor localization | Full pairwise graph + weighted least-squares global optimization |
| Drift estimation | Cassette/digital archiving literature | Windowed fine offsets + linear regression |
| Creation-time coarse hint | shotcut-multicam-sync | Use `ffprobe` `creation_time`/`com.apple.quicktime.creationdate` when available |
