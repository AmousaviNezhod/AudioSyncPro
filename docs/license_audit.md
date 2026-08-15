# AudioSyncPro — License and Code Origin Audit

## 1. Code currently in the AudioSyncPro repository

- `js/main.js`, `js/ui.js`, `jsx/host.jsx`, `python/sync_bridge.py`, `index.html`, `css/style.css`, `CSXS/manifest.xml`:
  - Authored during this project.
  - No external source code copied into these files except:
    - `CSInterface.js` and `Vulcan.js` are Adobe CEP sample library files (standard Adobe SDK, BSD-style).
- Bundled binary:
  - `python/dist/bin/ffmpeg.exe` is the upstream FFmpeg binary. FFmpeg is primarily LGPL-2.1+/GPL-2.0+ depending on configure options. The bundled build used here is a LGPL build without GPL encoders (verify with `ffmpeg -version`).

## 2. External repositories studied

| Repository | License | Can we copy code? | Notes |
|---|---|---|---|
| synaudio (eshaz/synaudio) | LGPL-3.0 | No for static binary | Copyleft; would affect PyInstaller onefile. We are using only the *ideas* (granularity search, graph grouping). No code copied. |
| AudioAlign (protyposis/AudioAlign) | AGPL-3.0 | No | Strong copyleft and network clause. Only studied architecture/fingerprinting concepts. No code copied. |
| skelly_synchronize (freemocap/...) | AGPL-3.0 | No | Strong copyleft. Only studied workflow. No code copied. |
| shotcut-multicam-sync (vitaly-zdanevich/...) | MIT | Yes, but we are not using it | MIT-compatible. We adopted the *z_score confidence* and *coarse/fine* ideas independently. No code copied. |
| BBC audio-offset-finder (not cloned) | MIT | Idea only | Mel-spectral cross-correlation concept. No code copied. |

## 3. Third-party dependencies

- `numpy` — BSD-3-Clause. Safe to bundle.
- `ffmpeg` — LGPL/GPL (see above). A `THIRD_PARTY_NOTICES` file must accompany any distribution containing the binary.
- `pyinstaller` — GPL with a linking exception for generated binaries. The generated `sync_bridge.exe` is a derivative of PyInstaller bootloader (GPL). This is acceptable for distribution as long as source and bootloader license are noted.

## 4. Recommendations before release

1. Add a top-level `LICENSE` file for AudioSyncPro (proprietary or chosen by the owner).
2. Add `THIRD_PARTY_NOTICES` listing:
   - NumPy (BSD-3-Clause)
   - FFmpeg (LGPL-2.1+ or GPL-2.0+, with a link to source)
   - PyInstaller bootloader (GPL)
   - Adobe CEP CSInterface/Vulcan libraries
3. If the FFmpeg build is later switched to a GPL build, the `THIRD_PARTY_NOTICES` must change accordingly.
4. Never copy verbatim code from AGPL/LGPL repositories into the project source or the PyInstaller bundle.
5. Algorithmic ideas, mathematical formulas, and architectural concepts are not copyrightable and can be reimplemented independently.
