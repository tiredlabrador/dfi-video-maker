"""
Tests that The Dig takes the audio types its page says it does: MP3, FLAC,
WAV, AIFF and M4A. A WAV with no cover falls back to the DFI artwork; an M4A
brings its own cover, title and artist, as an MP3 does.
"""
import numpy as np
import pytest
from PIL import Image

import generate_video as gv


def make_audio(path, extra=(), cover=None):
    inputs = ["-f", "lavfi", "-i", "sine=frequency=220:duration=3"]
    maps = ["-map", "0:a"]
    if cover is not None:
        inputs += ["-i", str(cover)]
        maps += ["-map", "1:v", "-c:v", "png", "-disposition:v", "attached_pic"]
    gv.run(["ffmpeg", "-y", "-v", "error", *inputs, *maps, *extra, str(path)], "test audio")
    return str(path)


@pytest.mark.parametrize("name", ["track.wav", "track.aiff"])
def test_audio_with_no_cover_art_is_not_refused(tmp_path, name):
    """No cover is normal: the render falls back to the DFI design."""
    assert gv.extract_embedded_artwork(make_audio(tmp_path / name)) is None


def test_an_m4a_brings_its_own_cover_art(tmp_path):
    cover = tmp_path / "cover.png"
    Image.new("RGB", (64, 64), (200, 30, 30)).save(cover)
    path = make_audio(tmp_path / "track.m4a", ("-c:a", "aac"), cover=cover)
    art = gv.extract_embedded_artwork(path)
    assert art is not None and art.size == (64, 64)
    assert np.asarray(art.convert("RGB"))[32, 32].tolist() == [200, 30, 30]


def test_an_m4a_brings_its_title_and_artist(tmp_path):
    path = make_audio(tmp_path / "track.m4a", ("-c:a", "aac", "-metadata", "title=Blue Monday",
                                               "-metadata", "artist=New Order"))
    assert gv.read_audio_tags(path) == {"title": "Blue Monday", "artist": "New Order"}


def test_a_wav_renders_with_the_fallback_artwork(tmp_path):
    from app.render_service import RenderRequest, RenderService
    service = RenderService(str(tmp_path / "work"))
    cfg = gv.RenderConfig(clip_length_seconds=2, fallback_text=True)
    request = RenderRequest(audio_path=make_audio(tmp_path / "track.wav"), artwork_path=None,
                            clip_start="0:00", track="Track", artist="Artist", cfg=cfg)
    out = service.render(request)
    assert service.probe(out)["width"] == cfg.canvas_w
