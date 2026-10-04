"""
Tests for drawing the hole motif (assets/hole.svg) without an SVG library.

The SVG is one hand-drawn path of ~300 pieces using the "even-odd" fill rule,
where overlapping pieces punch holes in each other. It's drawn natively so it
can be recoloured to any of the brand accents.
"""
import numpy as np
import pytest
from PIL import Image

from app.divein.hole import hole_mask, parse_path, read_svg


def test_simple_paths_parse_into_closed_shapes():
    shapes = parse_path("M0 0L10 0L10 10L0 10Z")
    assert len(shapes) == 1
    assert np.allclose(shapes[0][[0, 1, 2, 3]], [[0, 0], [10, 0], [10, 10], [0, 10]])


def test_horizontal_vertical_and_curve_commands_are_understood():
    shapes = parse_path("M0 0H10V10C10 15 0 15 0 10Z")
    pts = shapes[0]
    assert np.allclose(pts[1], [10, 0]) and np.allclose(pts[2], [10, 10])
    assert pts[:, 1].max() > 13          # the curve bulges below y=10


def test_repeated_coordinates_after_a_move_are_lines():
    shapes = parse_path("M0 0 10 0 10 10Z")
    assert len(shapes[0]) >= 3


def test_even_odd_punches_a_hole():
    """A square inside a square: the inner one is a hole, not filled twice."""
    d = "M0 0H100V100H0Z M25 25H75V75H25Z"
    mask = np.asarray(hole_mask(None, width=100, d=d, view=(100, 100)))
    assert mask[10, 10] > 200            # the ring is filled
    assert mask[50, 50] < 50             # the middle is a hole


def test_the_real_motif_draws_at_the_requested_size():
    mask = hole_mask("assets/hole.svg", width=124)
    w, h = mask.size
    assert w == 124 and h == round(124 * 211 / 350)
    a = np.asarray(mask)
    assert 0.08 < (a > 128).mean() < 0.9, "should be a shape, not empty or solid"


def test_the_svg_size_is_read_from_its_viewbox():
    view, d = read_svg("assets/hole.svg")
    assert view == (350.0, 211.0) and len(d) > 1000
