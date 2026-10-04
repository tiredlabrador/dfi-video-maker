"""
The Dive In test area's part of the local server.

Everything lives under /api/divein/. The existing app hands those requests
over and otherwise doesn't know this exists.

Uploads here stream straight to disk rather than being held in memory, because
a whole mix can be several hundred megabytes — far past the limit the rest of
the app sets for single tracks.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
from collections import OrderedDict
from urllib.parse import parse_qs, urlparse

import generate_video as gv
from app.divein.audio import analyse, decode_excerpt
from app.divein.compose import Scene, font_path, load_photo
from app.divein.config import DEFAULTS, ConfigError, merge_config
from app.divein.render import render_still, render_video

MAX_UPLOAD_BYTES = 3 * 1024 * 1024 * 1024        # 3GB: any mix, any format
CHUNK = 1024 * 1024


class BadRequest(ValueError):
    """Something in the request a person needs to fix. Becomes a 400."""


class _LRU(OrderedDict):
    def __init__(self, size):
        super().__init__()
        self.size = size
        self.lock = threading.Lock()

    def get_or(self, key, make):
        with self.lock:
            if key in self:
                self.move_to_end(key)
                return self[key]
        value = make()
        with self.lock:
            self[key] = value
            while len(self) > self.size:
                self.popitem(last=False)
        return value


class DiveInService:
    def __init__(self, work_dir: str, jobs):
        self.root = os.path.join(work_dir, "divein")
        self.uploads = os.path.join(self.root, "uploads")
        self.outputs = os.path.join(self.root, "output")
        os.makedirs(self.uploads, exist_ok=True)
        os.makedirs(self.outputs, exist_ok=True)
        self.jobs = jobs
        self._photos = _LRU(4)
        self._scenes = _LRU(3)
        self._audio = _LRU(6)
        self._files: dict[str, list] = {}

    # ── routing ──────────────────────────────────────────────────────────
    def handle(self, h, method: str, path: str) -> None:
        """Answer one /api/divein/ request on handler `h`."""
        try:
            if method == "GET" and path == "/api/divein/defaults":
                return h._json(200, self.defaults())
            if method == "GET" and path.startswith("/api/divein/jobs/"):
                return self._job_route(h, path[len("/api/divein/jobs/"):])
            if method == "POST" and path == "/api/divein/upload":
                return h._json(200, self.upload(h))
            if method == "POST" and path == "/api/divein/still":
                body = self._still(self._read_json(h))
                return h._send(200, body, "image/jpeg")
            if method == "POST" and path in ("/api/divein/preview", "/api/divein/export"):
                payload = self._read_json(h)
                job = self._start(payload, preview=path.endswith("preview"))
                return h._json(202, self._job_payload(job))
            return h._error(404, "Not found.")
        except (BadRequest, ConfigError) as exc:
            return h._error(400, str(exc))

    def defaults(self) -> dict:
        fonts = {name: font_path(name) is not None and
                 os.path.basename(font_path(name)) == name
                 for name in {DEFAULTS["tape"]["font"], DEFAULTS["name"]["font"]}}
        return {"defaults": DEFAULTS, "fonts": fonts}

    # ── uploads ──────────────────────────────────────────────────────────
    def upload(self, h) -> dict:
        kind = parse_qs(urlparse(h.path).query).get("kind", [""])[0]
        if kind not in ("photo", "audio"):
            h._drain_body()
            raise BadRequest("Upload kind must be photo or audio.")
        length = h._content_length()
        if length <= 0:
            raise BadRequest("The upload was empty.")
        if length > MAX_UPLOAD_BYTES:
            raise BadRequest("That file is over 3GB.")

        name = h.headers.get("X-Filename", "") or "upload"
        _, ext = os.path.splitext(os.path.basename(name))
        ext = re.sub(r"[^A-Za-z0-9.]", "", ext)[:12]
        digest = hashlib.sha256()
        handle, tmp = tempfile.mkstemp(dir=self.uploads, suffix=".part")
        try:
            with os.fdopen(handle, "wb") as out:
                remaining = length
                while remaining > 0:
                    chunk = h.rfile.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            h._body_read = True
            if remaining:
                raise BadRequest("The upload was cut short.")
            token = digest.hexdigest()[:32] + ext
            final = os.path.join(self.uploads, token)
            os.replace(tmp, final)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

        info = {"token": token, "kind": kind, "name": os.path.basename(name)}
        if kind == "photo":
            try:
                photo = self._photo(token)
            except Exception:                      # noqa: BLE001
                raise BadRequest("That photo couldn't be opened. JPG or PNG works; "
                                 "iPhone HEIC photos need exporting as JPG first.")
            info.update(width=photo.width, height=photo.height)
        else:
            info["duration"] = self._duration(final)
        return info

    def _duration(self, path: str) -> float:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
        try:
            value = float(result.stdout.strip())
        except ValueError:
            value = 0.0
        if result.returncode != 0 or value <= 0:
            raise BadRequest("That audio file couldn't be read. MP3, WAV, FLAC, "
                             "M4A and AIFF all work.")
        return value

    def _resolve(self, token, what: str) -> str:
        if not isinstance(token, str) or not token or "/" in token or "\\" in token \
                or token.startswith("."):
            raise BadRequest(f"The {what} needs choosing again.")
        root = os.path.realpath(self.uploads)
        path = os.path.realpath(os.path.join(root, token))
        if not path.startswith(root + os.sep) or not os.path.isfile(path):
            raise BadRequest(f"The {what} needs choosing again.")
        return path

    def _photo(self, token):
        return self._photos.get_or(token, lambda: load_photo(self._resolve(token, "photo")))

    # ── building a design from a request ─────────────────────────────────
    def _read_json(self, h) -> dict:
        length = h._content_length()
        if length <= 0 or length > 4 * 1024 * 1024:
            raise BadRequest("The request was empty or too large.")
        body = h.rfile.read(length)
        h._body_read = True
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BadRequest("The request wasn't valid JSON.")
        if not isinstance(data, dict):
            raise BadRequest("The request wasn't a JSON object.")
        return data

    def _config(self, d: dict) -> dict:
        cfg = merge_config(d.get("overrides") or {})
        mode = d.get("glyph", cfg["glyph"]["mode"])
        if mode not in ("rings", "bars", "line", "none"):
            raise BadRequest("Glyph style must be rings, bars, line or none.")
        cfg["glyph"]["mode"] = mode
        cfg["twitch"]["enabled"] = bool(d.get("twitch", cfg["twitch"]["enabled"]))
        if d.get("clip_seconds") is not None:
            try:
                seconds = float(d["clip_seconds"])
            except (TypeError, ValueError):
                raise BadRequest("Clip length must be a number of seconds.")
            if not 0.5 <= seconds <= 120:
                raise BadRequest("Clip length must be between 0.5 and 120 seconds.")
            cfg["audio"]["clip_seconds"] = seconds
        return cfg

    def _scene(self, d: dict, cfg: dict, fmt: str) -> Scene:
        photo_token = d.get("photo")
        crop = d.get("crop") or {}
        artist = str(d.get("artist", ""))
        episode = str(d.get("episode", "")).strip() or "00.00"
        key = json.dumps([photo_token, crop, artist, episode, fmt, cfg], sort_keys=True)
        photo = self._photo(photo_token)
        return self._scenes.get_or(key, lambda: Scene(cfg, photo, artist, episode,
                                                      crop=crop, fmt=fmt))

    @staticmethod
    def _starts(d: dict) -> list[float]:
        raw = d.get("starts", "")
        parts = raw if isinstance(raw, list) else re.split(r"[,\n;]+", str(raw))
        parts = [str(p).strip() for p in parts if str(p).strip()]
        if not parts:
            raise BadRequest("Add at least one clip start time, like 45:10.")
        out = []
        for p in parts:
            try:
                out.append(gv.parse_timecode(p))
            except gv.RenderError:
                raise BadRequest(f"Couldn't read the start time {p!r}. Use mm:ss "
                                 f"or h:mm:ss.")
        return out

    def _analysis(self, d: dict, cfg: dict, start: float, seconds: float):
        audio = self._resolve(d.get("audio"), "audio file")
        episode = str(d.get("episode", "")).strip() or "00.00"
        sr = cfg["audio"]["sample_rate"]
        key = json.dumps([audio, start, seconds, episode, cfg["audio"], cfg["twitch"],
                          cfg["glyph"]["bars"]["count"], cfg["canvas"]["fps"]],
                         sort_keys=True)
        def make():
            samples = decode_excerpt(audio, start, seconds, sr)
            return samples, analyse(samples, sr, cfg["canvas"]["fps"], cfg, episode)
        return self._audio.get_or(key, make)

    # ── the live still ───────────────────────────────────────────────────
    def _still(self, d: dict) -> bytes:
        cfg = self._config(d)
        fmt = "square" if d.get("format") == "square" else "portrait"
        scene = self._scene(d, cfg, fmt)
        analysis, frame = None, 0
        if d.get("audio") and d.get("time") is not None and fmt == "portrait":
            start = self._starts(d)[0]
            seconds = cfg["audio"]["clip_seconds"]
            try:
                _, analysis = self._analysis(d, cfg, start, seconds)
            except ValueError as exc:
                raise BadRequest(str(exc))
            frame = int(max(0.0, min(float(d["time"]), seconds)) * cfg["canvas"]["fps"])
        img = scene.frame(frame, analysis, debug=bool(d.get("debug")))
        import io
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=88)
        return buffer.getvalue()

    # ── preview and export jobs ──────────────────────────────────────────
    def _filename(self, d: dict, suffix: str) -> str:
        episode = str(d.get("episode", "")).strip() or "00.00"
        artist = " ".join(str(d.get("artist", "")).split()).title() or "Untitled"
        return gv.sanitise_filename(f"Dive In {episode} - {artist}{suffix}")

    def _start(self, d: dict, preview: bool):
        if not d.get("audio"):
            raise BadRequest("Choose the mix's audio file first.")
        cfg = self._config(d)
        starts = self._starts(d)
        self._resolve(d.get("audio"), "audio file")
        self._photo(d.get("photo"))                      # fail now, not mid-job
        debug = bool(d.get("debug"))
        files: list = []

        def work(progress):
            folder = tempfile.mkdtemp(dir=self.outputs)
            if preview:
                seconds = min(cfg["export"]["preview_seconds"], cfg["audio"]["clip_seconds"])
                samples, analysis = self._analysis(d, cfg, starts[0], cfg["audio"]["clip_seconds"])
                scene = self._scene(d, cfg, "portrait")
                out = os.path.join(folder, self._filename(d, " - preview.mp4"))
                render_video(scene, analysis, samples, cfg["audio"]["sample_rate"], out,
                             cfg, seconds, width=cfg["export"]["preview_width"],
                             debug=debug, progress=progress)
                files.append(out)
                return files

            seconds = cfg["audio"]["clip_seconds"]
            scene = self._scene(d, cfg, "portrait")
            steps = len(starts) + 1
            for i, start in enumerate(starts):
                progress(i / steps, f"Analysing clip {i + 1} of {len(starts)}")
                samples, analysis = self._analysis(d, cfg, start, seconds)
                suffix = f" - {i + 1}.mp4" if len(starts) > 1 else ".mp4"
                out = os.path.join(folder, self._filename(d, suffix))
                render_video(scene, analysis, samples, cfg["audio"]["sample_rate"], out,
                             cfg, seconds, debug=debug,
                             progress=lambda f, m="", i=i: progress((i + f) / steps,
                                 f"Clip {i + 1} of {len(starts)}: {m}"))
                files.append(out)
            progress((steps - 1) / steps, "Making the square JPG")
            square = self._scene(d, cfg, "square")
            jpg = os.path.join(folder, self._filename(d, " - square.jpg"))
            render_still(square, jpg, cfg["square"]["jpg_quality"])
            files.append(jpg)
            progress(1.0, "Finished")
            return files

        label = "Dive In preview" if preview else "Dive In export"
        job = self.jobs.submit("divein", work, label=label)
        self._files[job.id] = files
        return job

    def _job_payload(self, job) -> dict:
        data = job.as_dict()
        files = self._files.get(job.id, [])
        data["files"] = [{"name": os.path.basename(p),
                          "url": f"/api/divein/jobs/{job.id}/files/{i}",
                          "size": os.path.getsize(p) if os.path.exists(p) else 0}
                         for i, p in enumerate(files)] if job.status == "done" else []
        return data

    def _job_route(self, h, rest: str):
        job_id, _, tail = rest.partition("/files/")
        job = self.jobs.get(job_id)
        if job is None or job.id not in self._files:
            return h._error(404, "No such job.")
        if not tail:
            return h._json(200, self._job_payload(job))
        if job.status != "done":
            return h._error(409, "That isn't finished yet.")
        try:
            path = self._files[job.id][int(tail)]
        except (ValueError, IndexError):
            return h._error(404, "No such file.")
        kind = "image/jpeg" if path.endswith(".jpg") else "video/mp4"
        return h._send_file(path, kind, os.path.basename(path))
