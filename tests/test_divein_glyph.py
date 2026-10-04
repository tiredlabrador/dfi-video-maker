"""
Tests for the audio-reactive glyph: the ring coil, the bars and the line.

The rule that matters most: ring shapes are generated once per episode and never
change. Only their spacing moves. Re-randomising per frame would make the
outlines "boil".
"""
import math

import numpy as np
import pytest

from app.divein.config import merge_config
from app.divein.glyph import Glyph

YELLOW = (255, 254, 1)


@pytest.fixture
def cfg():
    return merge_config({})


def ellipse_deviation(points, rx, ry, cx, cy):
    """
    Signed distance from each point to the perfect ellipse, in glyph units.

    Measured as true geometric distance to a densely sampled ellipse. (Scaling
    the "radius ratio" instead exaggerates wobble at the top and bottom of a
    flat ellipse by a factor of rx/ry.)
    """
    t = np.linspace(0, 2 * math.pi, 4000, endpoint=False)
    ex, ey = cx + rx * np.cos(t), cy + ry * np.sin(t)
    d = np.hypot(points[:, :1] - ex, points[:, 1:] - ey)
    nearest = d.min(axis=1)
    inside = np.hypot((points[:, 0] - cx) / rx, (points[:, 1] - cy) / ry) < 1
    return np.where(inside, -nearest, nearest)


def test_there_are_five_rings_of_about_110_points(cfg):
    g = Glyph(cfg, "02.01")
    rings = g.ring_points(gap=9)
    assert len(rings) == 5
    assert all(105 <= len(r) <= 115 for r in rings)


def test_the_rings_are_deliberately_not_perfect_ellipses(cfg):
    g = Glyph(cfg, "02.01")
    rc = cfg["glyph"]["rings"]
    for i, ring in enumerate(g.ring_points(gap=9)):
        cy = rc["bottom_y"] - (4 - i) * 9
        dev = ellipse_deviation(ring, rc["rx"], rc["ry"], rc["centre_x"], cy)
        # Wobbly, but only slightly: high frequency, low amplitude.
        assert 0.15 < dev.std() < 0.9, dev.std()
        assert np.abs(dev).max() < 3.0


def test_ring_shapes_never_change_only_their_spacing_does(cfg):
    g = Glyph(cfg, "02.01")
    closed = g.ring_points(gap=2)
    open_ = g.ring_points(gap=18)
    for i, (a, b) in enumerate(zip(closed, open_)):
        shift = (4 - i) * (18 - 2)
        assert np.allclose(a[:, 0], b[:, 0])
        assert np.allclose(a[:, 1] - shift, b[:, 1])


def test_the_bottom_ring_is_anchored(cfg):
    g = Glyph(cfg, "02.01")
    assert np.allclose(g.ring_points(gap=2)[4], g.ring_points(gap=18)[4])


def test_rings_stack_upwards_by_the_gap(cfg):
    g = Glyph(cfg, "02.01")
    bottom = cfg["glyph"]["rings"]["bottom_y"]
    for gap in (2, 9, 18):
        for i, ring in enumerate(g.ring_points(gap=gap)):
            assert ring[:, 1].mean() == pytest.approx(bottom - (4 - i) * gap, abs=0.4)


def test_each_ring_has_its_own_wobble_and_start_angle(cfg):
    g = Glyph(cfg, "02.01")
    rc = cfg["glyph"]["rings"]
    rings = g.ring_points(gap=9)
    devs = [ellipse_deviation(r, rc["rx"], rc["ry"], rc["centre_x"],
                              r[:, 1].mean()) for r in rings]
    assert not np.allclose(devs[0], devs[1])
    starts = [math.atan2(r[0, 1] - r[:, 1].mean(), r[0, 0] - rc["centre_x"]) for r in rings]
    assert len({round(s, 2) for s in starts}) == 5


def test_the_same_episode_gives_the_same_rings_and_another_does_not(cfg):
    a = Glyph(cfg, "02.01").ring_points(9)
    b = Glyph(cfg, "02.01").ring_points(9)
    c = Glyph(cfg, "02.02").ring_points(9)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))
    assert not all(np.allclose(x, y) for x, y in zip(a, c))


# ── drawing ─────────────────────────────────────────────────────────────
def opaque_colours(tile):
    arr = np.asarray(tile)
    return {tuple(px[:3]) for px in arr[arr[..., 3] > 0]}


@pytest.mark.parametrize("mode", ["rings", "bars", "line"])
def test_every_mode_draws_only_in_brand_yellow(cfg, mode):
    cfg["glyph"]["mode"] = mode
    g = Glyph(cfg, "02.01")
    tile, _ = g.render(g.rest_state())
    assert opaque_colours(tile) == {YELLOW}


def test_none_mode_draws_nothing(cfg):
    cfg["glyph"]["mode"] = "none"
    g = Glyph(cfg, "02.01")
    assert g.render(g.rest_state()) is None


def test_at_rest_the_rings_sit_where_r1_has_them(cfg):
    """Measured from R1: the strong yellow of the coil spans x 905-1026, y 58-117."""
    g = Glyph(cfg, "02.01")
    tile, (x, y) = g.render(g.rest_state())
    alpha = np.asarray(tile)[..., 3] > 128          # the stroke itself, not glow
    ys, xs = np.nonzero(alpha)
    assert abs((x + xs.min()) - 905) <= 3
    assert abs((x + xs.max()) - 1026) <= 3
    assert abs((y + ys.min()) - 58) <= 3
    assert abs((y + ys.max()) - 117) <= 3


def test_an_open_coil_is_taller_and_grows_upwards(cfg):
    g = Glyph(cfg, "02.01")
    rest, (_, y_rest) = g.render(g.rest_state())
    peak, (_, y_peak) = g.render(dict(g.rest_state(), gap=18, glow=1.0))
    def extent(t, y0):
        ys = np.nonzero(np.asarray(t)[..., 3] > 128)[0] + y0
        return ys.min(), ys.max()
    top_r, bot_r = extent(rest, y_rest)
    top_p, bot_p = extent(peak, y_peak)
    assert abs(bot_r - bot_p) <= 1, "the bottom ring must stay put"
    # Gap 9 -> 18 lifts the top ring by 4 x 9 = 36px.
    assert abs((top_r - top_p) - 36) <= 2


def test_the_glow_is_stronger_at_a_peak(cfg):
    g = Glyph(cfg, "02.01")
    calm, _ = g.render(dict(g.rest_state(), glow=0.0))
    hot, _ = g.render(dict(g.rest_state(), glow=1.0))
    def halo(t):
        a = np.asarray(t)[..., 3].astype(int)
        return a[(a > 0) & (a < 200)].sum()
    assert halo(hot) > halo(calm) * 1.1


def test_bars_follow_their_levels(cfg):
    cfg["glyph"]["mode"] = "bars"
    g = Glyph(cfg, "02.01")
    low, (_, y_low) = g.render(dict(g.rest_state(), bands=[0, 0, 0, 0]))
    high, (_, y_high) = g.render(dict(g.rest_state(), bands=[1, 1, 1, 1]))
    def height(t):
        return np.ptp(np.nonzero(np.asarray(t)[..., 3] > 128)[0])
    assert height(high) > height(low) + 30


def test_three_or_four_bars(cfg):
    for count in (3, 4):
        cfg["glyph"]["mode"] = "bars"
        cfg["glyph"]["bars"]["count"] = count
        cfg["glyph"]["bars"]["rest_heights"] = [0.5] * count
        g = Glyph(cfg, "02.01")
        tile, _ = g.render(g.rest_state())
        cols = (np.asarray(tile)[..., 3] > 128).any(axis=0)
        runs = np.count_nonzero(np.diff(cols.astype(int)) == 1) + int(cols[0])
        assert runs == count


def test_the_line_moves_with_the_audio(cfg):
    cfg["glyph"]["mode"] = "line"
    g = Glyph(cfg, "02.01")
    quiet, _ = g.render(dict(g.rest_state(), wave=np.zeros(2000)))
    t = np.arange(2000) / 44100
    loud, _ = g.render(dict(g.rest_state(), wave=np.sin(2 * np.pi * 150 * t)))
    def height(tile):
        return np.ptp(np.nonzero(np.asarray(tile)[..., 3] > 128)[0])
    assert height(loud) > height(quiet) + 15
