# Browser render — measured capability findings

Measured in Chrome 148 against the live origin `https://tiredlabrador.github.io/dfi-video-maker/`
on 2026-09-04. These are test results, not opinions. Re-run before trusting them
on a different machine or browser.

## Codec support (`isConfigSupported`, 1080x1350 @ 30fps)

| Config | hardware | software |
|---|---|---|
| `avc1.640028` H.264 High L4.0 | yes | yes |
| `avc1.4d0028` H.264 Main L4.0 | yes | yes |
| `avc1.42E01E` H.264 Baseline L3.0 | **no** | **no** |
| `mp4a.40.2` AAC-LC 44.1k stereo | yes | — |

Baseline fails on the **level**, not the profile: L3.0 does not cover 1080x1350.
Get the level right in the codec string or the encoder refuses with no useful hint.

## Live encode smoke test

750 frames (= 25s @ 30fps) of 1080x1350, `avc1.640028`, 12 Mbps, prefer-hardware,
drawn on an OffscreenCanvas on the main thread:

- 750 chunks out, first chunk is a keyframe
- 3.46 MB total, valid 36-byte `avcC` decoder description emitted
- 35.4s wall clock (~21 fps) — **conservative floor**: main thread, throttled pane,
  drawing included. Native `stream_render` does the same job in ~8.4s.

## The GitHub Pages constraint

- `window.isSecureContext` = true
- `window.crossOriginIsolated` = **false**
- `SharedArrayBuffer` = **unavailable**

GitHub Pages cannot set the COOP/COEP response headers that cross-origin isolation
requires. So **multi-threaded `ffmpeg.wasm` cannot run here** — only the slow
single-threaded core. This is the decisive argument for WebCodecs and it is not
mentioned in HANDOFF.md.

## Confirmed gotcha: hidden tabs stall

Two benchmark runs timed out in a hidden browser pane. `requestAnimationFrame`
stopped firing entirely and `setTimeout` was clamped. Consequences for the build:

- render in a Web Worker with OffscreenCanvas, not on the main thread
- pace the encoder on `encodeQueueSize`, never on timers or rAF
- warn the operator not to background the tab mid-batch
