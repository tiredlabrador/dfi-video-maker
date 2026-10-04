"""
Every Dive In setting, in one place.

All sizes are in pixels on the 1080x1350 canvas unless the name says otherwise.
Change a number here (or paste an override into the Tuning box on the page) and
both the preview and the export follow, because they share one renderer.

Overrides are checked against these defaults: a misspelt name is an error, not
a silent no-op, so a typo can't leave you wondering why nothing changed.
"""
from __future__ import annotations

import copy
import hashlib

import numpy as np

DEFAULTS: dict = {
    "canvas": {"width": 1080, "height": 1350, "fps": 30},

    # The only three colours in the design.
    "colours": {"black": "#000000", "white": "#ffffff", "yellow": "#fffe01"},

    # Layer 1: the photo, cover-fitted, greyscale.
    "photo": {"contrast": 1.35, "brightness": 0.9},

    # Layer 2: a blurred, brighter copy, nudged, screened over the top.
    "ghost": {"enabled": True, "offset_x": 22, "offset_y": -10,
              "contrast": 1.6, "brightness": 1.3, "blur": 5.0, "opacity": 0.45},

    # Layer 3: film grain. `pool` is how many different grain frames exist;
    # frames are picked from it in a seeded random order, so it never visibly loops.
    "grain": {"enabled": True, "sd": 40.0, "opacity": 0.9, "every_frames": 2,
              "pool": 32},

    # Layer 4: darkened edges. Centre and radii are fractions of the canvas.
    "vignette": {"enabled": True, "centre_x": 0.5, "centre_y": 0.375,
                 "inner": 0.45, "edge_alpha": 0.65},

    # Layer 5: the DFI logo, taken from The Dig's overlay and scaled to match R1.
    "logo": {"x": 49, "y": 48, "width": 197},

    # Layer 7: hazard tape. `angle` is degrees, rising to the right.
    # `texture_*` reproduce R1, where the tape is a duller, grainy yellow rather
    # than flat #fffe01. Set texture_mix to 0 for pure #fffe01.
    "tape": {"enabled": True, "centre_x": 540, "centre_y": 754, "angle": 9.005,
             "thickness": 128, "font": "SquidBoyV4-Regular.otf", "font_size": 60,
             "text": "DON'T FALL IN • DIVE IN SERIES • {episode} • ",
             "phase": 969, "text_offset_y": 0,
             "texture_mix": 0.37, "texture_grey": 0.576, "texture_grain": 0.52},

    # Layer 8: artist name, bottom-left, up to two lines, bottom-anchored.
    "name": {"font": "SquidBoyV4-Regular.otf", "font_size": 152, "x": 44,
             "last_baseline": 1278, "line_gap": 132, "max_width": 990,
             "min_font_size": 80},

    # Layer 6: the audio-reactive glyph, top-right. Glyph units are pixels at
    # scale 1. The box is where the glyph sits at rest; it may grow upwards.
    "glyph": {
        "mode": "rings",              # rings | bars | line | none
        "box_x": 900, "box_y": 54, "box_width": 132, "box_height": 64,
        "scale": 1.0,
        "rings": {"count": 5, "rx": 58, "ry": 10, "points": 110, "stroke": 4.5,
                  "radial_sd": 0.55, "radial_smooth": 3, "vertical_sd": 0.35,
                  "centre_x": 66, "bottom_y": 52},
        "bars": {"count": 4, "bar_width": 14, "spacing": 10, "min_height": 8,
                 "max_height": 60, "rest_heights": [0.55, 0.9, 0.7, 0.45],
                 "bottom_y": 60, "corner": 7},
        "line": {"width": 116, "centre_y": 32, "max_amplitude": 26,
                 "window_ms": 40, "smooth": 5, "stroke": 4.5, "rest_amplitude": 3},
        "glow": {"blur": 6.0, "alpha": 0.6, "peak_blur": 8.0, "peak_alpha": 0.8},
    },

    # Audio analysis and how it drives the glyph.
    "audio": {
        "clip_seconds": 25.0, "sample_rate": 44100,
        # Loudness is scaled between these percentiles of the clip's own range,
        # so one huge peak can't squash everything else into "quiet".
        "rms_low_pct": 5.0, "rms_high_pct": 95.0, "silence_db": -50.0,
        "gap_silence": 2.0, "gap_low": 5.0, "gap_rest": 9.0, "gap_loud": 12.0,
        "gap_kick": 18.0,
        "attack_frames": 1, "release_ms": 200.0,
        "kick_low_hz": 40.0, "kick_high_hz": 120.0,
        "kick_sensitivity": 1.5, "kick_min_interval_ms": 220.0,
        "kick_floor": 0.02,
    },

    # The ghost "stutter" on each kick.
    "twitch": {"enabled": True, "min_px": 3.0, "max_px": 6.0, "release_ms": 120.0},

    # The 1:1 static JPG (SoundCloud). Overrides the 4:5 layout where they differ.
    "square": {"size": 1080, "tape_centre_y": 603, "name_last_baseline": 1008,
               "jpg_quality": 92},

    "export": {"crf": 18, "preset": "medium", "preview_seconds": 8.0,
               "preview_width": 540},
}


class ConfigError(ValueError):
    """An override names a setting that doesn't exist, or has the wrong type."""


def _check_type(path: str, default, value):
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigError(f"{path} should be true or false.")
    elif isinstance(default, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path} should be a number.")
    elif isinstance(default, str):
        if not isinstance(value, str):
            raise ConfigError(f"{path} should be text.")
    elif isinstance(default, list):
        if not isinstance(value, list):
            raise ConfigError(f"{path} should be a list.")


def _merge(base: dict, override: dict, path: str) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        where = f"{path}.{key}" if path else key
        if key not in base:
            raise ConfigError(f"There is no setting called {where}.")
        if isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{where} is a group of settings, not a value.")
            out[key] = _merge(base[key], value, where)
        else:
            _check_type(where, base[key], value)
            out[key] = copy.deepcopy(value)
    return out


def merge_config(overrides: dict | None) -> dict:
    """The defaults with `overrides` laid over them. Never mutates DEFAULTS."""
    if not overrides:
        return copy.deepcopy(DEFAULTS)
    if not isinstance(overrides, dict):
        raise ConfigError("Settings overrides must be a JSON object.")
    return _merge(DEFAULTS, overrides, "")


# ── seeded randomness ────────────────────────────────────────────────────
def seed_for(episode: str, purpose: str) -> int:
    """
    A seed derived from the episode number and what it's for.

    Same episode, same look, every time. Each purpose gets its own stream so
    that, say, changing the grain settings can't reshuffle the ring shapes.
    """
    key = f"{str(episode).strip()}|{purpose}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def rng_for(episode: str, purpose: str) -> np.random.Generator:
    return np.random.default_rng(seed_for(episode, purpose))
