"""
Tests for two things found after Dive In shipped, both in The Dig:

* A finished video whose track or artist had a character outside Latin-1
  (’, –, Ł, emoji) couldn't be downloaded: its name went raw into an HTTP
  header that only allows Latin-1, and the connection died.
* The Dig still used the old Squid Boy font after V4 arrived, so two font
  files were needed. V4 draws The Dig's captions identically.
"""
import json
import os
import threading
import urllib.request
from urllib.parse import unquote

import pytest
from PIL import Image

import generate_video as gv
from app.server import create_server
from tests.test_server import post_form, wait_for_job


@pytest.fixture
def server(tmp_path):
    srv = create_server(host="127.0.0.1", port=0, work_dir=str(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    folder = tmp_path_factory.mktemp("dig")
    audio = folder / "t.mp3"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=5", "-ac", "2", "-ar", "44100", str(audio)], "a")
    art = folder / "a.png"
    Image.new("RGB", (300, 300), (40, 90, 160)).save(art)
    return audio.read_bytes(), art.read_bytes()


def test_a_dig_video_with_a_curly_quote_or_polish_letter_downloads(server, media):
    audio, art = media
    job = json.load(post_form(
        server, "/api/render",
        fields={"track": "Mike’s Łukasz – Dub 😀", "artist": "Ol’ Dirty", "clip_start": "0:00",
                "preview": "1"},
        files={"audio": ("t.mp3", audio), "artwork": ("a.png", art)}))
    done = wait_for_job(server, job["id"])
    assert done["status"] == "done", done.get("error")
    response = urllib.request.urlopen(server.base_url + done["download_url"], timeout=30)
    assert response.read()[4:8] == b"ftyp"
    header = response.headers["Content-Disposition"]
    # A plain fallback name, plus the real one for browsers that read it.
    assert "filename*=UTF-8''" in header
    assert "Mike’s Łukasz – Dub 😀" in unquote(header.split("filename*=UTF-8''")[1])


def test_the_dig_uses_squid_boy_v4(server):
    assert os.path.basename(server.make_config().font_path or "") == "SquidBoyV4-Regular.otf"


def test_dive_in_no_longer_falls_back_to_the_old_font():
    from app.divein.compose import font_path
    assert font_path("NoSuchFont.otf") is None
