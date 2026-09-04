"""
Tests for what happens on a machine that is not this one.

A fresh clone of the public repo is missing the Squid Boy font, because it is
licensed and committing it would be redistributing it. The app has to survive
that: captions in a substitute font are a small problem, a crash is a big one.
"""
import json
import threading
import urllib.request

import pytest
from PIL import Image

import generate_video as gv
from app.server import create_server
from tests.test_server import post_form, wait_for_job     # noqa: F401


@pytest.fixture
def media(tmp_path):
    audio = tmp_path / "tone.mp3"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=5", "-ac", "2", "-ar", "44100",
            str(audio)], "test audio")
    art = tmp_path / "art.png"
    Image.new("RGB", (400, 400), (10, 120, 200)).save(art)
    return audio.read_bytes(), art.read_bytes()


def serve(tmp_path, **overrides):
    srv = create_server(host="127.0.0.1", port=0, work_dir=str(tmp_path),
                        config_overrides=overrides)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    return srv


def test_the_app_starts_and_renders_with_no_brand_font_installed(tmp_path, media):
    """The licensed font is absent from a fresh clone. It must not be fatal."""
    srv = serve(tmp_path, font_path=None)
    try:
        assert srv.make_config().font_path is None
        audio, art = media
        job = json.load(post_form(
            srv, "/api/render",
            fields={"track": "A Long Track Title Here", "artist": "Someone",
                    "clip_start": "0:00", "preview": "1"},
            files={"audio": ("t.mp3", audio), "artwork": ("a.png", art)}))
        finished = wait_for_job(srv, job["id"])
        assert finished["status"] == "done", finished.get("error")
    finally:
        srv.shutdown(); srv.server_close()


def test_missing_assets_are_reported_as_absent_not_as_broken_paths(tmp_path):
    """
    _asset() must return None for a file that isn't there, never a path that
    only exists on the machine it was written on.
    """
    srv = serve(tmp_path)
    try:
        for value in (srv._asset("does-not-exist.png"),
                      srv._asset("fonts/NoSuchFont.otf")):
            assert value is None
    finally:
        srv.shutdown(); srv.server_close()


def test_the_health_check_reports_which_optional_assets_are_missing(tmp_path):
    """
    The page should be able to tell the user their captions will look wrong,
    rather than leaving them to notice it in a finished video.
    """
    srv = serve(tmp_path, font_path=None)
    try:
        health = json.load(urllib.request.urlopen(srv.base_url + "/api/health"))
        assert health["brand_font"] is False
    finally:
        srv.shutdown(); srv.server_close()
