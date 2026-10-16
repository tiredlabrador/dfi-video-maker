"""9:16 Reels, and footage zoomed out over a blurred copy of itself."""
import pytest

from app.divein.config import for_aspect, merge_config
from app.divein.footage_clip import layout, min_zoom


def test_a_reel_is_1080_by_1920_with_the_4_by_5_design_in_its_middle():
    """The middle 4:5 is what the profile grid shows, and clear of the Reels buttons and caption."""
    base = merge_config({})
    reel = for_aspect(base, "9:16")
    assert (reel["canvas"]["width"], reel["canvas"]["height"]) == (1080, 1920)
    down = (1920 - 1350) / 2
    assert reel["logo"]["y"] == base["logo"]["y"] + down
    assert reel["glyph"]["box_y"] == base["glyph"]["box_y"] + down
    assert reel["tape"]["centre_y"] == base["tape"]["centre_y"] + down
    assert reel["name"]["last_baseline"] == base["name"]["last_baseline"] + down
    # The darkening is centred on the same part of the design.
    assert reel["vignette"]["centre_y"] * 1920 == pytest.approx(
        base["vignette"]["centre_y"] * 1350 + down)


def test_4_by_5_is_left_as_it_is():
    base = merge_config({})
    assert for_aspect(base, "4:5") == base


def test_an_unknown_shape_is_refused():
    with pytest.raises(ValueError):
        for_aspect(merge_config({}), "21:9")


def test_tuning_is_moved_with_the_design():
    """A tuned tape position is a 4:5 position: it moves down with everything else."""
    tuned = merge_config({"tape": {"centre_y": 700}})
    assert for_aspect(tuned, "9:16")["tape"]["centre_y"] == 700 + 285


def test_the_reel_leaves_the_settings_it_was_given_alone():
    base = merge_config({})
    for_aspect(base, "9:16")
    assert base["canvas"]["height"] == 1350


# ── framing footage ──────────────────────────────────────────────────────
def test_at_zoom_1_the_footage_fills_the_frame():
    box = layout(1920, 1080, 1080, 1920, 0.5, 0.5, 1.0)
    assert box["dest"] == (0, 0, 1080, 1920)
    assert box["source"][3] == pytest.approx(1080)                 # all of its height
    assert box["source"][2] == pytest.approx(1080 * 1080 / 1920)   # a 9:16 slice of its width
    assert not box["backdrop"]


def test_zoomed_out_all_the_way_the_whole_picture_shows_across_the_frame():
    zoom = min_zoom(1920, 1080, 1080, 1920)
    box = layout(1920, 1080, 1080, 1920, 0.5, 0.5, zoom)
    assert box["source"] == pytest.approx((0, 0, 1920, 1080))
    x, y, w, h = box["dest"]
    assert (x, w) == (0, 1080) and h == pytest.approx(1080 * 1080 / 1920, abs=1)
    assert y == pytest.approx((1920 - h) / 2, abs=1)                # in the middle
    assert box["backdrop"]


def test_part_way_out_the_picture_is_still_framed_left_or_right():
    zoom = (1 + min_zoom(1920, 1080, 1080, 1920)) / 2
    left = layout(1920, 1080, 1080, 1920, 0.0, 0.5, zoom)
    right = layout(1920, 1080, 1080, 1920, 1.0, 0.5, zoom)
    assert left["source"][0] == 0 and right["source"][0] > 0
    assert left["dest"][2] == 1080                                  # still as wide as the frame
    assert left["backdrop"]


def test_it_cannot_zoom_out_beyond_the_whole_picture():
    fit = layout(1920, 1080, 1080, 1920, 0.5, 0.5, min_zoom(1920, 1080, 1080, 1920))
    assert layout(1920, 1080, 1080, 1920, 0.5, 0.5, 0.01) == fit


def test_zooming_out_less_is_needed_for_4_by_5():
    assert min_zoom(1920, 1080, 1080, 1350) > min_zoom(1920, 1080, 1080, 1920)


def test_sizes_come_out_even_for_the_video_encoder():
    box = layout(1920, 1080, 540, 960, 0.3, 0.5, 0.71)
    assert all(v % 2 == 0 for v in box["dest"]) and all(v % 2 == 0 for v in box["size"])


def test_a_phone_video_filmed_upright_is_measured_upright(tmp_path):
    """Phones store the picture sideways with a note to turn it; ffmpeg turns it before framing."""
    import subprocess
    from app.divein.footage_clip import _picture_size
    plain, turned = tmp_path / "plain.mp4", tmp_path / "turned.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x180:d=1",
                    "-c:v", "libx264", str(plain)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", str(plain),
                    "-c", "copy", str(turned)], check=True)
    assert _picture_size(str(plain)) == (320, 180)
    assert _picture_size(str(turned)) == (180, 320)


def test_the_canvas_size_is_left_to_the_shape_buttons():
    from app.divein.api import BadRequest, DiveInService
    import tempfile
    from app.jobs import JobStore
    svc = DiveInService(tempfile.mkdtemp(), JobStore())
    with pytest.raises(BadRequest):
        svc._config({"overrides": {"canvas": {"height": 1920}}})
