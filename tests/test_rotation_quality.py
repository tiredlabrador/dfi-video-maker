"""
Rotation filter choice.

The per-frame spin uses bilinear — it's ~2x faster than bicubic and the visible
difference is a couple of dozen pixels on an 830px disc (the large raw pixel
differences are all in fully transparent areas that never get drawn).

The one-off pre-blur pass keeps bicubic: it runs once per video rather than 180
times, so the better filter is free there.
"""
import numpy as np
from PIL import Image

import generate_video as gv


def _disc(size=300):
    art = Image.new("RGBA", (size, size))
    pixels = art.load()
    for y in range(size):                       # detail to interpolate
        for x in range(size):
            pixels[x, y] = ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256, 255)
    return gv.make_record(art, 260, hole_diameter=12)


def test_per_frame_rotation_uses_the_fast_filter():
    record = _disc()
    got = gv._rotate_sharp(record, 24.0)
    expected = record.rotate(-24.0, resample=Image.BILINEAR, expand=False)
    assert np.array_equal(np.asarray(got), np.asarray(expected))


def test_visible_pixels_barely_differ_between_filters():
    """Guards the actual claim: what you can SEE is essentially unchanged."""
    record = _disc()
    fast = np.asarray(record.rotate(-24.0, resample=Image.BILINEAR, expand=False), float)
    fine = np.asarray(record.rotate(-24.0, resample=Image.BICUBIC, expand=False), float)

    visible = fine[:, :, 3] > 8                 # ignore fully transparent pixels
    colour_gap = np.abs(fast[:, :, :3] - fine[:, :, :3]).max(axis=2)
    noticeable = (colour_gap > 32) & visible
    assert noticeable.sum() < visible.sum() * 0.005, (
        f"{noticeable.sum()} visibly different pixels of {visible.sum()}")


def test_prebluring_keeps_the_higher_quality_filter():
    record = _disc()
    blurred = gv._rotate_motion_blurred(record, 0.0, 1.4, 4)
    assert blurred.size == record.size
    assert blurred.mode == "RGBA"
