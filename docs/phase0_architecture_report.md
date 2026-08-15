# AudioSyncPro — Phase 0 Architecture Report

## 1. Repository structure

```
AudioSyncPro/
├── CSXS/manifest.xml          # CEP extension manifest (XML)
├── README.md                  # Persian/RTL user-facing install guide
├── css/style.css              # Dark, right-to-left CEP panel styling
├── index.html                 # CEP panel HTML
├── js/ui.js                   # DOM/input helpers, presets, progress, logging
├── js/main.js                 # CEP runtime, host bridge loader, Python server IPC
├── jsx/host.jsx               # ExtendScript bridge: clip selection, move, gain
├── libs/CSInterface.js          # Adobe CEP CSInterface shim
├── libs/Vulcan.js             # Adobe CEP Vulcan helpers
└── python/
    ├── sync_bridge.py         # Monolithic Python server (CLI + stdio server)
    ├── requirements.txt       # numpy
    └── dist/
        ├── sync_bridge.exe    # PyInstaller onefile GUI-subsystem build
        └── bin/ffmpeg.exe     # Bundled ffmpeg decoder
```

There is no `package.json`, build system, test harness, or separate Python project layout. Everything is flat and hand-wired.

## 2. Current data flow

1. **Premiere Pro** selects timeline TrackItems.
2. `jsx/host.jsx::host.getSelectedClips()` walks `videoTracks` and `audioTracks`, checks `isSelected()`, and returns a JSON list of clip metadata (`id`, `name`, `mediaPath`, `startSeconds`, `durationSeconds`, `trackIndex`, `clipIndex`, `isAudio`). It also stores the live TrackItem objects in `host.__clipStore`.
3. `js/main.js` launches `python/dist/sync_bridge.exe` once via `window.cep.process.createProcess` with `--server`, reads JSON-line responses.
4. The panel sends `{op: "sync"|"normalize", clips, settings}` to the Python server over stdin.
5. `python/sync_bridge.py` extracts audio with bundled `ffmpeg`, runs a sync algorithm, and returns a `move`/`gain` plan.
6. `js/main.js` calls `host.applyPlan()` with the plan; ExtendScript performs `overwriteClip` / `clip.move` / `setClipGain`.

## 3. Current Python sync engine

All DSP code lives in one 981-line file.

### Audio extraction
- `extract_audio(ffmpeg_path, media_path, sample_rate, sample_seconds)`
- Uses `ffmpeg` to decode to mono `f32le` PCM, first `sample_seconds` only.
- No media inspection before decode. No channel selection. No stream mapping.
- No partial/read-window support beyond `-t`.

### Preprocessing
- `pre_emphasis` (high-pass-ish filter).
- `normalize_signal` (zero mean, unit std).
- Manual Hann window, manual mel-filterbank, manual magnitude STFT.
- All numpy-only; no librosa/scipy.

### Pairwise synchronization
- Two methods:
  1. `gcc_phat` on raw PCM with pre-emphasis + normalization + windowing.
  2. `gcc_phat_spectral` on mel-spectrogram, cross-correlating each mel band and summing coherently.
- `compare_pair` picks the higher Pearson confidence of the two.
- Search range limited by `maxOffsetSeconds`.
- Sub-sample refinement via parabolic interpolation around the GCC peak.
- Confidence = absolute Pearson correlation at the integer sample lag.

### Multiclip grouping
- `find_groups` builds a full pairwise `corr_matrix`.
- Edges exist where `confidence >= matchThreshold`.
- Connected components become groups.
- Reference per group is the clip with the highest sum of confidences.
- `get_path_offset` uses BFS over the graph to fill in edges that did not pass the threshold directly (path-sum of offsets).

### Timeline plan
- `build_plan` assigns a new track index per group member (`next_track` counter), then places orphans end-to-end after the last group end.
- `newStartSeconds` is `ref_start - member_offset`, shifted to avoid negative starts.
- No global consistency optimization. No drift estimation.

### Normalize
- `detect_volume` via `ffmpeg volumedetect`.
- `gain_for_normalization` computes `target_db - max_volume_db`.

## 4. Strengths

- Standalone, no Python/pip/ffmpeg install required on user machine.
- Uses a persistent JSON-line server, avoiding repeated process spawn.
- Already has two complementary correlation strategies (raw GCC-PHAT + mel-spectral GCC-PHAT).
- Handles audio extraction for any file ffmpeg understands.
- Preserves clip in/out points when moving across tracks.

## 5. Weaknesses and risks

### Architectural
- **Single monolithic file** with no separation of concerns (decoder, DSP, sync, plan, server).
- No type definitions or typed interfaces.
- No test suite, no benchmarks, no regression harness.
- No dependency/version pinning beyond `requirements.txt`.
- `sync_bridge.py` is duplicated inside `lutcodex/AudioSyncPro/...` in the same org.

### Synchronization accuracy
- **Windowing bug in GCC-PHAT**: `ref` and `tgt` are windowed with a Hann window *after* normalization, but the windows applied are of length `ref_len` and `tgt_len` respectively. This zeroes the edges of the *longer* signal unevenly if lengths differ.
- **Confidence is Pearson on raw samples at the integer lag**, not a true GCC-PHAT peak prominence or peak-to-second-peak ratio. A strong noise peak can score high if it accidentally correlates.
- **No peak-ratio / second-peak test**: no rejection of aliasing/ambiguity.
- **No coarse-to-fine search**: full FFT at the configured sample rate. Large offsets with long files require large FFTs and lots of memory.
- **No drift estimation**: assumes a single fixed offset. Long recordings from independent clocks will fail.
- **No explicit handling of non-overlap**: if clips only partially overlap, the Pearson score is computed over the overlap only, but no diagnostic reports how much overlap exists.
- **No per-channel logic**: always mixes to mono. Phase differences or multi-channel sources may be handled poorly.
- **Mel-spectral GCC-PHAT uses `np.log10` on power**, which can be dominated by low-energy frames; no compression/PCEN.
- **Path offset accumulation** in `get_path_offset` can propagate error across a chain.

### Performance
- Every pair is compared with full-length extraction up to `sampleSeconds`.
- No caching of extracted audio or features.
- STFT is computed with a Python loop for the mel filterbank; for 1024 FFT it is fine, but not scalable.
- `extract_audio` reads `stderr` into memory even on success; large stderr not truncated.
- No streaming/chunked decoding; cannot handle multi-hour files without reading the whole window.

### Premiere / bridge
- Track assignment uses a global `next_track` counter that does **not** distinguish video vs audio tracks. `applyPlan` interprets `newTrackIndex` per `isAudio`, but if a group contains both video and audio clips the indexing can place audio on high video tracks or vice-versa.
- `getClipDurationSeconds` falls back to `inPoint/outPoint` with possible `NaN`.
- `projectItem.setInPoint(..., 4)` uses a hard-coded `4` constant; meaning is undocumented and may be version-specific.
- `host.applyPlan` sorts by `(trackIndex, -clipIndex)` but uses stored clip objects, so index shifts are less critical, but it still does not validate that stored objects are still valid.
- Gain adjustment uses a simple `volume` property name search; no per-channel volume.

### Robustness
- `extract_audio` raises if samples contain non-finite values, which can happen with corrupt media; no fallback.
- No validation of sample rate, channel count, or timebase.
- No handling of silence, pure tone, or highly repetitive signals where GCC-PHAT peaks are ambiguous.
- Settings (`sampleRate`, `sampleSeconds`, etc.) are passed raw; no validation of `maxOffset` vs `sampleSeconds`.

## 6. Recommended target architecture

A modular pipeline:

```
Premiere JSX → Clip metadata / TrackItem handles
      ↓
CEP JS (main.js) → server lifecycle + request/response
      ↓
Python Sync API
      ↓
Decoder layer  (ffmpeg partial read, metadata, channel selection)
      ↓
Preprocessor   (resample, A-weighting/whitening, VAD/activity mask)
      ↓
Feature layer  (mono mix strategy, mel-spectrogram, onset envelope)
      ↓
Coarse sync    (downsampled energy/envelope correlation or FFT-GCC)
      ↓
Fine sync      (GCC-PHAT at full rate, parabolic interpolation)
      ↓
Confidence     (peak ratio, Pearson, overlap length, SNR estimate)
      ↓
Outlier reject / graph consistency / global offset optimization
      ↓
Drift analysis (optional, linear regression over windows)
      ↓
Plan builder   (track assignment, move/gain operations)
      ↓
Diagnostics    (JSON report of every stage)
```

Key decisions to make in Phase 1/2:

1. Replace full-length read with **partial windowed read** around an expected sync region.
2. Add **media inspection** (`ffprobe`) to obtain sample rate, channels, duration, timebase.
3. Implement **robust peak detection** (peak ratio / prominence) and a separate **Pearson confidence** over the actual overlap.
4. Add **global graph optimization** instead of greedy connected components.
5. Add **drift estimation** over multiple windows where overlap is long enough.
6. Add a **benchmark harness** that generates synthetic offsets, noise, reverb, EQ, drift.
7. Separate source code into `src/` modules with typed interfaces.

## 7. External research needed

- `synaudio` — cross-correlation based browser/node sync; multi-clip strategy.
- `AudioAlign` — DTW / fingerprinting for long-form alignment.
- `skelly_synchronize` — multicam sync via audio.
- `shotcut-multicam-sync` — practical multicam workflow.
- General GCC-PHAT, PCEN, spectral whitening, onset detection literature.

## 8. License and origin audit status

- Current project has no `LICENSE` file and no `THIRD_PARTY_NOTICES`.
- `ffmpeg.exe` is bundled; its license is LGPL/GPL and must be documented.
- Any external code reuse must be recorded before it is merged.
