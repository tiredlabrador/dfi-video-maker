"""
Tests for the batch endpoints.

Files reach the server once, when they are chosen (that is what fills in the
track and artist boxes). Starting the batch then refers to them by token rather
than sending ten MP3s a second time.
"""
import io
import json
import threading
import urllib.error
import urllib.request
import zipfile

import pytest
from PIL import Image

import generate_video as gv
from app.server import create_server
from tests.test_server import post_form, get      # noqa: F401


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
    folder = tmp_path_factory.mktemp("media")
    audio = folder / "tone.mp3"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=5", "-ac", "2", "-ar", "44100",
            str(audio)], "test audio")
    art = folder / "a.png"
    Image.new("RGB", (400, 400), (30, 160, 90)).save(art)
    return audio.read_bytes(), art.read_bytes()


def post_json(server, path, payload):
    request = urllib.request.Request(
        server.base_url + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.load(urllib.request.urlopen(request, timeout=30))


def upload(server, audio):
    """Choose a file, the way the page does, and get back its token."""
    return json.load(post_form(server, "/api/inspect", fields={},
                               files={"audio": ("t.mp3", audio)}))["upload_token"]


def wait(server, job_id, timeout=180):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = json.load(get(server, f"/api/jobs/{job_id}"))
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.2)
    raise AssertionError("batch never finished")


def test_a_batch_of_three_renders_all_three(server, media):
    audio, _ = media
    tokens = [upload(server, audio) for _ in range(3)]
    job = post_json(server, "/api/batch", {"preview": True, "items": [
        {"upload_token": t, "track": f"Track {i}", "artist": "A",
         "clip_start": "0:00"} for i, t in enumerate(tokens, 1)]})
    finished = wait(server, job["id"])
    assert finished["status"] == "done"
    assert len(finished["items"]) == 3
    assert all(item["status"] == "done" for item in finished["items"])


def test_the_batch_reports_each_track_separately_as_it_goes(server, media):
    audio, _ = media
    tokens = [upload(server, audio) for _ in range(2)]
    job = post_json(server, "/api/batch", {"preview": True, "items": [
        {"upload_token": t, "track": f"T{i}", "artist": "A", "clip_start": "0:00"}
        for i, t in enumerate(tokens, 1)]})
    finished = wait(server, job["id"])
    first = finished["items"][0]
    assert first["filename"].startswith("01")
    assert first["download_url"]
    assert first["probe"]["video_codec"] == "h264"


def test_a_bad_token_is_refused_before_anything_starts(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/batch", {"items": [
            {"upload_token": "../../../../etc/passwd", "track": "T",
             "artist": "A", "clip_start": "0:00"}]})
    assert caught.value.code == 400


def test_an_unknown_token_is_refused(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/batch", {"items": [
            {"upload_token": "deadbeef.mp3", "track": "T", "artist": "A",
             "clip_start": "0:00"}]})
    assert caught.value.code == 400


def test_an_empty_batch_is_refused_with_a_readable_message(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/batch", {"items": []})
    assert caught.value.code == 400
    assert "no tracks" in caught.value.read().decode().lower()


def test_the_whole_batch_downloads_as_one_zip(server, media):
    audio, _ = media
    tokens = [upload(server, audio) for _ in range(2)]
    job = post_json(server, "/api/batch", {"preview": True, "items": [
        {"upload_token": t, "track": f"T{i}", "artist": "A", "clip_start": "0:00"}
        for i, t in enumerate(tokens, 1)]})
    wait(server, job["id"])

    response = get(server, f"/api/jobs/{job['id']}/zip")
    assert response.headers["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.read()))
    assert archive.testzip() is None
    assert len(archive.namelist()) == 2


def test_artwork_can_be_uploaded_separately_and_used(server, media):
    audio, art = media
    audio_token = upload(server, audio)
    art_token = json.load(post_form(server, "/api/upload", fields={},
                                    files={"file": ("a.png", art)}))["upload_token"]
    job = post_json(server, "/api/batch", {"preview": True, "items": [
        {"upload_token": audio_token, "artwork_token": art_token,
         "track": "T", "artist": "A", "clip_start": "0:00"}]})
    finished = wait(server, job["id"])
    assert finished["items"][0]["status"] == "done"


def test_a_batch_request_from_another_website_is_refused(server, media):
    audio, _ = media
    token = upload(server, audio)
    request = urllib.request.Request(
        server.base_url + "/api/batch",
        data=json.dumps({"items": [{"upload_token": token, "track": "T",
                                    "artist": "A", "clip_start": "0:00"}]}).encode(),
        headers={"Content-Type": "application/json",
                 "Origin": "https://evil.example.com"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=10)
    assert caught.value.code == 403


def test_finished_tracks_appear_while_the_batch_is_still_running(server, media):
    """
    A batch of ten takes a minute. Watching the first ones appear as they land
    is the difference between "it's working" and "has this frozen?".
    """
    import time

    audio, _ = media
    tokens = [upload(server, audio) for _ in range(4)]
    job = post_json(server, "/api/batch", {"preview": True, "items": [
        {"upload_token": t, "track": f"T{i}", "artist": "A", "clip_start": "0:00"}
        for i, t in enumerate(tokens, 1)]})

    saw_partial = False
    deadline = time.time() + 120
    while time.time() < deadline:
        state = json.load(get(server, f"/api/jobs/{job['id']}"))
        done = [i for i in state["items"] if i["status"] == "done"]
        if state["status"] == "running" and 0 < len(done) < 4:
            saw_partial = True
        if state["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert saw_partial, "no partial results were ever visible during the batch"
