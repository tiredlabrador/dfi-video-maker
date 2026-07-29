"""
Layout tests — the disc must land exactly where the design says.

Reference design (Figma, 4:5 Instagram post): canvas 1080x1350, disc 830x830
positioned at X=125, Y=212. These numbers are the contract; if the maths or the
defaults drift, these tests fail.
"""
import os
import tempfile

from PIL import Image

import generate_video as gv

DESIGN_CANVAS = (1080, 1350)
DESIGN_DISC = 830
DESIGN_POS = (125, 212)          # top-left corner of the disc on the canvas
TOLERANCE = 2                    # anti-aliased edge wobble


def _rendered_disc_bbox(cfg):
    """Render one frame of a solid disc and report where it actually landed."""
    art = Image.new("RGBA", (400, 400), (255, 0, 0, 255))
    record = gv.make_record(art, cfg.circle_diameter, hole_diameter=0)
    with tempfile.TemporaryDirectory() as d:
        gv.render_frames(record, d, 1, cfg)
        frame = Image.open(os.path.join(d, "00000.png")).convert("RGB")
    # Everything that isn't the black background is the disc.
    mask = frame.point(lambda v: 255 if v > 40 else 0).convert("L")
    return mask.getbbox()


def test_defaults_are_the_four_by_five_design():
    cfg = gv.RenderConfig()
    assert (cfg.canvas_w, cfg.canvas_h) == DESIGN_CANVAS
    assert cfg.circle_diameter == DESIGN_DISC


def test_disc_renders_at_the_designed_position():
    cfg = gv.RenderConfig(caption=False, motion_blur_samples=1)
    left, top, right, bottom = _rendered_disc_bbox(cfg)
    assert abs(left - DESIGN_POS[0]) <= TOLERANCE, f"left was {left}"
    assert abs(top - DESIGN_POS[1]) <= TOLERANCE, f"top was {top}"
    assert abs(right - (DESIGN_POS[0] + DESIGN_DISC)) <= TOLERANCE, f"right was {right}"
    assert abs(bottom - (DESIGN_POS[1] + DESIGN_DISC)) <= TOLERANCE, f"bottom was {bottom}"


def test_disc_clears_the_caption_area():
    """The disc must not overlap the burnt-in caption at the bottom."""
    cfg = gv.RenderConfig(caption=False, motion_blur_samples=1)
    _, _, _, bottom = _rendered_disc_bbox(cfg)
    caption_top = cfg.canvas_h - cfg.caption_margin - int(cfg.canvas_h * 0.045) * 3
    assert bottom < caption_top, "disc overlaps the caption block"


def test_square_format_still_works():
    """Changing canvas + disc + overlay should still produce a centred square."""
    cfg = gv.RenderConfig(canvas_h=1080, circle_diameter=710, disc_offset_y=-40,
                          caption=False, motion_blur_samples=1)
    left, top, right, bottom = _rendered_disc_bbox(cfg)
    assert abs(left - (1080 - 710) // 2) <= TOLERANCE
    assert abs((right - left) - 710) <= TOLERANCE
    assert abs((bottom - top) - 710) <= TOLERANCE


def test_mismatched_overlay_shape_warns(capsys):
    """A square overlay on a 4:5 canvas would be stretched — warn, don't stay silent."""
    cfg = gv.RenderConfig(overlay_path="assets/overlay-square.png")   # 1080x1080
    gv.load_overlay(cfg)                                              # canvas is 4:5
    assert "STRETCHED" in capsys.readouterr().out


def test_matching_overlay_shape_is_quiet(capsys):
    cfg = gv.RenderConfig(overlay_path="assets/overlay-portrait.png")  # 1080x1350
    gv.load_overlay(cfg)
    assert "STRETCHED" not in capsys.readouterr().out
