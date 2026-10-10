"""
Roughly where a DJ mix moves from one record to the next.

A record holds its key and its sound for minutes; a transition is where both
shift and stay shifted. So the mix is described every half second by which
notes are sounding (its chroma: twelve values, one per note of the scale) and
how its energy spreads from bass to treble. Then, at each moment, the half
minute before is compared with the half minute after. Chord changes inside a
record average out over that span; a new record doesn't.

It's rough on purpose: blends run for 16-64 bars, and the marker lands near
their middle. Good enough to say "something changes here, worth a look".
"""
from __future__ import annotations

import subprocess

import numpy as np

SR = 8000                     # notes up to 4kHz: all the harmony that matters
HOP = SR // 2                 # a description every half second
WIN = 4096
SIDE = 60                     # compare 30s before with 30s after (in hops)
MIN_GAP = 150                 # transitions at least 75s apart (in hops)
MIN_SECONDS = 120.0
CHROMA_REF = 0.05             # a clear change of key/harmony
BANDS_REF = 0.9               # a clear change of sound (in standard deviations)


def decode(path: str) -> np.ndarray:
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
                             "-ar", str(SR), "-f", "f32le", "pipe:1"], capture_output=True)
    if result.returncode != 0:
        raise ValueError("That audio file couldn't be read.")
    return np.frombuffer(result.stdout, dtype=np.float32)


def _features(audio: np.ndarray):
    """Per half second: chroma (n, 12), band levels (n, 16), overall level (n,)."""
    n = (audio.size - WIN) // HOP + 1
    freqs = np.fft.rfftfreq(WIN, 1 / SR)
    notes = (freqs >= 50) & (freqs <= 2000)
    pitch = np.zeros(freqs.size, dtype=int)
    pitch[notes] = np.round(12 * np.log2(freqs[notes] / 440.0)).astype(int) % 12
    edges = np.geomspace(40, 3800, 17)
    band = np.digitize(freqs, edges) - 1                 # -1 or 16: outside the bands
    # One table that sums each frequency into its note, its band, and the total.
    sums = np.zeros((freqs.size, 12 + 16 + 1), dtype=np.float32)
    sums[notes, pitch[notes]] = 1
    inside = (band >= 0) & (band < 16)
    sums[inside, 12 + band[inside]] = 1
    sums[:, -1] = 1
    window = np.hanning(WIN).astype(np.float32)
    out = np.zeros((n, sums.shape[1]))
    for first in range(0, n, 512):                       # a few minutes at a time
        count = min(512, n - first)
        idx = (first + np.arange(count))[:, None] * HOP + np.arange(WIN)
        spectrum = np.fft.rfft(audio[idx] * window, axis=1)
        power = (spectrum.real ** 2 + spectrum.imag ** 2).astype(np.float32)
        out[first:first + count] = power @ sums
    chroma, bands, level = out[:, :12], out[:, 12:28], out[:, 28]
    return chroma, np.log(bands + 1e-9), np.log(level + 1e-12)


def _means(x: np.ndarray, side: int):
    """For each moment: the average of `x` over the span before and the span after."""
    total = np.concatenate([np.zeros((1, x.shape[1])), np.cumsum(x, axis=0)])
    i = np.arange(side, x.shape[0] - side + 1)
    before = (total[i] - total[i - side]) / side
    after = (total[i + side] - total[i]) / side
    return i, before, after


def find_transitions(audio: np.ndarray) -> list[float]:
    """Rough transition times, in whole seconds, earliest first."""
    if audio.size < MIN_SECONDS * SR:
        return []
    chroma, bands, level = _features(audio.astype(np.float32, copy=False))
    if level.max() < np.log(1e-6 * WIN):                 # silence throughout
        return []
    loud = level > np.percentile(level, 90) - np.log(1e4)   # within 40dB of the loud parts
    chroma = chroma / (np.linalg.norm(chroma, axis=1, keepdims=True) + 1e-12)
    chroma[~loud] = 0                                    # quiet moments say nothing
    spread = bands[loud].std(axis=0) + 1e-6
    bands = (bands - bands[loud].mean(axis=0)) / spread
    bands[~loud] = 0

    i, c_before, c_after = _means(chroma, SIDE)
    if i.size == 0:
        return []
    cos = (c_before * c_after).sum(axis=1) / (
        np.linalg.norm(c_before, axis=1) * np.linalg.norm(c_after, axis=1) + 1e-12)
    _, b_before, b_after = _means(bands, SIDE)
    sound = np.linalg.norm(b_before - b_after, axis=1) / np.sqrt(bands.shape[1])
    change = (1 - cos) / CHROMA_REF + sound / BANDS_REF
    # Music against silence isn't a change of record: where either side is
    # mostly quiet, there's nothing to compare.
    _, l_before, l_after = _means(loud[:, None].astype(float), SIDE)
    change[(l_before[:, 0] < 0.5) | (l_after[:, 0] < 0.5)] = 0

    # Peaks only (the highest point within half a minute either side), the
    # strongest first, each at least MIN_GAP from any already kept.
    padded = np.pad(change, SIDE, constant_values=-np.inf)
    peak = change >= np.lib.stride_tricks.sliding_window_view(padded, 2 * SIDE + 1).max(axis=1)
    kept: list[int] = []
    for k in np.argsort(-change):
        if change[k] < 1.0:
            break
        if peak[k] and all(abs(k - j) >= MIN_GAP for j in kept):
            kept.append(int(k))
    return sorted(float(round((i[k] * HOP + WIN / 2) / SR)) for k in kept)
