"""
The Dive In look, drawn onto camera footage one frame at a time.

The same settings and maths as the photo clips (compose.py): black and white,
contrast, the ghost (a soft, brighter echo that jumps on each hit in the
music), grain and darkened edges. Each can be switched off. The photo clips'
sizes are for a 1080-wide frame; smaller frames (previews) are scaled to match.
"""
from __future__ import annotations

import threading

import numpy as np
from PIL import Image, ImageFilter

from app.divein.compose import (_blend, _screen, _soft_light, _treat, grain_frame, grain_index,
                                vignette_mask)

OPTIONS = ("bw", "contrast", "grain", "vignette", "ghost")


class FootageLook:
    def __init__(self, cfg: dict, episode: str, W: int, H: int, options: dict,
                 analysis: dict | None = None, graphics: "Graphics | None" = None):
        self.cfg = cfg
        self.graphics = graphics
        self.episode = str(episode).strip()
        self.W, self.H = W, H
        self.on = {k: bool(options.get(k)) for k in OPTIONS}
        self.analysis = analysis
        self.scale = W / cfg["canvas"]["width"]
        self._vignette = vignette_mask({**cfg["vignette"], "enabled": True}, W, H) \
            if self.on["vignette"] else None
        self._grain_seq: list[int] = []
        self._grain_cache: dict[int, np.ndarray] = {}
        self._grain_lock = threading.Lock()   # frames are drawn several at a time

    def _grain(self, f: int) -> np.ndarray:
        gr = self.cfg["grain"]
        with self._grain_lock:
            k = grain_index(gr, self.episode, f, self._grain_seq)
            if k not in self._grain_cache:
                self._grain_cache[k] = grain_frame(gr, self.episode, f"grain-footage-{k}",
                                                   self.W, self.H)
            grain = self._grain_cache[k]
        return grain.astype(np.float32) / 255.0

    def _twitch(self, f: int) -> tuple[float, float]:
        a = self.analysis
        if not a or not len(a.get("twitch_x", [])):
            return 0.0, 0.0
        i = min(f, len(a["twitch_x"]) - 1)
        return float(a["twitch_x"][i]), float(a["twitch_y"][i])

    def _ghost(self, x: np.ndarray, f: int) -> np.ndarray:
        """The echo: brighter, blurred, nudged (further on each hit)."""
        g = self.cfg["ghost"]
        bright = _treat(x, g["contrast"], g["brightness"])
        mode = "L" if bright.ndim == 2 else "RGB"
        img = Image.fromarray(np.round(bright * 255).astype(np.uint8), mode)
        img = img.filter(ImageFilter.GaussianBlur(radius=g["blur"] * self.scale))
        soft = np.asarray(img, dtype=np.float32) / 255.0
        tx, ty = self._twitch(f)
        dx = int(round((g["offset_x"] + tx) * self.scale))
        dy = int(round((g["offset_y"] + ty) * self.scale))
        # Shift by (dx, dy), repeating the edge into the gap it leaves.
        pad = max(abs(dx), abs(dy)) + 1
        widths = ((pad, pad), (pad, pad)) + (((0, 0),) if soft.ndim == 3 else ())
        padded = np.pad(soft, widths, mode="edge")
        return padded[pad - dy:pad - dy + self.H, pad - dx:pad - dx + self.W]

    def frame(self, f: int, rgb: np.ndarray) -> np.ndarray:
        """Frame `f` of the footage (HxWx3 uint8), with the look applied."""
        on = self.on
        if not any(on.values()):
            return self._graphics(f, rgb)
        x = rgb.astype(np.float32) / 255.0
        if on["bw"]:
            # The same weights as the photo's greyscale (ITU-R 601).
            x = x[..., 0] * 0.299 + x[..., 1] * 0.587 + x[..., 2] * 0.114
        p = self.cfg["photo"]
        value = _treat(x, p["contrast"], p["brightness"]) if on["contrast"] else x
        if on["ghost"] and self.cfg["ghost"]["opacity"] > 0:
            value = _screen(value, self._ghost(x, f), self.cfg["ghost"]["opacity"])
        if on["grain"]:
            grain = self._grain(f)
            value = _soft_light(value, grain if value.ndim == 2 else grain[..., None],
                                self.cfg["grain"]["opacity"])
        if self._vignette is not None:
            value = value * (self._vignette if value.ndim == 2 else self._vignette[..., None])
        out = np.clip(np.round(value * 255.0), 0, 255).astype(np.uint8)
        out = np.repeat(out[..., None], 3, axis=2) if out.ndim == 2 else out
        return self._graphics(f, out)

    def _graphics(self, f: int, out: np.ndarray) -> np.ndarray:
        """The tape, logo, name and rings on top: after the look, so they keep their colour."""
        if self.graphics is None:
            return out
        out = np.array(out)                          # a copy that can be drawn on
        self.graphics.draw(out, f)
        return out


class Graphics:
    """
    The design's graphics (tape, logo, name, rings), as drawn on the photo
    clips, laid over footage frames of any size. `scene` is the design at
    full size, with no photo; smaller frames (previews) get them scaled.
    """

    def __init__(self, scene, W: int, H: int, analysis: dict | None):
        self.scene, self.analysis = scene, analysis
        self.k = W / scene.W
        self._lock = threading.Lock()                # frames are drawn several at a time
        self._tapes: dict = {}
        self._rings: dict = {}                       # each rings drawing, scaled to fit
        self._pieces = [self._fit(tile, pos) for tile, pos in scene._overlay_pieces]

    def _fit(self, tile: Image.Image, pos) -> tuple:
        if abs(self.k - 1) < 1e-6:
            return tile, pos
        size = (max(1, round(tile.width * self.k)), max(1, round(tile.height * self.k)))
        return tile.resize(size, Image.LANCZOS), (round(pos[0] * self.k), round(pos[1] * self.k))

    def draw(self, out: np.ndarray, f: int) -> None:
        # Only the grain's running order needs one thread at a time; the rest
        # comes out the same whichever thread makes it, so is made unlocked.
        with self._lock:
            k = self.scene.grain_index(f)            # the tape's texture follows the grain
        if k not in self._tapes:
            layer = self.scene._tape_layer(k)
            self._tapes[k] = self._fit(*layer) if layer else None
        tape = self._tapes[k]
        if tape is not None:
            _blend(out, *tape)
        for tile, pos in self._pieces:
            _blend(out, tile, pos)
        rings = self.scene.glyph.render(self.scene.glyph_state(f, self.analysis))
        if rings is not None:
            kept = self._rings.get(id(rings[0]))
            if kept is None or kept[0] is not rings[0]:
                if len(self._rings) > 400:
                    self._rings.clear()
                # The drawing is kept alongside, so its id can't be reused.
                kept = (rings[0], self._fit(*rings))
                self._rings[id(rings[0])] = kept
            _blend(out, *kept[1])
