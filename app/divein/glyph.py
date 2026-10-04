"""
The audio-reactive glyph, top-right: the ring coil, or one of two waveforms.

The coil is the DFI logo's motif — five stacked ellipses, like a spring seen at
an angle. Each ring is hand-wobbly rather than a perfect ellipse, and that
wobble is generated ONCE per episode. Only the spacing between rings moves with
the music. Re-randomising the wobble every frame would make the outlines boil.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.divein.config import rng_for

SUPERSAMPLE = 4          # draw 4x larger then shrink: smooth, anti-aliased lines


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
        self._rest_wave = self._make_rest_wave(episode)

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

    def _make_rest_wave(self, episode: str) -> np.ndarray:
        rng = rng_for(episode, "line")
        raw = rng.normal(size=110)
        for _ in range(3):
            raw = (np.roll(raw, 1) + raw + np.roll(raw, -1)) / 3
        return raw / (np.abs(raw).max() or 1.0)

    def ring_points(self, gap: float) -> list[np.ndarray]:
        """
        Each ring's outline in glyph units, stacked with the given gap.

        Anchored at the middle ring by default, so the coil opens and closes
        evenly up and down; or at the bottom ring, growing upwards.
        """
        rc = self.g["rings"]
        count = len(self._rings)
        out = []
        for i, shape in enumerate(self._rings):
            if rc["anchor"] == "bottom":
                cy = rc["bottom_y"] - (count - 1 - i) * gap
            else:
                cy = rc["centre_y"] + (i - (count - 1) / 2) * gap
            out.append(shape + np.array([rc["centre_x"], cy]))
        return out

    def rest_state(self) -> dict:
        """What the glyph looks like with no music: the static JPG, the preview."""
        return {"gap": self.cfg["audio"]["gap_rest"], "glow": 0.0,
                "bands": list(self.g["bars"]["rest_heights"]), "wave": None,
                "level": 0.5}

    # ── drawing ──────────────────────────────────────────────────────────
    def render(self, state: dict, scale: float = 1.0, origin=None):
        """
        Draw the glyph. Returns (RGBA tile, (x, y) on the canvas), or None.

        `origin` overrides where the glyph box sits (defaults to box_x, box_y).
        """
        if self.mode == "none":
            return None
        s = scale * self.g["scale"]
        ox, oy = origin if origin is not None else (self.g["box_x"], self.g["box_y"])

        if self.mode == "rings":
            shapes = [np.vstack([p, p[:1]]) for p in self.ring_points(state["gap"])]
            stroke = self.g["rings"]["stroke"]
            kind = "lines"
        elif self.mode == "bars":
            shapes = self._bar_rects(state["bands"])
            stroke = 0.0
            kind = "bars"
        elif self.mode == "line":
            shapes = [self._line_points(state.get("wave"), state.get("level", 0.5))]
            stroke = self.g["line"]["stroke"]
            kind = "lines"
        else:
            raise ValueError(f"Unknown glyph mode {self.mode!r}.")

        glow = self.g["glow"]
        t = float(np.clip(state.get("glow", 0.0), 0, 1))
        blur = (glow["blur"] + (glow["peak_blur"] - glow["blur"]) * t) * scale
        alpha = glow["alpha"] + (glow["peak_alpha"] - glow["alpha"]) * t

        # Bounds of everything to be drawn, in canvas pixels, with room for glow.
        allpts = np.vstack([np.asarray(sh).reshape(-1, 2) for sh in shapes])
        margin = stroke * s / 2 + 3 * blur + 2
        x0 = math.floor(ox + allpts[:, 0].min() * s - margin)
        y0 = math.floor(oy + allpts[:, 1].min() * s - margin)
        x1 = math.ceil(ox + allpts[:, 0].max() * s + margin)
        y1 = math.ceil(oy + allpts[:, 1].max() * s + margin)
        w, h = x1 - x0, y1 - y0

        big = Image.new("L", (w * SUPERSAMPLE, h * SUPERSAMPLE), 0)
        draw = ImageDraw.Draw(big)
        def to_big(px, py):
            return ((ox + px * s - x0) * SUPERSAMPLE, (oy + py * s - y0) * SUPERSAMPLE)

        if kind == "lines":
            width = max(1, int(round(stroke * s * SUPERSAMPLE)))
            for shape in shapes:
                pts = [to_big(px, py) for px, py in shape]
                draw.line(pts, fill=255, width=width, joint="curve")
                r = width / 2                     # round caps on open lines
                for cx, cy in (pts[0], pts[-1]):
                    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
        else:
            corner = self.g["bars"]["corner"] * s * SUPERSAMPLE
            for (bx0, by0, bx1, by1) in shapes:
                a = to_big(bx0, by0)
                b = to_big(bx1, by1)
                draw.rounded_rectangle([a[0], a[1], b[0], b[1]],
                                       radius=min(corner, (b[0] - a[0]) / 2), fill=255)

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

    def _bar_rects(self, levels) -> list[tuple]:
        b = self.g["bars"]
        count = int(b["count"])
        levels = list(levels)[:count] + [0.0] * max(0, count - len(levels))
        total = count * b["bar_width"] + (count - 1) * b["spacing"]
        left = self.g["rings"]["centre_x"] - total / 2
        rects = []
        for i, v in enumerate(levels):
            height = b["min_height"] + (b["max_height"] - b["min_height"]) * float(np.clip(v, 0, 1))
            x = left + i * (b["bar_width"] + b["spacing"])
            rects.append((x, b["bottom_y"] - height, x + b["bar_width"], b["bottom_y"]))
        return rects

    def _line_points(self, wave, level: float) -> np.ndarray:
        """
        A short stretch of the actual waveform, drawn across the glyph box.

        The shape is scaled to fill the box, and loudness decides how far it
        swings — so it always reads as a waveform, but quiet passages are
        visibly calmer than loud ones.
        """
        ln = self.g["line"]
        n = 110
        taper = np.sin(np.linspace(0, math.pi, n)) ** 0.8   # ends meet the centre
        if wave is None:
            values = self._rest_wave * ln["rest_amplitude"] / max(ln["max_amplitude"], 1e-6)
        else:
            wave = np.asarray(wave, dtype=np.float64)
            if wave.size < 2 or not np.abs(wave).max():
                values = np.zeros(n)
            else:
                # Average down to n points (not just sample), then soften.
                box = max(1, wave.size // n)
                if box > 1:
                    wave = np.convolve(wave, np.ones(box) / box, mode="same")
                values = np.interp(np.linspace(0, wave.size - 1, n),
                                   np.arange(wave.size), wave)
                k = max(1, int(ln["smooth"]))
                if k > 1:
                    values = np.convolve(values, np.ones(k) / k, mode="same")
                peak = np.abs(values).max()
                values = values / peak if peak else values
                swing = ln["min_scale"] + (1 - ln["min_scale"]) * float(np.clip(level, 0, 1))
                values = values * swing
        x = self.g["rings"]["centre_x"] + np.linspace(-ln["width"] / 2, ln["width"] / 2, n)
        y = ln["centre_y"] - values * taper * ln["max_amplitude"]
        return np.stack([x, y], axis=1)
