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
import math
import re

import numpy as np

DEFAULTS: dict = {
    "canvas": {"width": 1080, "height": 1350, "fps": 30},

    # Black and white, plus one accent for the tape and the glyph.
    "colours": {"black": "#000000", "white": "#ffffff"},
    # The DFI brand accents (from Dom's palette).
    "accents": {"yellow": "#fffe01", "red": "#ea2020", "green": "#3dff00",
                "white": "#ffffff"},
    "accent": "yellow",

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

    # Layer 5: the DFI logo, the same size and place as in The Dig.
    "logo": {"x": 49, "y": 66, "width": 170},

    # Layer 7: hazard tape. `angle` is degrees, rising to the right.
    # `texture_*` reproduce R1, where the tape is a duller, grainy yellow rather
    # than flat #fffe01. Set texture_mix to 0 for pure #fffe01.
    "tape": {"enabled": True, "centre_x": 540, "centre_y": 754, "angle": 9.005,
             "thickness": 128, "font": "SquidBoyV4-Regular.otf", "font_size": 60,
             "text": "DON'T FALL IN • DIVE IN SERIES • {episode} • ",
             "phase": 969, "text_offset_y": 0,
             "texture_mix": 0.37, "texture_grey": 0.576, "texture_grain": 0.52},

    # Layer 8: artist name, bottom-left, up to two lines, bottom-anchored.
    # `x` is where the ink starts: the same 49px margin as the logo.
    "name": {"font": "SquidBoyV4-Regular.otf", "font_size": 152, "x": 49,
             "last_baseline": 1278, "line_gap": 132, "max_width": 990,
             "min_font_size": 80},

    # Layer 6: the audio-reactive glyph, top-right. Glyph units are pixels at
    # scale 1. The box is where the glyph sits at rest. Placed to mirror the
    # logo: a 49px right margin, centred on the logo's middle (y 103.5).
    "glyph": {
        "mode": "rings",              # rings | none
        "box_x": 905, "box_y": 70, "box_width": 132, "box_height": 64,
        "scale": 1.0,
        # anchor "centre": the coil opens from its middle ring;
        # "bottom": from the bottom ring upwards (as first built).
        "rings": {"count": 5, "rx": 58, "ry": 10, "points": 110, "stroke": 4.5,
                  "radial_sd": 0.55, "radial_smooth": 3, "vertical_sd": 0.35,
                  "centre_x": 66, "anchor": "centre", "centre_y": 34, "bottom_y": 52},
        "glow": {"blur": 6.0, "alpha": 0.6, "peak_blur": 8.0, "peak_alpha": 0.8},
    },

    # Audio analysis and how it drives the glyph.
    "audio": {
        "clip_seconds": 25.0, "sample_rate": 44100,
        # What moves the coil. "level" (the default): just how loud it is —
        # simple, nothing to misfire; the twitch fires on sudden jumps in
        # loudness. "kicks": loudness plus detected kick drums, which snap it
        # fully open and trigger the twitch.
        "drive": "level",
        # Loudness is scaled between these percentiles of the clip's own range,
        # so one huge peak can't squash everything else into "quiet".
        "rms_low_pct": 5.0, "rms_high_pct": 95.0, "silence_db": -50.0,
        "gap_silence": 2.0, "gap_low": 5.0, "gap_rest": 9.0, "gap_loud": 12.0,
        "gap_kick": 18.0,
        "attack_frames": 1, "release_ms": 200.0,
        "kick_low_hz": 40.0, "kick_high_hz": 120.0,
        "kick_sensitivity": 1.5, "kick_min_interval_ms": 220.0,
        "kick_floor": 0.02,
        # A hit counts as a kick only if it peaks at least this fraction as loud
        # as the strong hits in the previous kick_window_s (plus a short look
        # ahead). Filters out bass notes, which share the kick's frequency band
        # but hit softer.
        "kick_relative": 0.7, "kick_window_s": 3.0, "kick_lookahead_s": 0.6,
        # How sharp the attack must be: the rise over ~12ms as a fraction of
        # the level it reaches. Stops held bass notes counting as kicks.
        "kick_min_rise": 0.25,
        # Volume mode's twitch trigger: a jump of at least this many dB within
        # two frames, while the level (0..1) is at least hit_min_level.
        "hit_rise_db": 4.0, "hit_min_level": 0.3,
    },

    # The ghost "stutter" on each kick.
    "twitch": {"enabled": True, "min_px": 3.0, "max_px": 6.0, "release_ms": 120.0},

    # The hole motif (assets/hole.svg), used instead of the glyph on the 1:1 JPG.
    # Centred where the coil sits at rest; width in pixels.
    "hole": {"file": "hole.svg", "width": 122, "offset_x": 0, "offset_y": 0,
             "glow": True},

    # The 1:1 static JPG (SoundCloud). Overrides the 4:5 layout where they differ.
    "square": {"size": 1080, "tape_centre_y": 603, "name_last_baseline": 1008,
               "jpg_quality": 92},

    # max_mbps caps the bitrate: grain is nearly incompressible, and uncapped a
    # 25s clip is well over 100MB. 12 gives roughly 30-40MB and keeps most of
    # the grain; 16 keeps a little more at ~50MB. Instagram re-compresses well
    # below either, which softens grain whatever is uploaded.
    "export": {"crf": 18, "preset": "medium", "max_mbps": 12.0,
               "preview_seconds": 8.0, "preview_width": 540},
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


# Sensible limits. Outside these the renderer would crash, hang or eat memory,
# so the setting is refused with its name instead.
RANGES = {
    "canvas.width": (64, 4096), "canvas.height": (64, 4096), "canvas.fps": (1, 120),
    "photo.contrast": (0, 5), "photo.brightness": (0, 5),
    "ghost.offset_x": (-200, 200), "ghost.offset_y": (-200, 200),
    "ghost.contrast": (0, 5), "ghost.brightness": (0, 5), "ghost.blur": (0, 50),
    "ghost.opacity": (0, 1),
    "grain.sd": (0, 128), "grain.opacity": (0, 1), "grain.every_frames": (1, 30),
    "grain.pool": (2, 64),
    "vignette.centre_x": (0, 1), "vignette.centre_y": (0, 1),
    "vignette.inner": (0, 0.99), "vignette.edge_alpha": (0, 1),
    "logo.width": (1, 2000),
    "tape.thickness": (1, 1000), "tape.font_size": (4, 400), "tape.angle": (-89, 89),
    "tape.texture_mix": (0, 1), "tape.texture_grey": (0, 1), "tape.texture_grain": (0, 4),
    "name.font_size": (8, 400), "name.min_font_size": (8, 400),
    "name.max_width": (50, 4000), "name.line_gap": (0, 1000),
    "glyph.box_width": (1, 2000), "glyph.box_height": (1, 2000), "glyph.scale": (0.1, 10),
    "glyph.rings.count": (1, 12), "glyph.rings.points": (8, 1000),
    "glyph.rings.rx": (1, 1000), "glyph.rings.ry": (0.5, 1000),
    "glyph.rings.stroke": (0.5, 50), "glyph.rings.radial_sd": (0, 20),
    "glyph.rings.radial_smooth": (1, 20), "glyph.rings.vertical_sd": (0, 20),
    "glyph.rings.centre_y": (-1000, 1000), "glyph.rings.bottom_y": (-1000, 1000),
    "hole.width": (8, 1000), "hole.offset_x": (-1000, 1000), "hole.offset_y": (-1000, 1000),
    "glyph.glow.blur": (0, 50), "glyph.glow.peak_blur": (0, 50),
    "glyph.glow.alpha": (0, 1), "glyph.glow.peak_alpha": (0, 1),
    "audio.clip_seconds": (0.5, 120), "audio.sample_rate": (8000, 192000),
    "audio.rms_low_pct": (0, 100), "audio.rms_high_pct": (0, 100),
    "audio.silence_db": (-200, 0), "audio.attack_frames": (1, 60),
    "audio.release_ms": (1, 5000), "audio.kick_low_hz": (10, 1000),
    "audio.kick_high_hz": (10, 2000), "audio.kick_sensitivity": (0, 10),
    "audio.kick_min_interval_ms": (50, 2000), "audio.kick_floor": (0, 1),
    "audio.kick_relative": (0, 2), "audio.kick_window_s": (0.1, 30),
    "audio.kick_lookahead_s": (0, 5), "audio.kick_min_rise": (0, 1),
    "audio.hit_rise_db": (0.1, 60), "audio.hit_min_level": (0, 1),
    "twitch.min_px": (0, 100), "twitch.max_px": (0, 100), "twitch.release_ms": (1, 2000),
    "square.size": (64, 4096), "square.jpg_quality": (1, 100),
    "export.crf": (0, 51), "export.max_mbps": (0.5, 200),
    "export.preview_seconds": (0.5, 120), "export.preview_width": (64, 4096),
}
PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium",
           "slow", "slower", "veryslow")


def _get(cfg: dict, dotted: str):
    value = cfg
    for part in dotted.split("."):
        value = value[part]
    return value


def validate(cfg: dict) -> dict:
    """Refuse values the renderer can't work with, naming the setting."""
    for path, (lo, hi) in RANGES.items():
        value = _get(cfg, path)
        if not math.isfinite(value) or not lo <= value <= hi:
            raise ConfigError(f"{path} must be between {lo} and {hi} (it's {value}).")
    for path in ("canvas.width", "canvas.height", "square.size", "export.preview_width"):
        if int(_get(cfg, path)) % 2:
            raise ConfigError(f"{path} must be an even number (video needs that).")
    for group in ("colours", "accents"):
        for name, value in cfg[group].items():
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                raise ConfigError(f"{group}.{name} must look like #fffe01.")
    if cfg["accent"] not in cfg["accents"]:
        raise ConfigError(f"accent must be one of: {', '.join(cfg['accents'])}.")
    if cfg["audio"]["drive"] not in ("kicks", "level"):
        raise ConfigError("audio.drive must be kicks or level.")
    if cfg["glyph"]["rings"]["anchor"] not in ("centre", "bottom"):
        raise ConfigError("glyph.rings.anchor must be centre or bottom.")
    if not cfg["tape"]["text"].strip():
        raise ConfigError("tape.text can't be empty.")
    a = cfg["audio"]
    order = ["gap_silence", "gap_low", "gap_rest", "gap_loud", "gap_kick"]
    values = [a[k] for k in order]
    if any(not 0 <= v <= 200 for v in values) or values != sorted(values):
        raise ConfigError("The audio gap values must rise in order: "
                          "gap_silence ≤ gap_low ≤ gap_rest ≤ gap_loud ≤ gap_kick.")
    if a["rms_low_pct"] >= a["rms_high_pct"]:
        raise ConfigError("audio.rms_low_pct must be below audio.rms_high_pct.")
    if a["kick_low_hz"] >= a["kick_high_hz"]:
        raise ConfigError("audio.kick_low_hz must be below audio.kick_high_hz.")
    if cfg["twitch"]["min_px"] > cfg["twitch"]["max_px"]:
        raise ConfigError("twitch.min_px must not be more than twitch.max_px.")
    if cfg["export"]["preset"] not in PRESETS:
        raise ConfigError(f"export.preset must be one of: {', '.join(PRESETS)}.")
    if cfg["glyph"]["mode"] not in ("rings", "none"):
        raise ConfigError("glyph.mode must be rings or none.")
    return cfg


def merge_config(overrides: dict | None) -> dict:
    """The defaults with `overrides` laid over them, checked. Never mutates DEFAULTS."""
    if not overrides:
        return copy.deepcopy(DEFAULTS)
    if not isinstance(overrides, dict):
        raise ConfigError("Settings overrides must be a JSON object.")
    return validate(_merge(DEFAULTS, overrides, ""))


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
