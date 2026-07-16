"""
Tests for the fallback artwork card — used when a track has no embedded cover art
and no override. Two flavours: a static PNG, or a generated card with the track
title and artist written on it.
"""
from PIL import Image

import generate_video as gv


def _dark_pixel_count(img):
    grey = img.convert("L")
    return sum(1 for v in grey.getdata() if v < 100)


def test_text_card_writes_something():
    cfg = gv.RenderConfig()
    card = gv.make_fallback_card("Blue Monday", "Datassette", 400, cfg)
    assert card.size == (400, 400)
    # A white card with black text: there must be a decent chunk of dark pixels.
    assert _dark_pixel_count(card) > 200


def test_text_card_is_mostly_background():
    cfg = gv.RenderConfig()
    card = gv.make_fallback_card("Blue Monday", "Datassette", 400, cfg)
    # Sanity: it's a light card, not a black rectangle.
    assert _dark_pixel_count(card) < 400 * 400 * 0.5


def test_long_title_is_shrunk_to_fit_inside_the_disc():
    cfg = gv.RenderConfig()
    long_title = "A Very Long Track Title That Simply Keeps Going And Going"
    card = gv.make_fallback_card(long_title, "Some Very Long Artist Name Here", 400, cfg)
    grey = card.convert("L")
    # Text must stay clear of the edges, or it'd be clipped by the circular mask.
    margin = 40
    for x in range(400):
        assert grey.getpixel((x, 3)) > 200          # nothing at the very top
    for y in range(400):
        assert grey.getpixel((3, y)) > 200          # nothing at the far left
        assert grey.getpixel((396, y)) > 200        # nothing at the far right
