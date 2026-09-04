"""
Tests for the render service: the bridge between the web layer and the engine.

This is the layer that turns "the user picked these files and typed 1:30" into
a finished MP4 on disk, with progress reported along the way. It owns the
temporary working directory and the output naming.
"""
import os

import pytest
from PIL import Image

import generate_video as gv
from app.render_service import RenderRequest, RenderService


@pytest.fixture
def service(tmp_path):
    return RenderService(work_dir=str(tmp_path))


@pytest.fixture
def audio(tmp_path):
    """A short real audio file, made with ffmpeg so the codecs are genuine."""
    path = tmp_path / "tone.mp3"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=6", "-ac", "2", "-ar", "44100",
            str(path)], "test audio")
    return str(path)


@pytest.fixture
def artwork(tmp_path):
    path = tmp_path / "art.png"
    Image.new("RGB", (600, 600), (200, 40, 90)).save(path)
    return str(path)


def small_cfg(**kw):
    """A tiny, fast render config. Shape is what matters here, not size."""
    # Both dimensions must be EVEN: H.264 with yuv420p stores colour at half
    # resolution, so an odd width or height has nowhere to put the last row.
    # 216x270 keeps the real 4:5 shape and renders in well under a second.
    defaults = dict(canvas_w=216, canvas_h=270, circle_diameter=166,
                    hole_diameter=6, clip_length_seconds=2,
                    spin_period_seconds=1, fps=10, caption=False)
    defaults.update(kw)
    return gv.RenderConfig(**defaults)


def test_saving_an_upload_puts_the_bytes_on_disk(service):
    path = service.save_upload("track.mp3", b"AUDIOBYTES")
    assert os.path.exists(path)
    assert open(path, "rb").read() == b"AUDIOBYTES"


def test_saved_uploads_keep_their_extension_so_ffmpeg_can_sniff_the_format(service):
    assert service.save_upload("My Track.mp3", b"x").endswith(".mp3")
    assert service.save_upload("cover.PNG", b"x").endswith(".PNG")


def test_a_hostile_filename_cannot_escape_the_working_directory(service):
    """A filename is untrusted input, even from a local browser."""
    path = service.save_upload("../../../../etc/passwd", b"x")
    assert os.path.realpath(path).startswith(os.path.realpath(service.work_dir))


def test_two_uploads_with_the_same_name_do_not_overwrite_each_other(service):
    first = service.save_upload("a.mp3", b"FIRST")
    second = service.save_upload("a.mp3", b"SECOND")
    assert first != second
    assert open(first, "rb").read() == b"FIRST"


def test_rendering_produces_a_playable_mp4(service, audio, artwork):
    request = RenderRequest(audio_path=audio, artwork_path=artwork,
                            clip_start="0:01", track="Fantasy",
                            artist="Mariah Carey", cfg=small_cfg())
    out = service.render(request, progress=lambda f, m="": None)
    assert os.path.exists(out)
    assert out.endswith(".mp4")
    assert os.path.getsize(out) > 1000


def test_the_rendered_file_matches_the_spec(service, audio, artwork):
    cfg = small_cfg()
    request = RenderRequest(audio_path=audio, artwork_path=artwork,
                            clip_start="0:00", track="T", artist="A", cfg=cfg)
    out = service.render(request, progress=lambda f, m="": None)
    probe = service.probe(out)
    assert probe["width"] == cfg.canvas_w
    assert probe["height"] == cfg.canvas_h
    assert probe["video_codec"] == "h264"
    assert probe["pix_fmt"] == "yuv420p"
    assert probe["audio_codec"] == "aac"
    assert probe["channels"] == 2
    assert probe["sample_rate"] == 44100
    assert probe["duration"] == pytest.approx(cfg.clip_length_seconds, abs=0.05)


def test_progress_is_reported_from_start_to_finish(service, audio, artwork):
    seen = []
    request = RenderRequest(audio_path=audio, artwork_path=artwork,
                            clip_start="0:00", track="T", artist="A",
                            cfg=small_cfg())
    service.render(request, progress=lambda f, m="": seen.append(f))
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(1.0)
    assert len(seen) > 3, "progress should be reported more than a couple of times"


def test_progress_messages_are_plain_english(service, audio, artwork):
    messages = []
    request = RenderRequest(audio_path=audio, artwork_path=artwork,
                            clip_start="0:00", track="T", artist="A",
                            cfg=small_cfg())
    service.render(request, progress=lambda f, m="": m and messages.append(m))
    assert messages
    for message in messages:
        assert message[0].isupper(), f"{message!r} should read like a sentence"
        assert "_" not in message, f"{message!r} leaks a variable name"


def test_no_artwork_anywhere_gives_an_error_a_human_can_act_on(service, audio):
    request = RenderRequest(audio_path=audio, artwork_path=None,
                            clip_start="0:00", track="T", artist="A",
                            cfg=small_cfg())
    with pytest.raises(gv.NoArtworkError):
        service.render(request, progress=lambda f, m="": None)


def test_the_output_is_named_after_the_artist_and_track(service, audio, artwork):
    request = RenderRequest(audio_path=audio, artwork_path=artwork,
                            clip_start="0:00", track="Love Story",
                            artist="Layo & Bushwacka!", cfg=small_cfg())
    out = service.render(request, progress=lambda f, m="": None)
    name = os.path.basename(out)
    assert "Love Story" in name
    assert "Layo" in name
    assert "/" not in name


def test_clip_start_actually_offsets_the_audio(service, audio, artwork):
    """A clip starting later must not be the same bytes as one starting at zero."""
    cfg = small_cfg()
    base = service.render(
        RenderRequest(audio_path=audio, artwork_path=artwork, clip_start="0:00",
                      track="A", artist="X", cfg=cfg),
        progress=lambda f, m="": None)
    later = service.render(
        RenderRequest(audio_path=audio, artwork_path=artwork, clip_start="0:03",
                      track="B", artist="X", cfg=cfg),
        progress=lambda f, m="": None)
    assert open(base, "rb").read() != open(later, "rb").read()
