# Third-Party Notices

AudioSyncPro bundles and/or is influenced by the following third-party software.

## FFmpeg
- Binary bundled at `python/dist/bin/ffmpeg.exe`.
- License: LGPL/GPL depending on build configuration.
- This project uses FFmpeg for audio decoding and volume analysis only.

## NumPy
- Used for signal processing (FFT, correlation).
- License: BSD-3-Clause.

## PyInstaller
- Used to build the standalone `sync_bridge.exe`.
- License: GPL-2.0 with exception.

## External algorithm research
- **eshaz/synaudio** (LGPL-3.0): studied for multi-clip GCC-PHAT and peak-ratio confidence ideas.
- **protyposis/AudioAlign** (AGPL-3.0): studied for fingerprinting and coarse-to-fine alignment architecture.
- **freemocap/skelly_synchronize** (AGPL-3.0): studied for envelope-based coarse correlation and preprocessing.
- **vitaly-zdanevich/shotcut-multicam-sync** (MIT): studied for FFmpeg-based media extraction and MLT plan generation.

No source code from AGPL/LGPL projects was copied directly; only algorithmic concepts and architecture patterns were evaluated and reimplemented independently in this project.

See `docs/external_repo_comparison.md` and `docs/license_audit.md` for details.
