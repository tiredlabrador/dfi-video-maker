"""
Looking inside an audio file before rendering it.

Two things matter here. The obvious one is convenience: reading the title and
artist out of the file's tags saves typing them. The one that actually earns
its keep is showing you the artwork the video *will* use, before you commit —
that check is what caught a "TORRENT DAY" advertising banner embedded as the
cover art of a bootleg, which would otherwise have gone out on Instagram.
"""
from __future__ import annotations

import base64
import io

import generate_video as gv


def _duration_label(seconds: float) -> str:
    minutes, remainder = divmod(int(round(seconds)), 60)
    return f"{minutes}:{remainder:02d}"


def inspect_audio(path: str, has_override_artwork: bool = False,
                  fallback_available: bool = True) -> dict:
    """
    Report what we can learn about one audio file.

    Never raises for a bad file: an unreadable file is a normal thing for a
    person to pick by mistake, so it comes back as `readable: False` with a
    message rather than as a crash.
    """
    result = {
        "readable": True,
        "error": "",
        "track": "",
        "artist": "",
        "duration": 0.0,
        "duration_label": "0:00",
        "has_artwork": False,
        "artwork_source": "none",
        "artwork_preview": "",
    }

    try:
        tags = gv.read_audio_tags(path) or {}
    except Exception as exc:                        # noqa: BLE001
        tags = {}
        result["readable"] = False
        result["error"] = f"That file could not be read as audio ({exc})."

    result["track"] = (tags.get("title") or "").strip()
    result["artist"] = (tags.get("artist") or "").strip()

    try:
        from mutagen import File as MutagenFile
        media = MutagenFile(path)
        if media is None:
            result["readable"] = False
            result["error"] = result["error"] or (
                "That file is not audio we can read. Try an MP3, WAV, FLAC or M4A."
            )
        elif getattr(media, "info", None) is not None:
            result["duration"] = float(getattr(media.info, "length", 0.0) or 0.0)
            result["duration_label"] = _duration_label(result["duration"])
    except Exception as exc:                        # noqa: BLE001
        result["readable"] = False
        result["error"] = result["error"] or f"Could not read that file ({exc})."

    # Which artwork will the finished video actually use? Same order as the
    # engine, so what is shown here is what gets rendered.
    if has_override_artwork:
        result["artwork_source"] = "override"
        result["has_artwork"] = True
    else:
        embedded = None
        try:
            embedded = gv.extract_embedded_artwork(path)
        except Exception:                           # noqa: BLE001
            embedded = None
        if embedded is not None:
            result["has_artwork"] = True
            result["artwork_source"] = "embedded"
            result["artwork_preview"] = _thumbnail_data_url(embedded)
        elif fallback_available:
            result["artwork_source"] = "fallback"
        else:
            result["artwork_source"] = "none"

    return result


def _thumbnail_data_url(image, size: int = 320) -> str:
    """Shrink artwork to something a web page can show inline."""
    from PIL import Image

    if not isinstance(image, Image.Image):
        try:
            image = Image.open(io.BytesIO(image))
        except Exception:                           # noqa: BLE001
            return ""
    thumb = image.convert("RGB").copy()
    thumb.thumbnail((size, size), Image.LANCZOS)
    buffer = io.BytesIO()
    thumb.save(buffer, format="JPEG", quality=82)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
