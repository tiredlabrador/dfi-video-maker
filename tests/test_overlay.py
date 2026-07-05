"""
Tests for the static branding overlay (e.g. THE DIG / DON'T FALL IN).

We test at the frame level (fast, no ffmpeg): render_frames must composite a
given overlay image on top of every frame, and must leave the background alone
when no overlay is supplied.
"""
import os
import tempfile

from PIL import Image

import generate_video as gv


def _record(size=60):
    """A stand-in 'record' — a solid opaque square, smaller than the canvas so
    the canvas corners stay background-coloured."""
    return Image.new("RGBA", (size, size), (0, 0, 200, 255))


def _corner_overlay(canvas=100, patch=20):
    """Transparent overlay with an opaque red patch in the top-left corner."""
    ov = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    for y in range(patch):
        for x in range(patch):
            ov.putpixel((x, y), (255, 0, 0, 255))
    return ov


def _first_frame(frames_dir):
    return Image.open(os.path.join(frames_dir, "00000.png")).convert("RGB")


def test_overlay_is_composited_on_top_of_frame():
    cfg = gv.RenderConfig(canvas_w=100, canvas_h=100, bg_colour="black")
    with tempfile.TemporaryDirectory() as d:
        gv.render_frames(_record(), d, 1, cfg, overlay=_corner_overlay())
        frame = _first_frame(d)
    # The overlay's red corner patch must be visible in the rendered frame.
    assert frame.getpixel((5, 5)) == (255, 0, 0)


def test_no_overlay_leaves_background_untouched():
    cfg = gv.RenderConfig(canvas_w=100, canvas_h=100, bg_colour="black")
    with tempfile.TemporaryDirectory() as d:
        gv.render_frames(_record(), d, 1, cfg, overlay=None)
        frame = _first_frame(d)
    # No overlay → the corner is just the black background.
    assert frame.getpixel((5, 5)) == (0, 0, 0)
