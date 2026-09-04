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
from app.batch import BatchItem, render_batch, zip_results
from app.inspector import inspect_audio
from app.jobs import JobStore
from app.multipart import ParseError, parse_multipart
from app.render_service import RenderRequest, RenderService

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Refuse anything larger than this. The whole body is held in memory while it
# is parsed, and parsing copies it, so the real peak is a few times this number
# — which is why the cap is well above a normal track but nowhere near a
# gigabyte. Real files are 5-50MB.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024      # 100 MB


class _Handler(BaseHTTPRequestHandler):
    server_version = "DFIVideoMaker/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):      # quieter than the default
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send_file(self, path: str, content_type: str, filename: str,
                   disposition: str = "inline") -> None:
        """
        Send a file, honouring a Range request.

        Video needs this. Safari refuses to play media from a server that does
        not support ranges, and no browser can seek without it — the preview
        player would just sit there blank.
        """
        size = os.path.getsize(path)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'{disposition}; filename="{filename}"',
        }
        start, end = 0, size - 1
        status = 200

        raw_range = self.headers.get("Range", "")
        if raw_range.startswith("bytes="):
            spec = raw_range[len("bytes="):].split(",")[0].strip()
            first, _, last = spec.partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:                       # "bytes=-500" = the last 500
                    start = max(0, size - int(last))
                else:
                    raise ValueError
            except ValueError:
                start, end = 0, size - 1
            else:
                if start >= size or start > end:
                    return self._send(
                        416, b"", "text/plain",
                        {"Content-Range": f"bytes */{size}"})
                end = min(end, size - 1)
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"

        with open(path, "rb") as handle:
            handle.seek(start)
            body = handle.read(end - start + 1)
        self._send(status, body, content_type, headers)

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
        # Read and throw away any body the client is still sending. The browser
        # reuses one connection for many requests, so an unread body would be
        # parsed as the *next* request and fail with something baffling. This
        # is what turns "That file is too big" into a generic network error.
        self._drain_body()
        self._json(status, {"error": message})

    def _content_length(self) -> int:
        """The declared body size, or -1 if the header is missing or nonsense."""
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return -1
        return value if value >= 0 else -1

    def _drain_body(self) -> None:
        """Swallow an unread request body so the connection stays usable."""
        if getattr(self, "_body_read", False):
            return
        self._body_read = True
        remaining = self._content_length()
        if remaining <= 0:
            return
        if remaining > MAX_UPLOAD_BYTES:
            # Too much to swallow politely; hang up instead of reading it all.
            self.close_connection = True
            return
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)

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
            cfg = self.server.make_config()
            return self._json(200, {
                "ok": True,
                "ffmpeg": shutil.which("ffmpeg") is not None,
                "ffprobe": shutil.which("ffprobe") is not None,
                # The brand font is licensed, so it is not in the public repo.
                # Without it captions render in a substitute, which is easy to
                # miss until a video is already posted. Say so up front.
                "brand_font": cfg.font_path is not None,
                "overlay": cfg.overlay_path is not None,
                "fallback_art": cfg.fallback_path is not None,
            })
        if path == "/api/jobs":
            return self._json(200, [self._job_payload(j)
                                    for j in self.server.jobs.list()])
        if path.startswith("/api/jobs/"):
            rest = path[len("/api/jobs/"):]
            if rest.endswith("/file"):
                return self._serve_job_file(rest[:-len("/file")])
            if rest.endswith("/zip"):
                return self._serve_batch_zip(rest[:-len("/zip")])
            if "/items/" in rest:
                job_id, _, index = rest.partition("/items/")
                return self._serve_batch_item(job_id, index)
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
        if path == "/api/upload":
            return self._upload()
        if path == "/api/batch":
            return self._start_batch()
        return self._error(404, "Not found.")

    def _read_form(self):
        """Read and parse a multipart body, or send the error and return None."""
        length = self._content_length()
        if length < 0:
            self._error(400, "The request had a malformed length.")
            return None
        if length == 0:
            self._error(400, "The request had no body.")
            return None
        if length > MAX_UPLOAD_BYTES:
            self._error(413, f"That file is too big. The limit is "
                             f"{MAX_UPLOAD_BYTES // (1024 * 1024)}MB.")
            return None
        body = self.rfile.read(length)
        self._body_read = True
        try:
            return parse_multipart(body, self.headers.get("Content-Type", ""))
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
        payload["probe"] = getattr(job, "probe", None) or None
        if job.kind == "batch":
            payload["items"] = self._batch_items_payload(job)
            payload["zip_url"] = (f"/api/jobs/{job.id}/zip"
                                  if job.status == "done" else None)
            return payload
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
        self._send_file(job.result, "video/mp4", os.path.basename(job.result))

    def _upload(self) -> None:
        """Store one file and hand back a token for it."""
        form = self._read_form()
        if form is None:
            return
        _, files = form
        upload = files.get("file") or files.get("artwork") or files.get("audio")
        if upload is None:
            return self._error(400, "No file was sent.")
        path = self.server.render_service.save_upload(upload.filename,
                                                      upload.content)
        return self._json(200, {"upload_token": os.path.basename(path)})

    def _resolve_token(self, token: str) -> str | None:
        """
        Turn an upload token back into a path, or None if it is not one of ours.

        The token comes from the browser, so it is untrusted: it is treated as a
        bare filename inside the uploads folder and the resolved path is checked
        to be genuinely inside it before anything is opened.
        """
        if not token or "/" in token or "\\" in token or token.startswith("."):
            return None
        uploads = os.path.realpath(self.server.render_service.uploads_dir)
        candidate = os.path.realpath(os.path.join(uploads, token))
        if not candidate.startswith(uploads + os.sep):
            return None
        return candidate if os.path.isfile(candidate) else None

    def _read_json(self):
        length = self._content_length()
        if length <= 0 or length > 4 * 1024 * 1024:
            self._error(400, "The request body was missing or too large.")
            return None
        body = self.rfile.read(length)
        self._body_read = True
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._error(400, "The request body was not valid JSON.")
            return None

    def _start_batch(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        raw_items = payload.get("items") or []
        if not raw_items:
            return self._error(400, "There are no tracks in this batch.")

        # Resolve every token BEFORE starting anything. A batch takes minutes;
        # finding out at track seven that its file was never uploaded is much
        # worse than refusing the whole thing up front.
        items = []
        for position, entry in enumerate(raw_items, start=1):
            audio_path = self._resolve_token(entry.get("upload_token", ""))
            if audio_path is None:
                return self._error(
                    400, f"Track {position} refers to a file the app does not "
                         f"have. Choose it again.")
            artwork_path = None
            if entry.get("artwork_token"):
                artwork_path = self._resolve_token(entry["artwork_token"])
                if artwork_path is None:
                    return self._error(
                        400, f"The replacement artwork for track {position} is "
                             f"missing. Choose it again.")
            items.append(BatchItem(
                audio_path=audio_path, artwork_path=artwork_path,
                clip_start=(entry.get("clip_start") or "0:00").strip() or "0:00",
                track=(entry.get("track") or "").strip() or "Untitled",
                artist=(entry.get("artist") or "").strip() or "Unknown artist",
            ))

        cfg = self.server.make_config(preview=bool(payload.get("preview")))
        service = self.server.render_service

        # The job holds the results list from the start and render_batch
        # appends to it, so the page can show each video as it lands instead
        # of everything appearing at the end.
        live_results: list = []

        def work(progress):
            return render_batch(service, items, cfg,
                                progress=lambda f, m="": progress(f, m),
                                results=live_results)

        job = self.server.jobs.submit(
            "batch", work, label=f"Batch of {len(items)}")
        job.results = live_results
        self._json(202, self._job_payload(job))

    def _batch_items_payload(self, job) -> list:
        payload = []
        for index, result in enumerate(getattr(job, "results", []) or []):
            entry = {
                "position": result.position,
                "track": result.item.track,
                "artist": result.item.artist,
                "status": result.status,
                "filename": result.filename,
                "error": result.error,
                "probe": result.probe or None,
            }
            if result.status == "done":
                entry["download_url"] = f"/api/jobs/{job.id}/items/{index}"
            payload.append(entry)
        return payload

    def _serve_batch_item(self, job_id: str, index: str) -> None:
        job = self.server.jobs.get(job_id)
        if job is None:
            return self._error(404, "No such job.")
        results = getattr(job, "results", []) or []
        try:
            result = results[int(index)]
        except (ValueError, IndexError):
            return self._error(404, "No such track in that batch.")
        if result.status != "done" or not result.output_path:
            return self._error(409, "That track has not finished.")
        self._send_file(result.output_path, "video/mp4",
                        os.path.basename(result.output_path))

    def _serve_batch_zip(self, job_id: str) -> None:
        job = self.server.jobs.get(job_id)
        if job is None:
            return self._error(404, "No such job.")
        if job.status not in ("done", "error"):
            return self._error(409, "That batch is still running.")
        results = getattr(job, "results", []) or []
        blob = zip_results(results)
        if not blob or len(results) == 0:
            return self._error(409, "There is nothing finished to download.")
        self._send(200, blob, "application/zip",
                   {"Content-Disposition": 'attachment; filename="dfi-videos.zip"'})

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

        # `probe` is a plain box the work fills in. It cannot refer to the Job
        # itself: submit() starts the thread BEFORE it returns, so a render
        # that finished quickly would reach for a variable that did not exist
        # yet. That was hidden only by renders taking a few seconds.
        probe_box: dict = {}

        def work(progress):
            path = service.render(request, progress=progress)
            # Probe every render: the difference between "it did not crash"
            # and "the file really is what we promised".
            probe_box.update(service.probe(path))
            return path

        job = self.server.jobs.submit("render", work, label=f"{artist} — {track}")
        job.probe = probe_box
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

    def handle_error(self, request, client_address):
        """
        A browser closing a connection mid-request is normal, not an error.

        Left alone, every abandoned request prints a traceback, which buries
        the ones that actually matter.
        """
        import sys
        kind = sys.exc_info()[0]
        if kind is not None and issubclass(kind, (ConnectionError, BrokenPipeError,
                                                  TimeoutError)):
            return
        super().handle_error(request, client_address)

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
