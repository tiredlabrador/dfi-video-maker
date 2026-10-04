"""
The audio-reactive coil, top-right.

The coil is the DFI logo's motif — five stacked ellipses, like a spring seen at
an angle. Each ring is hand-wobbly rather than a perfect ellipse, and that
wobble is generated ONCE per episode. Only the spacing between rings moves with
the music. Re-randomising the wobble every frame would make the outlines boil.

Drawing a coil is the slowest part of a frame, so finished drawings are kept
and reused: the spacing is rounded to an eighth of a pixel, so a 25-second clip
needs a hundred or so drawings rather than 750.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.divein.config import rng_for

SUPERSAMPLE = 4          # draw 4x larger then shrink: smooth, anti-aliased lines
GAP_STEP = 1 / 8         # spacing is rounded to this many pixels for reuse


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def accent_rgb(cfg: dict) -> tuple[int, int, int]:
    """The chosen brand accent (yellow, red, green or white) as RGB."""
    return hex_to_rgb(cfg["accents"][cfg["accent"]])


class Glyph:
    def __init__(self, cfg: dict, episode: str):
        self.cfg = cfg
        self.g = cfg["glyph"]
        self.mode = self.g["mode"]
        self.colour = accent_rgb(cfg)
        self._rings = self._make_rings(episode)
        self._drawn: dict = {}

    # ── shapes, made once ────────────────────────────────────────────────
    def _make_rings(self, episode: str) -> list[np.ndarray]:
        rc = self.g["rings"]
        n = int(rc["points"])
        rings = []
        for i in range(int(rc["count"])):
            rng = rng_for(episode, f"ring{i}")
            start = rng.uniform(0, 2 * math.pi)
            theta = start + 2 * math.pi * np.arange(n) / n
            radial = rng.normal(0, rc["radial_sd"], n)
            k = max(1, int(rc["radial_smooth"]))
            if k > 1:                       # light smoothing over neighbours
                radial = sum(np.roll(radial, s) for s in range(-(k // 2), k - k // 2)) / k
            vertical = rng.normal(0, rc["vertical_sd"], n)
            x = rc["rx"] * np.cos(theta)
            y = rc["ry"] * np.sin(theta)
            nx, ny = rc["ry"] * np.cos(theta), rc["rx"] * np.sin(theta)
            norm = np.hypot(nx, ny)
            x = x + radial * nx / norm
            y = y + radial * ny / norm + vertical
            rings.append(np.stack([x, y], axis=1))
        return rings

    def ring_points(self, gap: float) -> list[np.ndarray]:
        """
        Each ring's outline in glyph units, stacked with the given gap and
        anchored at the middle ring, so the coil opens evenly up and down.
        """
        rc = self.g["rings"]
        middle = (len(self._rings) - 1) / 2
        return [shape + np.array([rc["centre_x"], rc["centre_y"] + (i - middle) * gap])
                for i, shape in enumerate(self._rings)]

    def rest_state(self) -> dict:
        """The coil with no music: the at-rest look."""
        return {"gap": self.cfg["audio"]["gap_rest"], "glow": 0.0}

    # ── drawing ──────────────────────────────────────────────────────────
    def render(self, state: dict):
        """Draw the coil. Returns (RGBA tile, (x, y) on the canvas), or None."""
        if self.mode == "none":
            return None
        gap = round(float(state["gap"]) / GAP_STEP) * GAP_STEP
        glow_t = round(float(np.clip(state.get("glow", 0.0), 0, 1)) * 40) / 40
        key = (gap, glow_t)
        if key not in self._drawn:
            if len(self._drawn) > 400:
                self._drawn.clear()
            self._drawn[key] = self._draw(gap, glow_t)
        return self._drawn[key]

    def _draw(self, gap: float, t: float):
        s = self.g["scale"]
        ox, oy = self.g["box_x"], self.g["box_y"]
        shapes = [np.vstack([p, p[:1]]) for p in self.ring_points(gap)]
        stroke = self.g["rings"]["stroke"]

        glow = self.g["glow"]
        blur = glow["blur"] + (glow["peak_blur"] - glow["blur"]) * t
        alpha = glow["alpha"] + (glow["peak_alpha"] - glow["alpha"]) * t

        # Bounds of everything to be drawn, in canvas pixels, with room for glow.
        allpts = np.vstack(shapes)
        margin = stroke * s / 2 + 3 * blur + 2
        x0 = math.floor(ox + allpts[:, 0].min() * s - margin)
        y0 = math.floor(oy + allpts[:, 1].min() * s - margin)
        x1 = math.ceil(ox + allpts[:, 0].max() * s + margin)
        y1 = math.ceil(oy + allpts[:, 1].max() * s + margin)
        w, h = x1 - x0, y1 - y0

        big = Image.new("L", (w * SUPERSAMPLE, h * SUPERSAMPLE), 0)
        draw = ImageDraw.Draw(big)
        width = max(1, int(round(stroke * s * SUPERSAMPLE)))
        for shape in shapes:
            pts = [((ox + px * s - x0) * SUPERSAMPLE, (oy + py * s - y0) * SUPERSAMPLE)
                   for px, py in shape]
            draw.line(pts, fill=255, width=width, joint="curve")

        mask = big.resize((w, h), Image.LANCZOS)
        halo = mask.filter(ImageFilter.GaussianBlur(radius=blur / 2.0))
        m = np.asarray(mask, dtype=np.float32) / 255.0
        g_ = np.asarray(halo, dtype=np.float32) / 255.0 * alpha
        combined = m + g_ * (1.0 - m)
        tile = np.zeros((h, w, 4), dtype=np.uint8)
        tile[..., :3] = self.colour
        tile[..., 3] = np.clip(np.round(combined * 255), 0, 255).astype(np.uint8)
        tile[tile[..., 3] == 0, :3] = 0
        return Image.fromarray(tile, "RGBA"), (x0, y0)
