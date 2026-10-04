"""
Tests for the Dive In audio analysis.

Synthetic audio is used throughout because it has a right answer: a kick placed
at exactly 2.0s must be detected at exactly frame 60. Real mixes can only tell
you whether it "looks about right".
"""
import numpy as np
import pytest

import generate_video as gv
from app.divein.audio import analyse, decode_excerpt, smooth_envelope
from app.divein.config import merge_config

SR = 44100
FPS = 30


def kick(sr=SR, length=0.15, freq=55.0):
    t = np.arange(int(sr * length)) / sr
    # A real kick sweeps down in pitch and dies away fast.
    sweep = freq * (1 + 2.0 * np.exp(-t * 40))
    phase = 2 * np.pi * np.cumsum(sweep) / sr
    return np.sin(phase) * np.exp(-t * 18)


def hat(rng, sr=SR, length=0.04):
    noise = rng.normal(size=int(sr * length))
    # Crude high-pass: difference of neighbours removes the low end.
    noise = np.diff(noise, prepend=0)
    return noise * np.exp(-np.arange(noise.size) / (sr * 0.01)) * 0.3


def beat(seconds=6.0, kicks_at=None, hats=True, pad_level=0.05, sr=SR):
    """A stereo test loop with kicks at known times."""
    rng = np.random.default_rng(1)
    n = int(seconds * sr)
    mono = pad_level * np.sin(2 * np.pi * 330 * np.arange(n) / sr)
    if kicks_at is None:
        kicks_at = np.arange(0.5, seconds - 0.2, 0.5)      # 120 bpm
    k = kick(sr)
    for t in kicks_at:
        i = int(t * sr)
        mono[i:i + k.size] += 0.8 * k[: max(0, min(k.size, n - i))]
    if hats:
        for t in np.arange(0.25, seconds - 0.1, 0.5):         # off-beat hats
            h = hat(rng, sr)
            i = int(t * sr)
            mono[i:i + h.size] += h[: max(0, min(h.size, n - i))]
    return np.stack([mono, mono], axis=1).astype(np.float32), list(kicks_at)


@pytest.fixture
def cfg():
    return merge_config({})


# ── per-frame shape ─────────────────────────────────────────────────────
def test_there_is_one_value_per_video_frame(cfg):
    samples, _ = beat(4.0)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    assert len(result["gap"]) == 4 * FPS
    for key in ("rms_db", "level", "kick", "gap", "glow", "twitch_x", "twitch_y"):
        assert len(result[key]) == 4 * FPS, key


# ── kick detection ──────────────────────────────────────────────────────
def test_kicks_are_found_on_the_right_frames(cfg):
    samples, times = beat(6.0)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    found = np.nonzero(result["kick"])[0]
    expected = [int(round(t * FPS)) for t in times]
    assert len(found) == len(expected), (found, expected)
    for f, e in zip(found, expected):
        assert abs(f - e) <= 1, f"kick at frame {e} detected at {f}"


def test_hi_hats_alone_are_not_mistaken_for_kicks(cfg):
    samples, _ = beat(6.0, kicks_at=[], hats=True)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    assert result["kick"].sum() == 0


def test_kicks_closer_than_the_minimum_interval_count_once(cfg):
    samples, _ = beat(4.0, kicks_at=[1.0, 1.05, 3.0], hats=False)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    assert result["kick"].sum() == 2


# ── the gap ─────────────────────────────────────────────────────────────
def test_the_coil_snaps_open_on_the_kick_frame_itself(cfg):
    """Fast attack: the gap reaches the kick value on the frame the kick lands."""
    samples, _ = beat(4.0, kicks_at=[2.0], hats=False)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    k = int(np.nonzero(result["kick"])[0][0])
    assert result["gap"][k] == pytest.approx(cfg["audio"]["gap_kick"])


def test_the_coil_settles_back_over_the_release_time_not_instantly(cfg):
    samples, _ = beat(4.0, kicks_at=[2.0], hats=False)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    k = int(np.nonzero(result["kick"])[0][0])
    gap = result["gap"]
    # One frame later it's still most of the way open...
    assert gap[k + 1] > cfg["audio"]["gap_kick"] - 4
    # ...and by ~500ms it has largely settled.
    assert gap[k + 15] < cfg["audio"]["gap_kick"] - 6


def test_silence_closes_the_coil_down(cfg):
    samples = np.zeros((SR * 3, 2), dtype=np.float32)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    assert result["gap"].max() == pytest.approx(cfg["audio"]["gap_silence"])
    assert result["kick"].sum() == 0


def test_the_gap_never_leaves_the_configured_range(cfg):
    samples, _ = beat(6.0)
    gap = analyse(samples, SR, FPS, cfg, episode="02.01")["gap"]
    assert gap.min() >= cfg["audio"]["gap_silence"] - 1e-6
    assert gap.max() <= cfg["audio"]["gap_kick"] + 1e-6


def test_one_huge_spike_does_not_flatten_the_rest(cfg):
    """
    Loudness is scaled against the clip's typical range. With plain min/max, a
    single clipped spike would push every other moment towards 'quiet'.
    """
    samples, _ = beat(6.0, kicks_at=[], hats=False, pad_level=0.05)
    samples[: SR * 3] *= 0.2                       # first half quiet
    samples[int(SR * 5.0): int(SR * 5.01)] = 0.99  # one tiny spike
    level = analyse(samples, SR, FPS, cfg, episode="02.01")["level"]
    assert level[100:140].mean() > 0.7, "the loud half should read as loud"
    assert level[10:60].mean() < 0.3, "the quiet half should read as quiet"


def test_glow_rises_with_the_gap(cfg):
    samples, _ = beat(4.0, kicks_at=[2.0], hats=False)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    k = int(np.nonzero(result["kick"])[0][0])
    assert result["glow"][k] == pytest.approx(1.0)
    assert 0.0 <= result["glow"].min()


# ── twitch ──────────────────────────────────────────────────────────────
def test_each_kick_nudges_the_ghost_by_three_to_six_pixels(cfg):
    samples, _ = beat(6.0, hats=False)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    for k in np.nonzero(result["kick"])[0]:
        size = np.hypot(result["twitch_x"][k], result["twitch_y"][k])
        assert 3.0 - 1e-6 <= size <= 6.0 + 1e-6


def test_the_nudge_eases_back_to_nothing_within_the_release(cfg):
    samples, _ = beat(4.0, kicks_at=[2.0], hats=False)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    k = int(np.nonzero(result["kick"])[0][0])
    size = np.hypot(result["twitch_x"], result["twitch_y"])
    release_frames = int(np.ceil(cfg["twitch"]["release_ms"] / 1000 * FPS))
    assert size[k + 1] < size[k]
    assert size[k + release_frames + 1] == 0.0


def test_twitch_can_be_switched_off(cfg):
    cfg["twitch"]["enabled"] = False
    samples, _ = beat(4.0)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    assert not result["twitch_x"].any() and not result["twitch_y"].any()


def test_the_same_episode_gives_the_same_twitches(cfg):
    samples, _ = beat(4.0)
    a = analyse(samples, SR, FPS, cfg, episode="02.01")
    b = analyse(samples, SR, FPS, cfg, episode="02.01")
    c = analyse(samples, SR, FPS, cfg, episode="02.02")
    assert (a["twitch_x"] == b["twitch_x"]).all()
    assert not (a["twitch_x"] == c["twitch_x"]).all()


# ── bars ────────────────────────────────────────────────────────────────
def test_there_is_a_level_per_bar_per_frame(cfg):
    samples, _ = beat(3.0)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    assert result["bands"].shape == (3 * FPS, cfg["glyph"]["bars"]["count"])
    assert result["bands"].min() >= 0 and result["bands"].max() <= 1


# ── smoothing on its own ────────────────────────────────────────────────
def test_smoothing_jumps_up_at_once_and_falls_slowly():
    target = np.array([0, 0, 10, 0, 0, 0, 0, 0], dtype=float)
    out = smooth_envelope(target, attack_frames=1, release_frames=3.0)
    assert out[2] == 10
    assert 0 < out[3] < 10
    assert out[3] > out[4] > out[5]


# ── decoding ────────────────────────────────────────────────────────────
def test_decoding_cuts_out_exactly_the_requested_excerpt(tmp_path):
    path = tmp_path / "mix.wav"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=12", "-ac", "2", "-ar", "44100",
            str(path)], "test mix")
    samples = decode_excerpt(str(path), start=3.0, seconds=5.0, sample_rate=SR)
    assert samples.shape == (5 * SR, 2)
    assert samples.dtype == np.float32
    # ffmpeg's test tone peaks at 1/8 full scale; this only proves "not silence".
    assert np.abs(samples).max() > 0.05


def test_an_excerpt_running_past_the_end_is_padded_with_silence(tmp_path):
    path = tmp_path / "short.wav"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=4", "-ac", "2", "-ar", "44100",
            str(path)], "short mix")
    samples = decode_excerpt(str(path), start=2.0, seconds=5.0, sample_rate=SR)
    assert samples.shape == (5 * SR, 2)
    assert np.abs(samples[-SR:]).max() == 0.0


def test_a_start_beyond_the_end_is_a_clear_error(tmp_path):
    path = tmp_path / "short.wav"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=2", "-ac", "2", "-ar", "44100",
            str(path)], "short mix")
    with pytest.raises(ValueError) as caught:
        decode_excerpt(str(path), start=60.0, seconds=5.0, sample_rate=SR)
    assert "after the end" in str(caught.value)


def test_a_bassline_between_the_kicks_is_not_mistaken_for_kicks(cfg):
    """
    Found on a real house track: off-beat bass notes live in the same 40-120Hz
    band and were being counted as kicks, which held the coil open instead of
    letting it breathe. Real kicks hit roughly twice as hard as those notes.
    """
    samples, times = beat(8.0, hats=True)
    t = np.arange(int(0.12 * SR)) / SR
    pluck = np.sin(2 * np.pi * 70 * t) * np.exp(-t * 25) * 0.35   # sharp bass note
    mono = samples[:, 0].copy()
    for at in np.arange(0.75, 7.6, 0.5):                          # every off-beat
        i = int(at * SR)
        mono[i:i + pluck.size] += pluck[: max(0, min(pluck.size, mono.size - i))]
    samples = np.stack([mono, mono], axis=1).astype(np.float32)
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    found = np.nonzero(result["kick"])[0]
    expected = [int(round(x * FPS)) for x in times]
    assert len(found) == len(expected), (list(found), expected)


def test_quieter_kicks_in_a_quieter_section_still_count(cfg):
    """The comparison is with nearby hits, so a breakdown's softer kicks survive."""
    samples, times = beat(12.0, hats=False)
    samples[: int(SR * 6)] *= 0.4                                 # first half quieter
    result = analyse(samples, SR, FPS, cfg, episode="02.01")
    found = np.nonzero(result["kick"])[0]
    early = [f for f in found if f < 6 * FPS - 10]
    assert len(early) >= len([x for x in times if x < 5.7]) - 1
