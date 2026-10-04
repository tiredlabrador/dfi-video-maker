"""
Audio analysis for Dive In, done ahead of time.

The whole clip is analysed before a single frame is drawn, so the export is
deterministic and frame-accurate: frame 412 always gets the same gap, whether
it's rendered in a preview or the final export, on any machine.

Everything is numpy — no audio libraries to install.
"""
from __future__ import annotations

import math
import subprocess

import numpy as np

from app.divein.config import rng_for


# ── decoding ─────────────────────────────────────────────────────────────
def decode_excerpt(path: str, start: float, seconds: float,
                   sample_rate: int = 44100) -> np.ndarray:
    """
    Cut `seconds` of stereo audio out of `path`, starting at `start`.

    Only the excerpt is decoded, so a two-hour mix costs no more than a
    25-second clip. If the clip runs past the end of the mix it is padded with
    silence, so the video is always exactly the length asked for.
    """
    command = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}",
               "-t", f"{seconds:.3f}", "-i", path,
               "-f", "f32le", "-ac", "2", "-ar", str(sample_rate), "pipe:1"]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        raise ValueError("That audio file couldn't be read: "
                         + result.stderr.decode(errors="replace").strip()[-300:])
    samples = np.frombuffer(result.stdout, dtype=np.float32).reshape(-1, 2)
    if samples.shape[0] == 0:
        raise ValueError("That clip start is after the end of the mix.")
    wanted = int(round(seconds * sample_rate))
    if samples.shape[0] < wanted:
        pad = np.zeros((wanted - samples.shape[0], 2), dtype=np.float32)
        samples = np.concatenate([samples, pad])
    return np.ascontiguousarray(samples[:wanted])


# ── smoothing ────────────────────────────────────────────────────────────
def smooth_envelope(target: np.ndarray, attack_frames: float,
                    release_frames: float) -> np.ndarray:
    """
    Fast up, slow down — so the coil snaps open and settles back.

    Rising: reaches the target in `attack_frames` (1 = instantly).
    Falling: eases back with a time constant of `release_frames`.
    """
    out = np.empty(len(target), dtype=np.float64)
    if not len(target):
        return out
    decay = math.exp(-1.0 / max(release_frames, 1e-6))
    value = float(target[0])
    for i, goal in enumerate(target):
        if goal > value:
            value = goal if attack_frames <= 1 else value + (goal - value) / attack_frames
        else:
            value = goal + (value - goal) * decay
        out[i] = value
    return out


# ── pieces of the analysis ───────────────────────────────────────────────
def _frame_rms_db(mono: np.ndarray, sr: int, fps: int, n_frames: int) -> np.ndarray:
    per = sr / fps
    out = np.empty(n_frames)
    for f in range(n_frames):
        seg = mono[int(f * per): int((f + 1) * per)]
        rms = math.sqrt(float(np.mean(seg * seg))) if seg.size else 0.0
        out[f] = 20 * math.log10(rms + 1e-10)
    return out


def _normalise(db: np.ndarray, silent: np.ndarray, lo_pct: float,
               hi_pct: float) -> np.ndarray:
    """0..1 against the clip's own typical quiet and loud points."""
    audible = db[~silent]
    if audible.size == 0:
        return np.zeros_like(db)
    lo, hi = np.percentile(audible, [lo_pct, hi_pct])
    if hi - lo < 1.0:                       # essentially flat: call it middling
        level = np.full_like(db, 0.5)
    else:
        level = np.clip((db - lo) / (hi - lo), 0.0, 1.0)
    level[silent] = 0.0
    return level


def _band_filter(mono: np.ndarray, sr: int, low: float, high: float) -> np.ndarray:
    """Keep only `low`..`high` Hz, with soft edges so the filter doesn't ring."""
    spectrum = np.fft.rfft(mono)
    freqs = np.fft.rfftfreq(mono.size, 1.0 / sr)
    taper = 10.0
    gain = np.clip((freqs - (low - taper)) / taper, 0, 1) * \
        np.clip(((high + taper) - freqs) / taper, 0, 1)
    return np.fft.irfft(spectrum * gain, n=mono.size)


def _detect_kicks(mono: np.ndarray, sr: int, fps: int, n_frames: int,
                  cfg: dict) -> np.ndarray:
    """
    Find kick drums: sudden rises in the 40-120 Hz band.

    Works on the low band only, so hi-hats and snares can't trigger it, and
    requires a minimum level so a quiet passage can't either. Kicks closer
    together than `kick_min_interval_ms` count once.
    """
    a = cfg["audio"]
    low = _band_filter(mono, sr, a["kick_low_hz"], a["kick_high_hz"])
    hop, win = 256, 512
    count = max(0, (low.size - win) // hop + 1)
    kicks = np.zeros(n_frames, dtype=bool)
    if count < 3:
        return kicks
    frames = np.lib.stride_tricks.sliding_window_view(low, win)[::hop][:count]
    env = np.sqrt(np.mean(frames * frames, axis=1))
    onset = np.zeros_like(env)
    onset[2:] = np.maximum(0.0, env[2:] - env[:-2])

    # Adaptive threshold: well above what's normal for the surrounding second.
    span = max(1, int(0.5 * sr / hop))
    padded = np.pad(onset, span, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * span + 1)
    threshold = windows.mean(axis=1) + a["kick_sensitivity"] * windows.std(axis=1)

    candidates = np.nonzero((onset > threshold) & (env > a["kick_floor"]))[0]
    min_gap = a["kick_min_interval_ms"] / 1000.0 * sr / hop
    accepted: list[int] = []
    for i in sorted(candidates, key=lambda j: -onset[j]):     # strongest first
        if all(abs(i - j) >= min_gap for j in accepted):
            accepted.append(i)

    for i in accepted:
        seconds = (i * hop + win / 2) / sr
        frame = int(seconds * fps)
        if 0 <= frame < n_frames:
            kicks[frame] = True
    return kicks


def _bands(mono: np.ndarray, sr: int, fps: int, n_frames: int, count: int,
           lo_pct: float, hi_pct: float) -> np.ndarray:
    """Per-frame level in `count` frequency bands, each scaled 0..1."""
    edges = np.geomspace(40, 12000, count + 1)
    n_fft = 2048
    window = np.hanning(n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    per = sr / fps
    padded = np.pad(mono, n_fft)
    db = np.empty((n_frames, count))
    for f in range(n_frames):
        centre = int((f + 0.5) * per) + n_fft
        seg = padded[centre - n_fft // 2: centre + n_fft // 2] * window
        power = np.abs(np.fft.rfft(seg)) ** 2
        for b in range(count):
            mask = (freqs >= edges[b]) & (freqs < edges[b + 1])
            db[f, b] = 10 * math.log10(float(power[mask].sum()) + 1e-12)
    out = np.empty_like(db)
    for b in range(count):
        lo, hi = np.percentile(db[:, b], [lo_pct, hi_pct])
        out[:, b] = np.clip((db[:, b] - lo) / max(hi - lo, 1.0), 0, 1)
    return out


def _twitch(kicks: np.ndarray, fps: int, cfg: dict, episode: str):
    n = len(kicks)
    tx, ty = np.zeros(n), np.zeros(n)
    t = cfg["twitch"]
    if not t["enabled"]:
        return tx, ty
    rng = rng_for(episode, "twitch")
    release = max(1, int(math.ceil(t["release_ms"] / 1000.0 * fps)))
    for k in np.nonzero(kicks)[0]:
        angle = rng.uniform(0, 2 * math.pi)
        size = rng.uniform(t["min_px"], t["max_px"])
        for j in range(release + 1):
            if k + j >= n:
                break
            ease = (1.0 - j / release) ** 2          # eases back, fast then gentle
            tx[k + j] = size * math.cos(angle) * ease
            ty[k + j] = size * math.sin(angle) * ease
    return tx, ty


# ── the whole thing ──────────────────────────────────────────────────────
def analyse(samples: np.ndarray, sr: int, fps: int, cfg: dict,
            episode: str) -> dict:
    """
    Everything the renderer needs, one value per video frame.

    Returns arrays: rms_db, level (0..1), kick (bool), gap, glow (0..1),
    twitch_x, twitch_y, bands (frames x bars), plus the mono samples (for the
    line waveform).
    """
    a = cfg["audio"]
    mono = samples.mean(axis=1).astype(np.float64) if samples.ndim == 2 \
        else samples.astype(np.float64)
    n_frames = int(round(mono.size * fps / sr))

    rms_db = _frame_rms_db(mono, sr, fps, n_frames)
    silent = rms_db < a["silence_db"]
    level = _normalise(rms_db, silent, a["rms_low_pct"], a["rms_high_pct"])
    kicks = _detect_kicks(mono, sr, fps, n_frames, cfg) if not silent.all() \
        else np.zeros(n_frames, dtype=bool)

    target = np.interp(level, [0.0, 0.5, 1.0],
                       [a["gap_low"], a["gap_rest"], a["gap_loud"]])
    target[silent] = a["gap_silence"]
    target[kicks] = a["gap_kick"]
    release_frames = a["release_ms"] / 1000.0 * fps
    gap = smooth_envelope(target, a["attack_frames"], release_frames)
    gap = np.clip(gap, a["gap_silence"], a["gap_kick"])

    glow = np.clip((gap - a["gap_rest"]) / max(a["gap_kick"] - a["gap_rest"], 1e-6),
                   0.0, 1.0)

    bars = cfg["glyph"]["bars"]["count"]
    bands = _bands(mono, sr, fps, n_frames, bars, a["rms_low_pct"], a["rms_high_pct"])
    bands[kicks, 0] = 1.0
    for b in range(bars):
        bands[:, b] = smooth_envelope(bands[:, b], a["attack_frames"], release_frames)
    bands[silent] = 0.0

    tx, ty = _twitch(kicks, fps, cfg, episode)
    return {"rms_db": rms_db, "level": level, "kick": kicks, "gap": gap,
            "glow": glow, "twitch_x": tx, "twitch_y": ty, "bands": bands,
            "mono": mono.astype(np.float32), "sample_rate": sr, "fps": fps}
