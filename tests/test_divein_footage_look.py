"""
Tests for the footage look: black and white, contrast, grain, darkened edges
and the ghost, drawn onto each frame of camera footage.

They use the photo clips' own settings and drawing maths, so a carousel of
photo and footage clips looks like one set.
"""
import numpy as np
import pytest

from app.divein.config import merge_config
from app.divein.footage_look import FootageLook

W, H = 216, 270            # small frames keep the tests quick; the maths is size-free
ALL = {"bw": True, "contrast": True, "grain": True, "vignette": True, "ghost": True}
NONE = {k: False for k in ALL}


def look(options, analysis=None):
    return FootageLook(merge_config({}), "02.01", W, H, options, analysis)


def flat(level, colour=(1.0, 1.0, 1.0)):
    rgb = np.ones((H, W, 3), np.float32) * np.array(colour, np.float32) * level
    return np.clip(np.round(rgb * 255), 0, 255).astype(np.uint8)


def test_with_everything_off_the_footage_is_untouched():
    frame = (np.random.default_rng(1).random((H, W, 3)) * 255).astype(np.uint8)
    assert np.array_equal(look(NONE).frame(0, frame), frame)


def test_black_and_white_takes_out_the_colour():
    out = look({**NONE, "bw": True}).frame(0, flat(0.8, (1.0, 0.2, 0.1))).astype(int)
    assert np.abs(out[..., 0] - out[..., 1]).max() <= 1
    assert np.abs(out[..., 1] - out[..., 2]).max() <= 1


def test_without_black_and_white_the_colour_stays():
    out = look({**NONE, "contrast": True}).frame(0, flat(0.8, (1.0, 0.2, 0.1))).astype(int)
    assert out[..., 0].mean() > out[..., 1].mean() + 50


def test_contrast_pushes_light_and_dark_further_apart():
    frame = flat(0.35)
    frame[:, W // 2:] = flat(0.65)[:, W // 2:]
    before = frame[:, W // 2:].mean() - frame[:, :W // 2].mean()
    out = look({**NONE, "contrast": True}).frame(0, frame).astype(float)
    assert out[:, W // 2:].mean() - out[:, :W // 2].mean() > 1.2 * before


def test_the_edges_are_darkened():
    out = look({**NONE, "vignette": True}).frame(0, flat(0.7)).astype(float)
    assert out[5:15, 5:15].mean() < 0.7 * out[H // 3, W // 2].mean()


def test_grain_moves_from_frame_to_frame():
    lk = look({**NONE, "grain": True})
    a, b = lk.frame(0, flat(0.5)).astype(float), lk.frame(10, flat(0.5)).astype(float)
    assert a.std() > 3                                  # there is grain
    assert np.abs(a - b).mean() > 2                     # and it changes


def test_the_ghost_is_a_soft_brighter_echo_beside_the_picture():
    frame = flat(0.1)
    frame[100:140, 80:120] = 255                        # a bright square
    plain = look(NONE).frame(0, frame).astype(float)
    ghosted = look({**NONE, "ghost": True}).frame(0, frame).astype(float)
    cfg = merge_config({})["ghost"]
    # Scaled to this small frame, the echo sits up and to the right of the square.
    k = W / 1080
    x = int(118 + cfg["offset_x"] * k)
    assert ghosted[105:135, x - 2:x + 2].mean() > plain[105:135, x - 2:x + 2].mean() + 10


def test_the_ghost_jumps_on_each_hit_in_the_music():
    frame = flat(0.1)
    frame[100:140, 80:120] = 255
    still = {"twitch_x": np.zeros(10), "twitch_y": np.zeros(10)}
    hit = {"twitch_x": np.r_[np.zeros(5), np.full(5, 6.0)], "twitch_y": np.zeros(10)}
    calm = look({**NONE, "ghost": True}, still)
    jumpy = look({**NONE, "ghost": True}, hit)
    assert np.array_equal(calm.frame(7, frame), jumpy.frame(2, frame))       # no hit yet
    assert not np.array_equal(calm.frame(7, frame), jumpy.frame(7, frame))   # on the hit


def test_the_same_frame_always_comes_out_the_same():
    """Preview and export, any machine: frame 12 is always frame 12."""
    frame = (np.random.default_rng(2).random((H, W, 3)) * 255).astype(np.uint8)
    assert np.array_equal(look(ALL).frame(12, frame), look(ALL).frame(12, frame))


def test_frames_drawn_at_the_same_time_come_out_exactly_as_one_at_a_time():
    """The export draws several frames at once for speed: it mustn't change a single pixel."""
    from concurrent.futures import ThreadPoolExecutor
    frames = [(np.random.default_rng(i).random((H, W, 3)) * 255).astype(np.uint8) for i in range(6)]
    hits = {"twitch_x": np.r_[np.zeros(20), np.full(20, 5.0)], "twitch_y": np.zeros(40)}
    serial = look(ALL, hits)
    one_by_one = [serial.frame(f, frames[f % 6]) for f in range(40)]
    parallel = look(ALL, hits)
    with ThreadPoolExecutor(6) as pool:
        together = list(pool.map(lambda f: parallel.frame(f, frames[f % 6]), range(39, -1, -1)))[::-1]
    assert all(np.array_equal(a, b) for a, b in zip(one_by_one, together))
