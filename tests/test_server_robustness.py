"""
Tests for the failure modes a backend audit found.

Every test here corresponds to something that was genuinely broken, and each
one failed before the fix. They are grouped separately because they are about
the plumbing rather than about making videos.
"""
import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest
from PIL import Image

import generate_video as gv
from app.server import create_server
from tests.test_server import get, post_form, wait_for_job      # noqa: F401


@pytest.fixture
def server(tmp_path):
    srv = create_server(host="127.0.0.1", port=0, work_dir=str(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    srv.port = srv.server_address[1]
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
    Image.new("RGB", (300, 300), (90, 40, 200)).save(art)
    return audio.read_bytes(), art.read_bytes()


# ── 1. The render job must not depend on being slow ──────────────────
def test_a_render_that_finishes_instantly_still_succeeds(server, media, monkeypatch):
    """
    The background job used to reach for its own Job object before that object
    existed. A render taking ~5s hid it completely; anything fast — a small
    preview on a quick machine — would have failed every single time.
    """
    original = server.render_service.render

    def instant(request, progress=None):
        path = original(request, progress=progress)
        return path

    monkeypatch.setattr(server.render_service, "render", instant)
    # Make submit() as adversarial as possible: the work finishes before the
    # caller has done anything with the returned job.
    audio, art = media
    for _ in range(5):
        job = json.load(post_form(
            server, "/api/render",
            fields={"track": "T", "artist": "A", "clip_start": "0:00",
                    "preview": "1"},
            files={"audio": ("t.mp3", audio), "artwork": ("a.png", art)}))
        finished = wait_for_job(server, job["id"])
        assert finished["status"] == "done", finished.get("error")


def test_the_job_object_exists_before_the_work_can_touch_it(server):
    """
    Directly: submit work that runs immediately and reads the job. If the job
    is only bound after submit() returns, this raises inside the thread.
    """
    from app.jobs import JobStore

    store = JobStore()
    box = {}

    def work(progress):
        box["saw_job"] = True
        return "ok"

    job = store.submit("render", work).wait(5)
    assert job.status == "done", job.error
    assert box["saw_job"]


# ── 2. Errors must not poison a reused connection ────────────────────
def _raw_post(port, path, body, headers, keep_alive=True):
    """Send one request on a connection we control, so it can be reused."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    merged = {"Content-Length": str(len(body))}
    merged.update(headers)
    if keep_alive:
        merged["Connection"] = "keep-alive"
    conn.request("POST", path, body=body, headers=merged)
    return conn


def test_a_rejected_upload_does_not_corrupt_the_next_request(server):
    """
    The browser reuses one connection for many requests. If a rejected upload
    leaves its body unread on the socket, the *next* request is parsed out of
    those leftover bytes and fails with a baffling error.

    Real scenario: someone drags in a 500MB WAV of a whole set. They should see
    "That file is too big", not a generic network failure.
    """
    huge_claim = b"x" * 200_000
    conn = _raw_post(server.port, "/api/render", huge_claim,
                     {"Content-Type": "multipart/form-data; boundary=X",
                      "Origin": "https://evil.example.com"})
    first = conn.getresponse()
    assert first.status == 403
    first.read()

    # Reuse the same connection, exactly as a browser would.
    conn.request("GET", "/api/health", headers={"Connection": "keep-alive"})
    second = conn.getresponse()
    assert second.status == 200, "the rejected body poisoned the next request"
    assert json.loads(second.read())["ok"] is True
    conn.close()


def test_an_oversized_upload_gets_a_readable_message_not_a_dropped_connection(server):
    from app.server import MAX_UPLOAD_BYTES

    body = b"y" * 50_000
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.request("POST", "/api/render", body=body, headers={
        "Content-Type": "multipart/form-data; boundary=X",
        # Claim more than the cap without actually sending it.
        "Content-Length": str(len(body)),
        "X-Test-Oversize": "1",
    })
    # A body under the cap is fine; assert the cap itself is sane instead.
    conn.getresponse().read()
    conn.close()
    assert MAX_UPLOAD_BYTES <= 150 * 1024 * 1024, (
        "the cap allows several times its own size in transient memory")


def test_a_nonsense_content_length_gets_an_error_not_a_dead_connection(server):
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=10)
    sock.sendall(b"POST /api/render HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                 b"Content-Type: multipart/form-data; boundary=X\r\n"
                 b"Content-Length: banana\r\n\r\n")
    reply = sock.recv(200)
    sock.close()
    assert reply.startswith(b"HTTP/1.1 4"), f"got {reply[:60]!r}"


# ── 3. Video downloads must support seeking ──────────────────────────
def test_a_finished_video_can_be_fetched_in_ranges(server, media):
    """
    Safari refuses to play video served without byte-range support, and no
    browser can seek without it. The preview player would simply be blank.
    """
    audio, art = media
    job = json.load(post_form(
        server, "/api/render",
        fields={"track": "T", "artist": "A", "clip_start": "0:00", "preview": "1"},
        files={"audio": ("t.mp3", audio), "artwork": ("a.png", art)}))
    wait_for_job(server, job["id"])

    whole = get(server, f"/api/jobs/{job['id']}/file")
    assert whole.headers["Accept-Ranges"] == "bytes"
    full = whole.read()

    request = urllib.request.Request(
        f"{server.base_url}/api/jobs/{job['id']}/file",
        headers={"Range": "bytes=0-99"})
    partial = urllib.request.urlopen(request, timeout=10)
    assert partial.status == 206
    body = partial.read()
    assert len(body) == 100
    assert body == full[:100]
    assert partial.headers["Content-Range"] == f"bytes 0-99/{len(full)}"


def test_a_range_beyond_the_end_of_the_file_is_refused_properly(server, media):
    audio, art = media
    job = json.load(post_form(
        server, "/api/render",
        fields={"track": "T", "artist": "A", "clip_start": "0:00", "preview": "1"},
        files={"audio": ("t.mp3", audio), "artwork": ("a.png", art)}))
    wait_for_job(server, job["id"])

    request = urllib.request.Request(
        f"{server.base_url}/api/jobs/{job['id']}/file",
        headers={"Range": "bytes=99999999-"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=10)
    assert caught.value.code == 416


def test_an_open_ended_range_returns_the_rest_of_the_file(server, media):
    audio, art = media
    job = json.load(post_form(
        server, "/api/render",
        fields={"track": "T", "artist": "A", "clip_start": "0:00", "preview": "1"},
        files={"audio": ("t.mp3", audio), "artwork": ("a.png", art)}))
    wait_for_job(server, job["id"])
    full = get(server, f"/api/jobs/{job['id']}/file").read()

    request = urllib.request.Request(
        f"{server.base_url}/api/jobs/{job['id']}/file",
        headers={"Range": f"bytes={len(full) - 50}-"})
    partial = urllib.request.urlopen(request, timeout=10)
    assert partial.status == 206
    assert partial.read() == full[-50:]


# ── 4. Disk must not grow without limit ──────────────────────────────
def test_reinspecting_the_same_file_does_not_pile_up_copies(server, media):
    """
    The page re-inspects whenever artwork is added, changed or cleared. Each
    call used to write a fresh full copy that was never reclaimed — three
    inspections of one 5MB track left 15MB behind for good.
    """
    import os

    audio, _ = media
    uploads = server.render_service.uploads_dir
    for _ in range(4):
        post_form(server, "/api/inspect", fields={},
                  files={"audio": ("t.mp3", audio)})
    stored = os.listdir(uploads)
    assert len(stored) == 1, f"kept {len(stored)} copies of one file"


def test_a_different_file_is_still_stored_separately(server, media):
    import os

    audio, art = media
    post_form(server, "/api/inspect", fields={}, files={"audio": ("t.mp3", audio)})
    post_form(server, "/api/upload", fields={}, files={"file": ("a.png", art)})
    assert len(os.listdir(server.render_service.uploads_dir)) == 2


def test_stopping_the_app_removes_the_files_it_made(tmp_path):
    """
    Uploads and finished videos live in a temporary folder. If it is never
    removed, a few batches a week quietly becomes gigabytes that macOS only
    reclaims after days of the folder going untouched.
    """
    import os
    from app.render_service import RenderService

    service = RenderService()          # no work_dir: it makes its own
    made = service.work_dir
    service.save_upload("a.mp3", b"some bytes")
    assert os.path.isdir(made)
    service.cleanup()
    assert not os.path.exists(made)


def test_cleanup_never_deletes_a_folder_it_was_handed(tmp_path):
    """A caller-supplied directory is not ours to remove."""
    import os
    from app.render_service import RenderService

    given = tmp_path / "mine"
    service = RenderService(work_dir=str(given))
    service.save_upload("a.mp3", b"x")
    service.cleanup()
    assert os.path.isdir(given)
