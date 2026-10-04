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
        # The technical detail goes to the Terminal window; the person gets a
        # sentence (ffmpeg's own message includes private file paths).
        print("ffmpeg could not decode:", result.stderr.decode(errors="replace").strip()[-500:])
        raise ValueError("That audio file couldn't be read at that start time.")
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

    # A kick is a sudden jump: the rise must be a real share of the level it
    # reaches. A held bass note's envelope wobbles by a hair, which a purely
    # relative threshold would otherwise count, pumping the coil through a
    # breakdown.
    rising = onset >= a["kick_min_rise"] * np.maximum(env, 1e-9)
    candidates = np.nonzero((onset > threshold) & (env > a["kick_floor"]) & rising)[0]

    # Bass notes share the kick's frequency band. What separates them is how
    # loud they peak: on real tracks, kicks land about twice as loud. So each
    # hit is compared with the strong hits just before it (the upper quartile)
    # and only those in the same league count. Comparing locally rather than
    # across the whole clip lets a quieter section's softer kicks survive.
    if candidates.size:
        back = a["kick_window_s"] * sr / hop
        ahead = a["kick_lookahead_s"] * sr / hop
        after = max(1, int(0.035 * sr / hop))           # the hit's peak, ~35ms on
        peak = np.array([env[i:i + after + 1].max() for i in candidates])
        keep = []
        for i, level in zip(candidates, peak):
            # Mostly look back, as a listener would: a breakdown's softer kicks
            # mustn't be judged against the drop that follows them.
            near = peak[(candidates >= i - back) & (candidates <= i + ahead)]
            if level >= a["kick_relative"] * np.percentile(near, 75):
                keep.append(i)
        candidates = np.array(keep, dtype=int)
    min_gap = a["kick_min_interval_ms"] / 1000.0 * sr / hop
    accepted: list[int] = []
    for i in sorted(candidates, key=lambda j: -onset[j]):     # strongest first
        if all(abs(i - j) >= min_gap for j in accepted):
            accepted.append(i)

    outline = _envelope(low) if accepted else low
    for i in accepted:
        frame = int(round(_attack_start(outline, i, hop, win, sr) * fps))
        if 0 <= frame < n_frames:
            kicks[frame] = True
    return kicks


def _envelope(signal: np.ndarray) -> np.ndarray:
    """The smooth outline of a wave (its Hilbert envelope), without its ripples."""
    n = signal.size
    spectrum = np.fft.fft(signal)
    h = np.zeros(n)
    h[0] = 1
    if n % 2 == 0:
        h[n // 2] = 1
        h[1:n // 2] = 2
    else:
        h[1:(n + 1) // 2] = 2
    return np.abs(np.fft.ifft(spectrum * h))


def _attack_start(outline: np.ndarray, i: int, hop: int, win: int, sr: int) -> float:
    """
    When the hit actually began, in seconds.

    The detector notices a kick a little after it starts (about 18ms on
    average), so look back along the low band's outline for where it first
    reaches half its peak. Measured on synthetic kicks, that lands within
    about a millisecond of the true start.
    """
    a = max(0, (i - 6) * hop)
    b = min(outline.size, i * hop + win)
    seg = outline[a:b]
    if seg.size == 0 or seg.max() <= 0:
        return (i * hop + win / 2) / sr
    first = int(np.argmax(seg >= 0.5 * seg.max()))
    return (a + first) / sr


def _volume_hits(rms_db: np.ndarray, level: np.ndarray, silent: np.ndarray,
                 fps: int, cfg: dict) -> np.ndarray:
    """
    Sudden jumps in overall loudness, for the twitch in volume mode.

    No frequency analysis: a hit is the loudness rising by `hit_rise_db` within
    a couple of frames, while it's reasonably loud. Kicks are the usual cause,
    but anything that slams in counts; slow builds don't.
    """
    a = cfg["audio"]
    n = len(rms_db)
    hits = np.zeros(n, dtype=bool)
    gap = max(1, int(round(a["kick_min_interval_ms"] / 1000.0 * fps)))
    last = -gap
    for f in range(1, n):
        before = rms_db[f - 1] if f < 2 else min(rms_db[f - 1], rms_db[f - 2])
        if (rms_db[f] - before >= a["hit_rise_db"] and level[f] >= a["hit_min_level"]
                and not silent[f] and f - last >= gap):
            hits[f] = True
            last = f
    return hits


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
    twitch_x, twitch_y, and hit (what triggers the twitch).
    """
    a = cfg["audio"]
    mono = samples.mean(axis=1).astype(np.float64) if samples.ndim == 2 \
        else samples.astype(np.float64)
    n_frames = int(round(mono.size * fps / sr))

    rms_db = _frame_rms_db(mono, sr, fps, n_frames)
    silent = rms_db < a["silence_db"]
    level = _normalise(rms_db, silent, a["rms_low_pct"], a["rms_high_pct"])
    by_level = a["drive"] == "level"
    if by_level or silent.all():
        kicks = np.zeros(n_frames, dtype=bool)
    else:
        kicks = _detect_kicks(mono, sr, fps, n_frames, cfg)

    # Volume mode: loudness alone spans the whole range, so the loudest
    # moments open the coil fully. Kick mode keeps the top end for kicks.
    top = a["gap_kick"] if by_level else a["gap_loud"]
    target = np.interp(level, [0.0, 0.5, 1.0], [a["gap_low"], a["gap_rest"], top])
    target[silent] = a["gap_silence"]
    target[kicks] = a["gap_kick"]
    release_frames = a["release_ms"] / 1000.0 * fps
    gap = smooth_envelope(target, a["attack_frames"], release_frames)
    gap = np.clip(gap, a["gap_silence"], a["gap_kick"])

    glow = np.clip((gap - a["gap_rest"]) / max(a["gap_kick"] - a["gap_rest"], 1e-6),
                   0.0, 1.0)

    # The twitch fires on kicks, or in volume mode on sudden loudness jumps.
    hits = _volume_hits(rms_db, level, silent, fps, cfg) if by_level else kicks
    tx, ty = _twitch(hits, fps, cfg, episode)
    return {"rms_db": rms_db, "level": level, "kick": kicks, "hit": hits, "gap": gap,
            "glow": glow, "twitch_x": tx, "twitch_y": ty,
            "sample_rate": sr, "fps": fps}
