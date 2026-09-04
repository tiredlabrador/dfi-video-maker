"""
The local web server.

Runs on your own machine and serves one page to your own browser. Nothing is
uploaded anywhere: the "upload" is a copy from one folder on your disk to
another, which is why it is instant.

Built on Python's own `http.server` rather than a web framework. That is a
deliberate trade: the code here is a little longer, but installing this on
someone else's laptop needs no extra packages beyond what the engine already
uses, and there is no framework version to go stale.
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import generate_video as gv
from app.inspector import inspect_audio
from app.jobs import JobStore
from app.multipart import ParseError, parse_multipart
from app.render_service import RenderRequest, RenderService

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Refuse anything larger than this, so a mistake cannot eat all the memory.
MAX_UPLOAD_BYTES = 400 * 1024 * 1024      # 400 MB


class _Handler(BaseHTTPRequestHandler):
    server_version = "DFIVideoMaker/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):      # quieter than the default
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str,
              extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # This page is served to one local browser and must never be embedded
        # or cached by anything else.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # -- keeping other websites out ---------------------------------------
    def _is_local_request(self) -> bool:
        """
        Decide whether this request really came from our own page.

        Any website you happen to have open can quietly send requests to
        http://127.0.0.1 in the background. It cannot normally *read* the reply,
        but it can make things happen — so requests that announce a foreign
        origin, or that reach us via a domain name rather than the loopback
        address, are refused outright. This is the whole of the app's security
        model, and it is the reason the server binds to 127.0.0.1 only.
        """
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1", ""):
            return False
        origin = self.headers.get("Origin")
        if origin:
            hostname = urlparse(origin).hostname
            if hostname not in ("127.0.0.1", "localhost", "::1"):
                return False
        return True

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        if not self._is_local_request():
            return self._error(403, "This server only answers your own browser.")
        path = urlparse(self.path).path

        if path == "/":
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path == "/api/health":
            return self._json(200, {
                "ok": True,
                "ffmpeg": shutil.which("ffmpeg") is not None,
                "ffprobe": shutil.which("ffprobe") is not None,
            })
        if path == "/api/jobs":
            return self._json(200, [self._job_payload(j)
                                    for j in self.server.jobs.list()])
        if path.startswith("/api/jobs/"):
            rest = path[len("/api/jobs/"):]
            if rest.endswith("/file"):
                return self._serve_job_file(rest[:-len("/file")])
            job = self.server.jobs.get(rest)
            if job is None:
                return self._error(404, "No such job.")
            return self._json(200, self._job_payload(job))
        return self._error(404, "Not found.")

    def do_POST(self):
        if not self._is_local_request():
            return self._error(403, "This server only answers your own browser.")
        path = urlparse(self.path).path
        if path == "/api/render":
            return self._start_render()
        if path == "/api/inspect":
            return self._inspect()
        return self._error(404, "Not found.")

    def _read_form(self):
        """Read and parse a multipart body, or send the error and return None."""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._error(400, "The request had no body.")
            return None
        if length > MAX_UPLOAD_BYTES:
            self._error(413, "That file is too big.")
            return None
        try:
            return parse_multipart(self.rfile.read(length),
                                   self.headers.get("Content-Type", ""))
        except ParseError as exc:
            self._error(400, str(exc))
            return None

    def _inspect(self) -> None:
        form = self._read_form()
        if form is None:
            return
        _, files = form
        if "audio" not in files:
            return self._error(400, "No audio file was chosen.")
        service: RenderService = self.server.render_service
        path = service.save_upload(files["audio"].filename,
                                   files["audio"].content)
        result = inspect_audio(
            path,
            has_override_artwork="artwork" in files,
            fallback_available=self.server.make_config().fallback_path is not None,
        )
        # Hand back the stored path so a following render can reuse the upload
        # instead of sending the same 10MB up the pipe a second time.
        result["upload_token"] = os.path.basename(path)
        return self._json(200, result)

    # -- static files -----------------------------------------------------
    def _serve_static(self, relative: str) -> None:
        # `relative` comes from the URL, so it is untrusted. Resolve it and
        # confirm the result is genuinely inside the static folder before
        # opening anything.
        candidate = os.path.realpath(os.path.join(STATIC_DIR, unquote(relative)))
        if not candidate.startswith(os.path.realpath(STATIC_DIR) + os.sep):
            return self._error(403, "Forbidden.")
        if not os.path.isfile(candidate):
            return self._error(404, "Not found.")
        content_type, _ = mimetypes.guess_type(candidate)
        with open(candidate, "rb") as handle:
            self._send(200, handle.read(),
                       content_type or "application/octet-stream")

    # -- jobs -------------------------------------------------------------
    def _job_payload(self, job) -> dict:
        payload = job.as_dict()
        payload["probe"] = getattr(job, "probe", None)
        if job.status == "done":
            payload["download_url"] = f"/api/jobs/{job.id}/file"
            payload["filename"] = os.path.basename(job.result or "")
        return payload

    def _serve_job_file(self, job_id: str) -> None:
        job = self.server.jobs.get(job_id)
        if job is None:
            return self._error(404, "No such job.")
        if job.status != "done" or not job.result:
            return self._error(409, "That video is not finished yet.")
        if not os.path.isfile(job.result):
            return self._error(404, "The finished file is no longer on disk.")
        with open(job.result, "rb") as handle:
            body = handle.read()
        name = os.path.basename(job.result)
        self._send(200, body, "video/mp4",
                   {"Content-Disposition": f'inline; filename="{name}"'})

    def _start_render(self) -> None:
        form = self._read_form()
        if form is None:
            return
        fields, files = form

        if "audio" not in files:
            return self._error(400, "No audio file was chosen.")

        service: RenderService = self.server.render_service
        audio_path = service.save_upload(files["audio"].filename,
                                         files["audio"].content)
        artwork_path = None
        if "artwork" in files:
            artwork_path = service.save_upload(files["artwork"].filename,
                                               files["artwork"].content)

        track = fields.get("track", "").strip() or "Untitled"
        artist = fields.get("artist", "").strip() or "Unknown artist"
        cfg = self.server.make_config(preview=fields.get("preview") == "1")

        request = RenderRequest(
            audio_path=audio_path, artwork_path=artwork_path,
            clip_start=fields.get("clip_start", "0:00").strip() or "0:00",
            track=track, artist=artist, cfg=cfg,
        )

        def work(progress):
            path = service.render(request, progress=progress)
            # Probe every render: the difference between "it did not crash"
            # and "the file really is what we promised".
            job.probe = service.probe(path)
            return path

        job = self.server.jobs.submit("render", work, label=f"{artist} — {track}")
        self._json(202, self._job_payload(job))


class DFIServer(ThreadingHTTPServer):
    """The server, plus the shared things every request needs."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, work_dir=None, verbose=False,
                 config_overrides=None):
        super().__init__(address, handler)
        self.jobs = JobStore()
        self.render_service = RenderService(work_dir=work_dir)
        self.verbose = verbose
        self.config_overrides = config_overrides or {}

    def make_config(self, preview: bool = False) -> gv.RenderConfig:
        """
        Build the render settings.

        `preview` renders a smaller, quicker version for checking the framing;
        everything else about it — the shape, the spin, the blur — is identical,
        so what you see is what you get.
        """
        settings = dict(
            overlay_path=self._asset("overlay-portrait.png"),
            fallback_path=self._asset("fallback.png"),
            font_path=self._asset(os.path.join("fonts", "SquidBoy.otf")),
            motion_blur_samples=10,
            shutter_fraction=0.7,
        )
        settings.update(self.config_overrides)
        if preview:
            settings.update(canvas_w=432, canvas_h=540, circle_diameter=332,
                            hole_diameter=10, clip_length_seconds=3)
        return gv.RenderConfig(**{k: v for k, v in settings.items()
                                  if v is not None})

    @staticmethod
    def _asset(relative: str) -> str | None:
        path = os.path.join(REPO_DIR, "assets", relative)
        return path if os.path.exists(path) else None


def create_server(host: str = "127.0.0.1", port: int = 8765,
                  work_dir: str | None = None, verbose: bool = False,
                  config_overrides: dict | None = None) -> DFIServer:
    """
    Build the server, bound to the loopback address only.

    Binding to 127.0.0.1 rather than 0.0.0.0 means nothing else on the wifi —
    a cafe, a shared office — can reach it. It is only ever your own machine.
    """
    return DFIServer((host, port), _Handler, work_dir=work_dir,
                     verbose=verbose, config_overrides=config_overrides)
