"""
Tests for the local web server.

This runs a real server on a real port and talks to it over real HTTP, because
the things most likely to break here — routing, upload parsing, content types,
and refusing requests from other websites — only exist at that level.
"""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest
from PIL import Image

import generate_video as gv
from app.server import create_server


@pytest.fixture(scope="module")
def audio_and_art(tmp_path_factory):
    folder = tmp_path_factory.mktemp("media")
    audio = folder / "tone.mp3"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=6", "-ac", "2", "-ar", "44100",
            str(audio)], "test audio")
    art = folder / "art.png"
    Image.new("RGB", (400, 400), (10, 200, 120)).save(art)
    return audio.read_bytes(), art.read_bytes()


@pytest.fixture
def server(tmp_path):
    srv = create_server(host="127.0.0.1", port=0, work_dir=str(tmp_path))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


def get(server, path, headers=None):
    request = urllib.request.Request(server.base_url + path,
                                     headers=headers or {})
    return urllib.request.urlopen(request, timeout=10)


def post_form(server, path, fields, files, headers=None):
    """POST a multipart form the way a browser would."""
    boundary = "----dfitest0123456789"
    body = b""
    for name, value in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{name}"\r\n\r\n{value}\r\n').encode()
    for name, (filename, content) in files.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{name}"; filename="{filename}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n").encode()
        body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    merged = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    merged.update(headers or {})
    request = urllib.request.Request(server.base_url + path, data=body,
                                     headers=merged, method="POST")
    return urllib.request.urlopen(request, timeout=30)


def wait_for_job(server, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = json.load(get(server, f"/api/jobs/{job_id}"))
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} never finished")


# -- the page itself ------------------------------------------------------
def test_the_home_page_is_served(server):
    response = get(server, "/")
    assert response.status == 200
    assert "text/html" in response.headers["Content-Type"]
    assert b"DFI" in response.read()


def test_static_files_are_served_with_the_right_content_type(server):
    assert "javascript" in get(server, "/static/app.js").headers["Content-Type"]
    assert "css" in get(server, "/static/style.css").headers["Content-Type"]


def test_an_unknown_path_is_a_404(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        get(server, "/nope")
    assert caught.value.code == 404


def test_static_files_cannot_be_used_to_read_the_rest_of_the_disk(server):
    """`/static/../../secrets` must not escape the static folder."""
    with pytest.raises(urllib.error.HTTPError) as caught:
        get(server, "/static/..%2f..%2fgenerate_video.py")
    assert caught.value.code in (403, 404)


# -- health ---------------------------------------------------------------
def test_health_reports_whether_ffmpeg_is_available(server):
    health = json.load(get(server, "/api/health"))
    assert health["ok"] is True
    assert health["ffmpeg"] is True


# -- rendering ------------------------------------------------------------
def test_posting_a_render_returns_a_job_that_finishes(server, audio_and_art):
    audio, art = audio_and_art
    response = post_form(
        server, "/api/render",
        fields={"track": "Fantasy", "artist": "Mariah Carey",
                "clip_start": "0:01", "preview": "1"},
        files={"audio": ("tone.mp3", audio), "artwork": ("art.png", art)},
    )
    assert response.status == 202
    job = json.load(response)
    assert job["status"] in ("queued", "running")

    finished = wait_for_job(server, job["id"])
    assert finished["status"] == "done", finished.get("error")
    assert finished["progress"] == 1.0


def test_the_finished_video_can_be_downloaded_and_is_a_real_mp4(server, audio_and_art):
    audio, art = audio_and_art
    job = json.load(post_form(
        server, "/api/render",
        fields={"track": "T", "artist": "A", "clip_start": "0:00", "preview": "1"},
        files={"audio": ("tone.mp3", audio), "artwork": ("art.png", art)},
    ))
    wait_for_job(server, job["id"])
    response = get(server, f"/api/jobs/{job['id']}/file")
    assert response.headers["Content-Type"] == "video/mp4"
    data = response.read()
    assert len(data) > 1000
    assert data[4:8] == b"ftyp", "not an MP4 container"


def test_the_job_reports_what_was_actually_produced(server, audio_and_art):
    """Every render is probed, so the UI can show real facts, not assumptions."""
    audio, art = audio_and_art
    job = json.load(post_form(
        server, "/api/render",
        fields={"track": "T", "artist": "A", "clip_start": "0:00", "preview": "1"},
        files={"audio": ("tone.mp3", audio), "artwork": ("art.png", art)},
    ))
    finished = wait_for_job(server, job["id"])
    probe = finished["probe"]
    assert probe["video_codec"] == "h264"
    assert probe["pix_fmt"] == "yuv420p"
    assert probe["audio_codec"] == "aac"
    assert probe["channels"] == 2


def test_a_track_with_no_artwork_falls_back_to_the_label_design(server, audio_and_art):
    """
    The DFI fallback image is configured, so a track with no cover art still
    renders rather than failing. This is the behaviour the team relies on.
    """
    audio, _ = audio_and_art
    job = json.load(post_form(
        server, "/api/render",
        fields={"track": "T", "artist": "A", "clip_start": "0:00", "preview": "1"},
        files={"audio": ("tone.mp3", audio)},
    ))
    finished = wait_for_job(server, job["id"])
    assert finished["status"] == "done", finished.get("error")


def test_with_no_fallback_configured_a_missing_artwork_says_so_plainly(tmp_path,
                                                                       audio_and_art):
    """
    And when there is genuinely nothing to show, the message must be one a
    person can act on — not a stack trace.
    """
    srv = create_server(host="127.0.0.1", port=0, work_dir=str(tmp_path),
                        config_overrides={"fallback_path": None})
    # An override of None means "no fallback"; make_config drops None values, so
    # force it off explicitly here.
    srv.make_config = lambda preview=False: gv.RenderConfig(
        canvas_w=216, canvas_h=270, circle_diameter=166, hole_diameter=6,
        clip_length_seconds=2, spin_period_seconds=1, fps=10, caption=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        audio, _ = audio_and_art
        job = json.load(post_form(
            srv, "/api/render",
            fields={"track": "T", "artist": "A", "clip_start": "0:00"},
            files={"audio": ("tone.mp3", audio)},
        ))
        finished = wait_for_job(srv, job["id"])
        assert finished["status"] == "error"
        assert "artwork" in finished["error"].lower()
        assert "Traceback" not in finished["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_render_with_no_audio_at_all_is_rejected_immediately(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_form(server, "/api/render",
                  fields={"track": "T", "artist": "A", "clip_start": "0:00"},
                  files={})
    assert caught.value.code == 400
    assert "audio" in caught.value.read().decode().lower()


def test_asking_for_an_unknown_job_is_a_404(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        get(server, "/api/jobs/doesnotexist")
    assert caught.value.code == 404


def test_the_file_of_an_unfinished_job_is_not_offered(server, audio_and_art):
    audio, art = audio_and_art
    job = json.load(post_form(
        server, "/api/render",
        fields={"track": "T", "artist": "A", "clip_start": "0:00", "preview": "1"},
        files={"audio": ("tone.mp3", audio), "artwork": ("art.png", art)},
    ))
    # Immediately, before it can possibly have finished.
    try:
        response = get(server, f"/api/jobs/{job['id']}/file")
        assert response.status == 200      # it finished faster than we asked
    except urllib.error.HTTPError as error:
        assert error.code == 409


# -- keeping other websites out -------------------------------------------
def test_a_request_from_another_website_is_refused(server, audio_and_art):
    """
    A page on the open internet can POST to http://127.0.0.1 in the background.
    The server must not act on requests that did not come from its own page.
    """
    audio, art = audio_and_art
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_form(server, "/api/render",
                  fields={"track": "T", "artist": "A", "clip_start": "0:00"},
                  files={"audio": ("tone.mp3", audio), "artwork": ("art.png", art)},
                  headers={"Origin": "https://evil.example.com"})
    assert caught.value.code == 403


def test_a_request_with_a_foreign_host_header_is_refused(server):
    """Guards against DNS rebinding: a domain that resolves to 127.0.0.1."""
    with pytest.raises(urllib.error.HTTPError) as caught:
        get(server, "/api/health", headers={"Host": "evil.example.com"})
    assert caught.value.code == 403


def test_the_pages_own_requests_are_allowed(server):
    origin = server.base_url
    assert get(server, "/api/health", headers={"Origin": origin}).status == 200
