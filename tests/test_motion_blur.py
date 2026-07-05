"""
Tests for the 180-degree-shutter motion blur.

Motion blur = averaging the record across the slice of rotation the shutter is
open for. We prove it by rendering a hard black/white edge: with blur switched
off the edge stays crisp (almost no intermediate greys); with blur on, the
sweeping edge smears into a band of intermediate greys.
"""
import os
import tempfile

from PIL import Image

import generate_video as gv


def _split_record(size=120):
    """Opaque record: left half white, right half black — a hard vertical edge."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    for x in range(size // 2):
        for y in range(size):
            im.putpixel((x, y), (255, 255, 255, 255))
    return im


def _midgrey_count(frame):
    """How many pixels are an in-between grey (i.e. blended, not pure b/w)."""
    grey = frame.convert("L")
    return sum(1 for v in grey.getdata() if 60 < v < 195)


def _first_frame(frames_dir):
    return Image.open(os.path.join(frames_dir, "00000.png")).convert("RGB")


def _render(cfg, num_frames=12):
    with tempfile.TemporaryDirectory() as d:
        gv.render_frames(_split_record(), d, num_frames, cfg)
        return _first_frame(d)


def test_blur_off_keeps_edge_crisp():
    cfg = gv.RenderConfig(canvas_w=120, canvas_h=120, bg_colour="black",
                          motion_blur_samples=1)
    assert _midgrey_count(_render(cfg)) < 60


def test_blur_on_smears_the_edge():
    sharp = gv.RenderConfig(canvas_w=120, canvas_h=120, bg_colour="black",
                            motion_blur_samples=1)
    blurred = gv.RenderConfig(canvas_w=120, canvas_h=120, bg_colour="black",
                              motion_blur_samples=9, shutter_fraction=0.5)
    sharp_greys = _midgrey_count(_render(sharp))
    blur_greys = _midgrey_count(_render(blurred))
    # Blur should add a clear band of intermediate greys along the swept edge.
    assert blur_greys > sharp_greys + 200
