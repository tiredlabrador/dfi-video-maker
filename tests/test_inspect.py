"""
Tests for /api/inspect.

When you choose an audio file the app reads its tags and its embedded cover art
straight away, so the track and artist boxes fill themselves in and you can see
which artwork the video will actually use before committing to a render. That
last part is the bit that earns its keep: it is what caught a "TORRENT DAY"
banner embedded as cover art in a bootleg.
"""
import base64
import json
import threading
import urllib.error
import urllib.request

import pytest
from PIL import Image

import generate_video as gv
from tests.test_server import post_form, get      # noqa: F401 - shared helpers
from app.server import create_server


@pytest.fixture
def server(tmp_path):
    srv = create_server(host="127.0.0.1", port=0, work_dir=str(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def tagged_mp3(tmp_path):
    """An MP3 carrying a title, an artist and a cover image."""
    from mutagen.id3 import ID3, APIC, TIT2, TPE1
    from mutagen.mp3 import MP3

    path = tmp_path / "tagged.mp3"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=4", "-ac", "2", "-ar", "44100",
            str(path)], "tagged audio")

    art = tmp_path / "cover.jpg"
    Image.new("RGB", (500, 500), (240, 60, 20)).save(art)

    audio = MP3(str(path))
    if audio.tags is None:
        audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="Fantasy"))
    audio.tags.add(TPE1(encoding=3, text="Mariah Carey"))
    audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover",
                        data=art.read_bytes()))
    audio.save()
    return path.read_bytes()


def test_inspect_reads_the_title_and_artist_from_the_tags(server, tagged_mp3):
    result = json.load(post_form(server, "/api/inspect", fields={},
                                 files={"audio": ("tagged.mp3", tagged_mp3)}))
    assert result["track"] == "Fantasy"
    assert result["artist"] == "Mariah Carey"


def test_inspect_returns_the_embedded_cover_so_it_can_be_shown(server, tagged_mp3):
    result = json.load(post_form(server, "/api/inspect", fields={},
                                 files={"audio": ("tagged.mp3", tagged_mp3)}))
    assert result["has_artwork"] is True
    assert result["artwork_source"] == "embedded"
    # A data: URL the page can drop straight into an <img>.
    assert result["artwork_preview"].startswith("data:image/")
    payload = result["artwork_preview"].split(",", 1)[1]
    assert len(base64.b64decode(payload)) > 100


def test_inspect_reports_the_length_of_the_track(server, tagged_mp3):
    result = json.load(post_form(server, "/api/inspect", fields={},
                                 files={"audio": ("tagged.mp3", tagged_mp3)}))
    assert result["duration"] == pytest.approx(4.0, abs=0.5)
    assert result["duration_label"] == "0:04"


def test_an_untagged_file_says_so_rather_than_guessing(server):
    import io
    plain = io.BytesIO()
    import subprocess, tempfile, os
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "plain.mp3")
        gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                "-i", "sine=frequency=300:duration=2", "-ac", "2",
                "-ar", "44100", path], "plain audio")
        content = open(path, "rb").read()
    result = json.load(post_form(server, "/api/inspect", fields={},
                                 files={"audio": ("plain.mp3", content)}))
    assert result["track"] == ""
    assert result["artist"] == ""
    assert result["has_artwork"] is False
    assert result["artwork_source"] == "fallback"


def test_inspect_without_a_file_is_a_clear_400(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_form(server, "/api/inspect", fields={}, files={})
    assert caught.value.code == 400


def test_a_file_that_is_not_audio_at_all_is_reported_not_crashed(server):
    result = json.load(post_form(server, "/api/inspect", fields={},
                                 files={"audio": ("notmusic.mp3", b"NOT AUDIO")}))
    assert result["readable"] is False
    assert result["error"]
