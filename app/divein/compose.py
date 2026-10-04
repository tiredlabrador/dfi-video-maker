"""
Drawing one Dive In frame.

Layers, bottom to top:
  1. the photo — cover-cropped, greyscale, contrast and brightness
  2. the ghost — the same crop, nudged, blurred, brighter, screened on top
  3. grain — film-like noise, soft-light blended, changing every 2 frames
  4. vignette — darkened edges
  5. the hazard tape (textured like R1), its text
  6. the DFI logo and the artist name
  7. the coil (or, on the 1:1 JPG, the hole motif)

Speed comes from doing the slow work once. The photo layers are prepared when
the scene is built; each grain variation is composited once and reused. A
frame then only costs the glyph, unless the ghost is mid-twitch.
"""
from __future__ import annotations

import hashlib
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.divein.config import rng_for, seed_for
from app.divein.glyph import Glyph, accent_rgb, hex_to_rgb
from app.divein.hole import hole_mask

REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS = os.path.join(REPO_DIR, "assets")

# Where the logo sits inside The Dig's overlay PNG (its exact ink bounds).
LOGO_SOURCE = ("overlay-portrait.png", (49, 66, 219, 142))

# Longest side a source photo is kept at. Enough for a 3x zoom at 1080 wide.
MAX_SOURCE = 4000


# ── assets ───────────────────────────────────────────────────────────────
def font_path(name: str) -> str | None:
    """The font file, falling back to the original Squid Boy, then nothing."""
    for candidate in (name, "SquidBoy.otf"):
        path = os.path.join(ASSETS, "fonts", candidate)
        if os.path.exists(path):
            return path
    return None


def _font(name: str, size: int):
    path = font_path(name)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def load_photo(path: str) -> Image.Image:
    """
    Open a photo the right way up, as greyscale, at a sensible size.

    Phones store photos sideways with a note saying how to turn them, so that
    note is applied first. Greyscale uses the same weights as the CSS
    `grayscale()` filter the spec was written against.
    """
    img = Image.open(path)
    if img.format == "JPEG":
        # Ask the decoder for a smaller picture up front: a 48MP phone photo
        # otherwise briefly costs the best part of a gigabyte.
        img.draft("RGB", (MAX_SOURCE, MAX_SOURCE))
    img = ImageOps.exif_transpose(img)
    if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        # 16-bit and 32-bit greyscale: converting straight to 8-bit clips every
        # value above 255 to white, so scale it down properly instead.
        deep = np.asarray(img, dtype=np.float64)
        top = 65535.0 if deep.max() > 255 else 255.0
        if img.mode == "F" and deep.max() <= 1.0:
            top = 1.0
        img = Image.fromarray(np.clip(deep / top * 255.0, 0, 255).astype(np.uint8), "L")
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        backing = Image.new("RGBA", img.size, (0, 0, 0, 255))
        img = Image.alpha_composite(backing, img)
    img = img.convert("RGB")
    if max(img.size) > MAX_SOURCE:
        img.thumbnail((MAX_SOURCE, MAX_SOURCE), Image.LANCZOS)
    arr = np.asarray(img, dtype=np.float32)
    grey = arr[..., 0] * 0.2126 + arr[..., 1] * 0.7152 + arr[..., 2] * 0.0722
    return Image.fromarray(np.clip(np.round(grey), 0, 255).astype(np.uint8), "L")


def crop_window(iw: float, ih: float, w: int, h: int, crop: dict | None):
    """
    Which part of the photo fills the frame: (x0, y0, width, height) in photo px.

    `zoom` 1 is a plain cover fit; 2 shows half as much. `cx`, `cy` (0..1) is the
    point of the photo to centre on, clamped so the frame never runs off it.
    """
    crop = crop or {}
    zoom = max(1.0, float(crop.get("zoom", 1.0)))
    scale = max(w / iw, h / ih) * zoom
    win_w, win_h = w / scale, h / scale
    cx = float(np.clip(float(crop.get("cx", 0.5)) * iw, win_w / 2, iw - win_w / 2))
    cy = float(np.clip(float(crop.get("cy", 0.5)) * ih, win_h / 2, ih - win_h / 2))
    return cx - win_w / 2, cy - win_h / 2, win_w, win_h


# ── text ─────────────────────────────────────────────────────────────────
def tape_text(cfg: dict, episode: str) -> str:
    return cfg["tape"]["text"].replace("{episode}", str(episode).strip())


def layout_name(text: str, cfg: dict, max_width: float | None = None):
    """
    Upper-case the name and fit it onto at most two lines.

    A line break typed by the user is kept. Otherwise a name too wide for one
    line is split at the space that keeps the longer line shortest. If it still
    doesn't fit, the type shrinks — never below `min_font_size`.
    Returns (lines, font_size).
    """
    n = cfg["name"]
    max_width = max_width or n["max_width"]
    raw = [" ".join(part.split()) for part in str(text or "").upper().split("\n")]
    raw = [part for part in raw if part]
    if not raw:
        return [], n["font_size"]
    if len(raw) > 2:
        raw = [raw[0], " ".join(raw[1:])]

    size = int(n["font_size"])
    font = _font(n["font"], size)
    if len(raw) == 1 and font.getlength(raw[0]) > max_width and " " in raw[0]:
        words = raw[0].split()
        best = min(range(1, len(words)),
                   key=lambda i: max(font.getlength(" ".join(words[:i])),
                                     font.getlength(" ".join(words[i:]))))
        raw = [" ".join(words[:best]), " ".join(words[best:])]

    def too_wide(sz):
        f = _font(n["font"], sz)
        return max(f.getlength(line) for line in raw) > max_width

    while size > n["min_font_size"] and too_wide(size):
        size -= 2
    # Below the preferred minimum only if it still doesn't fit: running off the
    # edge of the picture is worse than small type.
    while size > 24 and too_wide(size):
        size -= 2
    return raw, size


def missing_glyphs(text: str, cfg: dict) -> list[str]:
    """
    Characters the brand font can't draw.

    Squid Boy covers Latin letters (accents included) and common punctuation,
    but not Cyrillic, Chinese, Japanese or emoji — those would just vanish.
    """
    font = _font(cfg["name"]["font"], 60)

    def ink(ch):
        img = Image.new("L", (120, 120), 0)
        ImageDraw.Draw(img).text((20, 20), ch, font=font, fill=255)
        return img.tobytes()

    placeholder = ink("\uffff")          # what the font draws for "no such letter"
    missing = []
    for ch in dict.fromkeys(str(text or "")):
        if ch.isspace():
            continue
        drawn = ink(ch)
        if not any(drawn) or drawn == placeholder:
            missing.append(ch)
    return missing


# ── blend maths (all on 0..1 floats) ─────────────────────────────────────
def _treat(grey: np.ndarray, contrast: float, brightness: float) -> np.ndarray:
    """CSS contrast() then brightness(), clamped after each like a browser."""
    out = np.clip((grey - 0.5) * contrast + 0.5, 0.0, 1.0)
    return np.clip(out * brightness, 0.0, 1.0)


def _screen(base, top, opacity):
    return base + opacity * ((1.0 - (1.0 - base) * (1.0 - top)) - base)


def _soft_light(base, top, opacity):
    """The W3C soft-light formula, mixed in at `opacity`."""
    d = np.where(base <= 0.25, ((16 * base - 12) * base + 4) * base, np.sqrt(base))
    light = np.where(top <= 0.5,
                     base - (1 - 2 * top) * base * (1 - base),
                     base + (2 * top - 1) * (d - base))
    return base + opacity * (light - base)


def _blend(out: np.ndarray, tile: Image.Image, pos) -> None:
    """Lay a straight-alpha RGBA tile onto an RGB array in place (clipped)."""
    x, y = pos
    t = np.asarray(tile)
    H, W = out.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + t.shape[1]), min(H, y + t.shape[0])
    if x0 >= x1 or y0 >= y1:
        return
    t = t[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    a = t[..., 3:4] / 255.0
    region = out[y0:y1, x0:x1].astype(np.float32)
    out[y0:y1, x0:x1] = np.clip(np.round(t[..., :3] * a + region * (1 - a)), 0, 255)


def _split_into_pieces(overlay: Image.Image):
    """
    Cut a mostly-empty full-canvas layer into the strips that hold anything
    (here: the logo at the top, the name at the bottom). Pasting two small
    pieces is much quicker than pasting a whole transparent canvas.
    """
    alpha = np.asarray(overlay)[..., 3]
    used = np.nonzero(alpha.any(axis=1))[0]
    if used.size == 0:
        return []
    pieces, start = [], used[0]
    for a, b in zip(used, list(used[1:]) + [None]):
        if b is None or b - a > 40:
            cols = np.nonzero(alpha[start:a + 1].any(axis=0))[0]
            box = (int(cols[0]), int(start), int(cols[-1]) + 1, int(a) + 1)
            pieces.append((overlay.crop(box), (box[0], box[1])))
            if b is not None:
                start = b
    return pieces


# ── the scene ────────────────────────────────────────────────────────────
class Scene:
    """Everything about one design that doesn't change frame to frame."""

    def __init__(self, cfg: dict, photo: Image.Image, artist: str, episode: str,
                 crop: dict | None = None, fmt: str = "portrait"):
        self.cfg = cfg
        self.episode = str(episode).strip()
        self.fmt = fmt
        self.W = cfg["canvas"]["width"]
        self.H = cfg["canvas"]["height"] if fmt == "portrait" else cfg["square"]["size"]
        self.fps = cfg["canvas"]["fps"]
        self.glyph = Glyph(cfg, self.episode)
        if photo is not None:                 # None: just the layers above it
            self._prepare_photo(photo, crop)
        self._vignette = self._make_vignette()
        self._tape_alpha, self._text_alpha = self._make_tape()
        self._overlay = self._make_overlay(artist)
        self._grain_cache: dict[int, np.ndarray] = {}
        self._background_cache: dict[int, np.ndarray] = {}
        self._grain_sequence: list[int] = []
        self._hole_tile = None
        self._tape_cache: dict = {}
        self._overlay_pieces = _split_into_pieces(self._overlay)

    # ── preparation ──────────────────────────────────────────────────────
    def _prepare_photo(self, photo: Image.Image, crop: dict | None) -> None:
        g = self.cfg["ghost"]
        t = self.cfg["twitch"]
        # Room around the frame so the ghost can be offset without running out.
        self.M = int(math.ceil(max(abs(g["offset_x"]), abs(g["offset_y"]))
                               + t["max_px"] + 4))
        x0, y0, ww, wh = crop_window(photo.width, photo.height, self.W, self.H, crop)
        scale = self.W / ww
        pad = int(math.ceil(self.M / scale)) + 2
        padded = ImageOps.expand(photo, border=pad, fill=0)
        box = (x0 - self.M / scale + pad, y0 - self.M / scale + pad,
               x0 + ww + self.M / scale + pad, y0 + wh + self.M / scale + pad)
        big = padded.resize((self.W + 2 * self.M, self.H + 2 * self.M),
                            Image.LANCZOS, box=box)
        grey = np.asarray(big, dtype=np.float32) / 255.0
        M = self.M
        p = self.cfg["photo"]
        self._base = _treat(grey[M:M + self.H, M:M + self.W], p["contrast"], p["brightness"])

        ghost = _treat(grey, g["contrast"], g["brightness"])
        ghost_img = Image.fromarray(np.round(ghost * 255).astype(np.uint8), "L")
        ghost_img = ghost_img.filter(ImageFilter.GaussianBlur(radius=g["blur"]))
        self._ghost = np.asarray(ghost_img, dtype=np.float32) / 255.0

    def _make_vignette(self) -> np.ndarray:
        v = self.cfg["vignette"]
        if not v["enabled"]:
            return np.ones((self.H, self.W), dtype=np.float32)
        cx, cy = v["centre_x"] * self.W, v["centre_y"] * self.H
        # Like CSS "ellipse farthest-corner": the ellipse reaches the far corner.
        rx = max(cx, self.W - cx) * math.sqrt(2)
        ry = max(cy, self.H - cy) * math.sqrt(2)
        y, x = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
        d = np.hypot((x - cx) / rx, (y - cy) / ry)
        ramp = np.clip((d - v["inner"]) / max(1.0 - v["inner"], 1e-6), 0.0, 1.0)
        return (1.0 - ramp * v["edge_alpha"]).astype(np.float32)

    def _tape_centre_y(self) -> float:
        return self.cfg["tape"]["centre_y"] if self.fmt == "portrait" \
            else self.cfg["square"]["tape_centre_y"]

    def _make_tape(self):
        tp = self.cfg["tape"]
        if not tp["enabled"]:
            z = np.zeros((self.H, self.W), dtype=np.float32)
            return z, z
        a = math.radians(tp["angle"])
        cx, cy = tp["centre_x"], self._tape_centre_y()
        y, x = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
        dist = (x - cx) * math.sin(a) + (y - cy) * math.cos(a)
        band = np.clip(tp["thickness"] / 2 - np.abs(dist) + 0.5, 0.0, 1.0)

        font = _font(tp["font"], int(tp["font_size"]))
        unit = tape_text(self.cfg, self.episode)
        length = int(math.hypot(self.W, self.H)) + 400
        strip = Image.new("L", (length, int(tp["thickness"]) + 40), 0)
        draw = ImageDraw.Draw(strip)
        period = draw.textlength(unit, font=font)
        # `phase` was measured against R1 on a 1600px strip centred on the
        # tape; keep the text in the same place relative to the centre.
        xpos = (length - 1600) / 2 - (tp["phase"] % period)
        while xpos > 0:
            xpos -= period
        mid = strip.height / 2 + tp["text_offset_y"]
        while xpos < length:
            draw.text((xpos, mid), unit, font=font, fill=255, anchor="lm")
            xpos += period
        rot = strip.rotate(tp["angle"], resample=Image.BICUBIC, expand=True)
        canvas = Image.new("L", (self.W, self.H), 0)
        canvas.paste(rot, (int(round(cx - rot.width / 2)), int(round(cy - rot.height / 2))))
        text = np.asarray(canvas, dtype=np.float32) / 255.0
        return band.astype(np.float32), (text * band).astype(np.float32)

    def _make_overlay(self, artist: str) -> Image.Image:
        overlay = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        lg = self.cfg["logo"]
        name, box = LOGO_SOURCE
        source = os.path.join(ASSETS, name)
        if os.path.exists(source):
            logo = Image.open(source).convert("RGBA").crop(box)
            height = round(logo.height * lg["width"] / logo.width)
            logo = logo.resize((int(lg["width"]), height), Image.LANCZOS)
            overlay.alpha_composite(logo, (int(lg["x"]), int(lg["y"])))

        n = self.cfg["name"]
        lines, size = layout_name(artist, self.cfg)
        if lines:
            font = _font(n["font"], size)
            gap = n["line_gap"] * size / n["font_size"]
            last = n["last_baseline"] if self.fmt == "portrait" \
                else self.cfg["square"]["name_last_baseline"]
            draw = ImageDraw.Draw(overlay)
            white = hex_to_rgb(self.cfg["colours"]["white"])
            for i, line in enumerate(lines):
                baseline = last - (len(lines) - 1 - i) * gap
                # `x` is where the ink begins, so nudge by the first letter's
                # side bearing: then the name lines up exactly with the logo.
                bearing = font.getbbox(line, anchor="ls")[0]
                draw.text((n["x"] - bearing, baseline), line, font=font,
                          fill=white + (255,), anchor="ls")
        return overlay

    # ── grain ────────────────────────────────────────────────────────────
    def grain_index(self, frame: int) -> int:
        """
        Which grain variation a frame uses. Changes every `every_frames`.

        Each step moves to a different variation (a seeded random step of at
        least one), so the grain can never hold for longer than it should. The
        sequence is built from the start, so it's the same whatever order frames
        are asked for in.
        """
        gr = self.cfg["grain"]
        pool = max(2, int(gr["pool"]))
        pair = frame // max(1, int(gr["every_frames"]))
        seq = self._grain_sequence
        while len(seq) <= pair:
            p = len(seq)
            if p == 0:
                seq.append(seed_for(self.episode, "grain-order-0") % pool)
            else:
                step = 1 + seed_for(self.episode, f"grain-order-{p}") % (pool - 1)
                seq.append((seq[-1] + step) % pool)
        return seq[pair]

    def _grain(self, k: int) -> np.ndarray:
        # Kept as 8-bit (a quarter of the memory); noise doesn't need more.
        if k not in self._grain_cache:
            gr = self.cfg["grain"]
            rng = rng_for(self.episode, f"grain-{self.fmt}-{k}")
            noise = rng.normal(128.0, gr["sd"], (self.H, self.W))
            self._grain_cache[k] = np.clip(np.round(noise), 0, 255).astype(np.uint8)
        return self._grain_cache[k].astype(np.float32) / 255.0

    # ── composition ──────────────────────────────────────────────────────
    def _tape_layer(self, k: int):
        """
        The tape for grain variation k: a straight-alpha RGBA strip covering
        just the rows the tape crosses, plus where it goes. Kept per variation,
        since the texture follows the grain.
        """
        if k not in self._tape_cache:
            tp = self.cfg["tape"]
            rows = np.nonzero(self._tape_alpha.any(axis=1))[0]
            if not tp["enabled"] or rows.size == 0:
                self._tape_cache[k] = None
                return None
            r0, r1 = int(rows[0]), int(rows[-1]) + 1
            ta = self._tape_alpha[r0:r1]
            xa = self._text_alpha[r0:r1]
            accent = np.array(accent_rgb(self.cfg), dtype=np.float32) / 255.0
            mix = tp["texture_mix"]
            if self.cfg["grain"]["enabled"]:
                n = self._grain(k)[r0:r1]
                tex = (tp["texture_grey"] + (n - 0.5) * tp["texture_grain"])[..., None]
            else:
                tex = tp["texture_grey"]
            colour = accent * (1.0 - mix) + mix * tex
            colour = np.broadcast_to(colour, ta.shape + (3,))
            # The text sits inside the tape: blend towards the text colour by
            # the share of each pixel it covers.
            ink = np.where(ta > 0, xa / np.maximum(ta, 1e-6), 0.0)[..., None]
            black = np.array(hex_to_rgb(self.cfg["colours"]["black"]), dtype=np.float32) / 255.0
            rgba = np.empty(ta.shape + (4,), dtype=np.uint8)
            rgba[..., :3] = np.clip(np.round((colour * (1.0 - ink) + black * ink) * 255), 0, 255)
            rgba[..., 3] = np.clip(np.round(ta * 255), 0, 255)
            self._tape_cache[k] = (Image.fromarray(rgba, "RGBA"), (0, r0))
        return self._tape_cache[k]

    def _background(self, k: int, twitch=(0, 0)) -> np.ndarray:
        """Everything below the coil, as an HxWx3 uint8 array."""
        tx, ty = int(round(twitch[0])), int(round(twitch[1]))
        cacheable = (tx, ty) == (0, 0)
        if cacheable and k in self._background_cache:
            return self._background_cache[k]

        cfg = self.cfg
        value = self._base
        g = cfg["ghost"]
        if g["enabled"]:
            dx, dy = int(round(g["offset_x"])) + tx, int(round(g["offset_y"])) + ty
            M = self.M
            ghost = self._ghost[M - dy:M - dy + self.H, M - dx:M - dx + self.W]
            value = _screen(value, ghost, g["opacity"])
        if cfg["grain"]["enabled"]:
            value = _soft_light(value, self._grain(k), cfg["grain"]["opacity"])
        value = value * self._vignette

        # Greyscale until here; Pillow turns it to RGB and lays the coloured
        # layers on top far faster than doing it as floating-point maths.
        grey = np.clip(np.round(value * 255.0), 0, 255).astype(np.uint8)
        img = Image.fromarray(grey, "L").convert("RGB")
        tape = self._tape_layer(k)
        if tape is not None:
            strip, pos = tape
            img.paste(strip, pos, strip)
        for piece, pos in self._overlay_pieces:
            img.paste(piece, pos, piece)
        out = np.asarray(img)
        if cacheable:
            self._background_cache[k] = out
        return out

    def glyph_state(self, frame: int, analysis: dict | None) -> dict:
        if analysis is None:
            return self.glyph.rest_state()
        f = min(frame, len(analysis["gap"]) - 1)
        return {"gap": float(analysis["gap"][f]), "glow": float(analysis["glow"][f])}

    def frame_array(self, f: int, analysis: dict | None = None) -> np.ndarray:
        """Frame `f` as an HxWx3 array. With no analysis: the at-rest look."""
        twitch = (0, 0)
        if analysis is not None:
            i = min(f, len(analysis["gap"]) - 1)
            twitch = (analysis["twitch_x"][i], analysis["twitch_y"][i])
        out = self._background(self.grain_index(f), twitch).copy()
        # The 1:1 JPG always shows the hole motif; the video shows the coil.
        drawn = self._hole() if self.fmt == "square" \
            else self.glyph.render(self.glyph_state(f, analysis))
        if drawn is not None:
            _blend(out, *drawn)
        return out

    def frame(self, f: int, analysis: dict | None = None) -> Image.Image:
        return Image.fromarray(self.frame_array(f, analysis), "RGB")

    def _hole(self):
        """The hole motif in the accent colour, centred where the coil rests."""
        if self._hole_tile is None:
            h = self.cfg["hole"]
            g = self.cfg["glyph"]
            path = os.path.join(ASSETS, h["file"])
            if not os.path.exists(path):
                return None
            mask = hole_mask(path, int(h["width"]))
            rc = g["rings"]
            cx = g["box_x"] + rc["centre_x"] * g["scale"] + h["offset_x"]
            cy = g["box_y"] + rc["centre_y"] * g["scale"] + h["offset_y"]
            glow = g["glow"]
            pad = int(3 * glow["blur"]) + 2 if h["glow"] else 0
            m = Image.new("L", (mask.width + 2 * pad, mask.height + 2 * pad), 0)
            m.paste(mask, (pad, pad))
            a = np.asarray(m, dtype=np.float32) / 255.0
            if h["glow"]:
                halo = m.filter(ImageFilter.GaussianBlur(radius=glow["blur"] / 2.0))
                a = a + (np.asarray(halo, dtype=np.float32) / 255.0 * glow["alpha"]) * (1 - a)
            tile = np.zeros(a.shape + (4,), dtype=np.uint8)
            tile[..., :3] = accent_rgb(self.cfg)
            tile[..., 3] = np.clip(np.round(a * 255), 0, 255).astype(np.uint8)
            x = int(round(cx - m.width / 2))
            y = int(round(cy - m.height / 2))
            self._hole_tile = (Image.fromarray(tile, "RGBA"), (x, y))
        return self._hole_tile

    def layers(self) -> Image.Image:
        """
        Everything above the photo, as one transparent image: vignette, tape,
        logo, name and the glyph (or hole) at rest. The page lays this over the
        photo to draw a quick draft while you drag, before the exact render.
        """
        cfg = self.cfg
        out = np.zeros((self.H, self.W, 4), dtype=np.float32)
        # Vignette: black at the darkening amount (same maths as multiplying).
        out[..., 3] = 1.0 - self._vignette
        def over(rgb, alpha):
            a = alpha[..., None]
            out_a = out[..., 3:4]
            new_a = a + out_a * (1 - a)
            out[..., :3] = np.where(new_a > 0, (rgb * a + out[..., :3] * out_a * (1 - a))
                                    / np.maximum(new_a, 1e-6), 0)
            out[..., 3:4] = new_a
        tp = cfg["tape"]
        if tp["enabled"]:
            accent = np.array(accent_rgb(cfg), dtype=np.float32) / 255.0
            flat = accent * (1 - tp["texture_mix"]) + tp["texture_mix"] * tp["texture_grey"]
            over(np.broadcast_to(flat, (self.H, self.W, 3)), self._tape_alpha)
            over(np.zeros((self.H, self.W, 3), dtype=np.float32), self._text_alpha)
        img = Image.fromarray(np.clip(np.round(out * 255), 0, 255).astype(np.uint8), "RGBA")
        img.alpha_composite(self._overlay)
        drawn = self._hole() if self.fmt == "square" else self.glyph.render(self.glyph.rest_state())
        if drawn is not None:
            tile, (x, y) = drawn
            img.alpha_composite(tile, (max(0, x), max(0, y)))
        return img
