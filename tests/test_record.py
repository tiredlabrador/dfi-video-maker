"""
Tests for the record disc itself — the circular mask and the centre spindle hole.
"""
from PIL import Image

import generate_video as gv


def _art(size=400, colour=(200, 30, 30, 255)):
    return Image.new("RGBA", (size, size), colour)


def test_centre_hole_is_punched_out():
    rec = gv.make_record(_art(), diameter=200, hole_diameter=24)
    cx = cy = 100
    # Dead centre must be fully transparent — that's the hole.
    assert rec.getpixel((cx, cy))[3] == 0
    # Well outside the hole (20px out, hole radius is 12) must still be solid.
    assert rec.getpixel((cx + 20, cy))[3] > 200


def test_no_hole_when_diameter_is_zero():
    rec = gv.make_record(_art(), diameter=200, hole_diameter=0)
    assert rec.getpixel((100, 100))[3] == 255


def test_disc_is_still_a_circle():
    rec = gv.make_record(_art(), diameter=200, hole_diameter=24)
    # Corners outside the circle stay transparent.
    assert rec.getpixel((2, 2))[3] == 0
    # A point inside the disc but away from the hole is opaque.
    assert rec.getpixel((100, 40))[3] > 200
