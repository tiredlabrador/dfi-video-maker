"""
Tests for the artwork QA preview — a contact sheet of the artwork that WOULD be
used, so dodgy embedded covers get spotted before anything is rendered or posted.
"""
from PIL import Image

import generate_video as gv


def _img(colour=(0, 120, 200, 255), size=(500, 500)):
    return Image.new("RGBA", size, colour)


def test_resolve_reports_override_as_the_source(tmp_path):
    art = tmp_path / "override.png"
    _img().save(art)
    cfg = gv.RenderConfig()
    image, source = gv.resolve_artwork("anything.mp3", str(art), cfg)
    assert source == "override"
    assert image.size == (500, 500)


def test_resolve_reports_embedded_as_the_source():
    cfg = gv.RenderConfig()
    image, source = gv.resolve_artwork(
        "0 - Test/02 Datassette - Blue Monday (V4).mp3", None, cfg
    )
    assert source == "embedded"
    assert image.width > 0


def test_resolve_reports_fallback_as_the_source(tmp_path):
    """Audio with no embedded art falls through to the configured fallback."""
    silent = tmp_path / "noart.mp3"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=200:duration=3", "-ac", "2", "-q:a", "9",
            str(silent)], "fixture")
    fb = tmp_path / "fb.png"
    _img((255, 0, 0, 255)).save(fb)
    cfg = gv.RenderConfig(fallback_path=str(fb))
    _, source = gv.resolve_artwork(str(silent), None, cfg)
    assert source == "fallback"


def test_contact_sheet_lays_out_a_grid():
    items = [(_img(), f"Track {i}") for i in range(5)]
    sheet = gv.make_contact_sheet(items, cols=3, thumb=200)
    # 5 items over 3 columns => 2 rows.
    assert sheet.width > 3 * 200
    assert sheet.height > 2 * 200
    assert sheet.mode == "RGB"


def test_contact_sheet_draws_labels():
    plain = gv.make_contact_sheet([(_img(), "")], cols=1, thumb=200)
    labelled = gv.make_contact_sheet([(_img(), "A LABEL HERE")], cols=1, thumb=200)
    def light(im):
        return sum(1 for v in im.convert("L").getdata() if v > 180)
    assert light(labelled) > light(plain) + 100


def test_contact_sheet_handles_a_missing_image():
    """A row that failed to load must not break the sheet."""
    sheet = gv.make_contact_sheet([(None, "BROKEN ROW")], cols=1, thumb=200)
    assert sheet.width > 0 and sheet.height > 0


def test_contact_sheet_with_no_items_is_still_valid():
    sheet = gv.make_contact_sheet([], cols=3, thumb=200)
    assert sheet.width > 0 and sheet.height > 0
