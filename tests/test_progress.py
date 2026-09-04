"""
Tests for the optional progress callback on the render engine.

A render takes several seconds. The UI needs to show honest progress rather than
an indeterminate spinner, so the engine reports how far through it is. The hook
is optional: every existing caller (including the Colab notebook) passes nothing
and must keep working unchanged.
"""
import inspect

import pytest
from PIL import Image

import generate_video as gv


def test_render_video_still_works_without_a_progress_callback():
    """The notebook calls this with no progress argument. It must stay valid."""
    signature = inspect.signature(gv.render_video)
    assert signature.parameters["progress"].default is None


def test_stream_render_progress_is_optional_too():
    signature = inspect.signature(gv.stream_render)
    assert signature.parameters["progress"].default is None


def test_progress_is_reported_between_zero_and_one_and_never_goes_backwards():
    cfg = gv.RenderConfig(canvas_w=80, canvas_h=100, circle_diameter=60,
                          clip_length_seconds=1, spin_period_seconds=1, fps=4)
    record = gv.make_record(Image.new("RGB", (60, 60), "red"), 60, 4)
    seen = []
    # iter_frames is a generator: it does nothing until it is consumed.
    list(gv.iter_frames(record, 4, cfg, None, progress=lambda f: seen.append(f)))
    assert seen, "iter_frames reported no progress at all"
    assert all(0.0 <= f <= 1.0 for f in seen)
    assert seen == sorted(seen)


def test_progress_reaches_the_end():
    cfg = gv.RenderConfig(canvas_w=80, canvas_h=100, circle_diameter=60,
                          clip_length_seconds=1, spin_period_seconds=1, fps=4)
    record = gv.make_record(Image.new("RGB", (60, 60), "red"), 60, 4)
    seen = []
    list(gv.iter_frames(record, 4, cfg, None, progress=lambda f: seen.append(f)))
    assert seen[-1] == pytest.approx(1.0)


def test_iter_frames_yields_the_same_pixels_with_and_without_progress():
    """The hook must observe the render, never change it."""
    cfg = gv.RenderConfig(canvas_w=80, canvas_h=100, circle_diameter=60,
                          clip_length_seconds=1, spin_period_seconds=1, fps=4)
    record = gv.make_record(Image.new("RGB", (60, 60), "red"), 60, 4)
    plain = [f.tobytes() for f in gv.iter_frames(record, 4, cfg, None)]
    hooked = [f.tobytes() for f in
              gv.iter_frames(record, 4, cfg, None, progress=lambda f: None)]
    assert plain == hooked
