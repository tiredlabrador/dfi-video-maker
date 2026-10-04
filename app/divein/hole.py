"""
The hole motif, drawn from assets/hole.svg without an SVG library.

The file is a single hand-drawn path using only the M, L, H, V, C and Z
commands and the "even-odd" fill rule (overlapping pieces cancel out, which is
what makes the holes). That's a small enough subset to fill directly, and doing
it natively means the shape can be coloured with any brand accent.
"""
from __future__ import annotations

import functools
import re

import numpy as np
from PIL import Image

_TOKEN = re.compile(r"[MLHVCZmlhvcz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
CURVE_STEPS = 6
SUPERSAMPLE = 4


def read_svg(path: str):
    """The viewBox size and the path data of a one-path SVG."""
    text = open(path, encoding="utf-8").read()
    vb = re.search(r'viewBox="([^"]+)"', text)
    _, _, w, h = (float(v) for v in vb.group(1).replace(",", " ").split())
    d = " ".join(re.findall(r'\sd="([^"]+)"', text))
    return (w, h), d


def parse_path(d: str) -> list[np.ndarray]:
    """Turn path data into closed outlines (lists of points), curves flattened."""
    tokens = _TOKEN.findall(d)
    shapes: list[np.ndarray] = []
    current: list[tuple[float, float]] = []
    x = y = 0.0
    cmd = None
    i = 0

    def num():
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    def close():
        if len(current) >= 3:
            shapes.append(np.array(current, dtype=np.float64))

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                close()
                current = []
                continue
        if cmd is None:
            i += 1
            continue
        if cmd in "Mm":
            close()
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if cmd == "m" else (nx, ny)
            current = [(x, y)]
            cmd = "l" if cmd == "m" else "L"           # extra pairs are lines
        elif cmd in "Ll":
            nx, ny = num(), num()
            x, y = (x + nx, y + ny) if cmd == "l" else (nx, ny)
            current.append((x, y))
        elif cmd in "Hh":
            nx = num()
            x = x + nx if cmd == "h" else nx
            current.append((x, y))
        elif cmd in "Vv":
            ny = num()
            y = y + ny if cmd == "v" else ny
            current.append((x, y))
        elif cmd in "Cc":
            pts = [num() for _ in range(6)]
            if cmd == "c":
                pts = [pts[0] + x, pts[1] + y, pts[2] + x, pts[3] + y, pts[4] + x, pts[5] + y]
            x1, y1, x2, y2, x3, y3 = pts
            for t in np.linspace(0, 1, CURVE_STEPS + 1)[1:]:
                mt = 1 - t
                current.append((mt ** 3 * x + 3 * mt * mt * t * x1 + 3 * mt * t * t * x2 + t ** 3 * x3,
                                mt ** 3 * y + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t ** 3 * y3))
            x, y = x3, y3
        else:
            raise ValueError(f"Unsupported SVG path command {cmd!r}.")
    close()
    return shapes


def _fill_even_odd(shapes, width: int, height: int, scale: float) -> np.ndarray:
    """Scanline fill: each row is inside wherever an odd number of edges lie left."""
    edges = []
    for pts in shapes:
        a = pts * scale
        b = np.roll(a, -1, axis=0)
        edges.append(np.hstack([a, b]))
    e = np.vstack(edges)
    x0, y0, x1, y1 = e[:, 0], e[:, 1], e[:, 2], e[:, 3]
    out = np.zeros((height, width), dtype=np.uint8)
    cols = np.arange(width) + 0.5
    for row in range(height):
        yc = row + 0.5
        hit = (y0 <= yc) != (y1 <= yc)
        if not hit.any():
            continue
        xs = x0[hit] + (yc - y0[hit]) * (x1[hit] - x0[hit]) / (y1[hit] - y0[hit])
        crossings = np.searchsorted(np.sort(xs), cols)   # edges to the left of each pixel
        out[row] = (crossings % 2).astype(np.uint8) * 255
    return out


@functools.lru_cache(maxsize=16)
def _cached(path: str | None, width: int, d: str | None, view):
    if path is not None:
        view, d = read_svg(path)
    vw, vh = view
    height = int(round(width * vh / vw))
    shapes = parse_path(d)
    big = _fill_even_odd(shapes, width * SUPERSAMPLE, height * SUPERSAMPLE,
                         width * SUPERSAMPLE / vw)
    return Image.fromarray(big, "L").resize((width, height), Image.LANCZOS)


def hole_mask(path: str | None, width: int, d: str | None = None, view=None) -> Image.Image:
    """The motif as a greyscale mask (white = shape), `width` pixels wide."""
    return _cached(path, int(width), d, tuple(view) if view else None).copy()
