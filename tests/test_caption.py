"""
Tests for the burnt-in caption — the track title and artist drawn on every video,
bottom-left, static (it does not spin with the record).
"""
from PIL import Image, ImageDraw

import generate_video as gv


def _blank(cfg):
    return Image.new("RGBA", (cfg.canvas_w, cfg.canvas_h), (0, 0, 0, 0))


def test_caption_sits_bottom_left():
    cfg = gv.RenderConfig()
    layer = _blank(cfg)
    gv.draw_caption(layer, "Blue Monday", "Datassette", cfg)
    box = layer.getchannel("A").getbbox()
    assert box is not None, "caption drew nothing"
    left, top, right, bottom = box
    assert left < cfg.canvas_w * 0.15      # hugs the left edge
    assert bottom > cfg.canvas_h * 0.85    # sits near the bottom
    assert top > cfg.canvas_h * 0.70       # nothing drawn up top


def test_caption_can_be_switched_off():
    cfg = gv.RenderConfig(caption=False)
    layer = _blank(cfg)
    gv.draw_caption(layer, "Blue Monday", "Datassette", cfg)
    assert layer.getchannel("A").getbbox() is None


def test_no_caption_without_track_or_artist():
    cfg = gv.RenderConfig()
    layer = _blank(cfg)
    gv.draw_caption(layer, "", "", cfg)
    assert layer.getchannel("A").getbbox() is None


def test_long_title_wraps_within_the_line_limit():
    cfg = gv.RenderConfig(caption_max_lines=2)
    draw = ImageDraw.Draw(_blank(cfg))
    long_title = ("HERE'S A REALLY LONG TRACK TITLE TO SEE HOW IT LOOKS "
                  "IF IT WRAPS TWO LINES")
    font, lines = gv._fit_caption(draw, long_title, cfg,
                                  max_width=cfg.canvas_w - 120,
                                  max_lines=2, start_size=48)
    assert 1 < len(lines) <= 2, f"expected 2 lines, got {len(lines)}"


def test_caption_stays_inside_the_canvas():
    cfg = gv.RenderConfig()
    layer = _blank(cfg)
    gv.draw_caption(layer, "HERE'S A REALLY LONG TRACK TITLE TO SEE HOW IT WRAPS "
                           "ONTO TWO LINES NICELY", "SOME ARTIST NAME", cfg)
    left, top, right, bottom = layer.getchannel("A").getbbox()
    assert left >= 0 and top >= 0
    assert right <= cfg.canvas_w and bottom <= cfg.canvas_h


def test_static_layer_combines_overlay_and_caption():
    cfg = gv.RenderConfig(overlay_path="assets/overlay.png")
    layer = gv.build_static_layer(cfg, "Blue Monday", "Datassette")
    box = layer.getchannel("A").getbbox()
    # Overlay content (top-left logo) + caption (bottom-left) => spans most of the height.
    assert box[1] < cfg.canvas_h * 0.2
    assert box[3] > cfg.canvas_h * 0.85
