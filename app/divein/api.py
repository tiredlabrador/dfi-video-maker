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
import io
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import traceback
import unicodedata
from collections import OrderedDict
from urllib.parse import parse_qs, quote, unquote, urlparse

import generate_video as gv
from app.divein.audio import analyse, decode_excerpt
from app.divein.compose import Scene, font_path, load_photo, missing_glyphs
from app.divein.config import DEFAULTS, ConfigError, merge_config
from app.divein.render import render_still, render_video

MAX_UPLOAD_BYTES = 3 * 1024 * 1024 * 1024        # 3GB: any mix, any format
CHUNK = 1024 * 1024


class BadRequest(ValueError):
    """Something in the request a person needs to fix. Becomes a 400."""


class _LRU(OrderedDict):
    """
    A small cache that keeps the most recent few things.

    If two requests want the same thing at once, the second waits for the
    first rather than building it again — so a burst of preview requests
    can't pile up duplicate copies in memory.
    """
    def __init__(self, size):
        super().__init__()
        self.size = size
        self.lock = threading.Lock()
        self._building: dict = {}

    def get_or(self, key, make):
        while True:
            with self.lock:
                if key in self:
                    self.move_to_end(key)
                    return self[key]
                waiting = self._building.get(key)
                if waiting is None:
                    done = threading.Event()
                    self._building[key] = done
                    break
            waiting.wait()
        try:
            value = make()
            with self.lock:
                self[key] = value
                while len(self) > self.size:
                    self.popitem(last=False)
            return value
        finally:
            with self.lock:
                self._building.pop(key, None)
            done.set()


def frame_at(time: float, fps: int, seconds: float) -> int:
    """The frame shown at `time` seconds into the clip (nearest, not floored)."""
    last = max(0, int(round(seconds * fps)) - 1)
    return int(min(last, max(0, round(float(time) * fps))))


def _scrub_paths(message: str) -> str:
    """Keep private file paths out of anything shown on the page."""
    return re.sub(r"(/[^\s:'\"]+)+", "…", message)


def _ascii_name(name: str) -> str:
    """A plain-ASCII version of a filename, for the one place that needs it."""
    folded = unicodedata.normalize("NFKD", name.replace("’", "'").replace("–", "-"))
    return folded.encode("ascii", "ignore").decode() or "dive-in"


class DiveInService:
    def __init__(self, work_dir: str, jobs):
        self.root = os.path.join(work_dir, "divein")
        self.uploads = os.path.join(self.root, "uploads")
        self.outputs = os.path.join(self.root, "output")
        os.makedirs(self.uploads, exist_ok=True)
        os.makedirs(self.outputs, exist_ok=True)
        self.jobs = jobs
        self._photos = _LRU(4)
        self._scenes = _LRU(2)
        self._layer_cache = _LRU(8)
        self._audio = _LRU(6)
        self._files: dict[str, list] = {}
        self._durations: dict[str, float] = {}
        self._still_lock = threading.Lock()
        self._latest_still: OrderedDict = OrderedDict()   # page -> newest request

    # ── routing ──────────────────────────────────────────────────────────
    def handle(self, h, method: str, path: str) -> None:
        """Answer one /api/divein/ request on handler `h`. Always answers."""
        try:
            if method == "GET" and path == "/api/divein/defaults":
                return h._json(200, self.defaults())
            if method == "GET" and path.startswith("/api/divein/jobs/"):
                return self._job_route(h, path[len("/api/divein/jobs/"):])
            if method == "POST" and path == "/api/divein/upload":
                return h._json(200, self.upload(h))
            if method == "POST" and path == "/api/divein/still":
                result = self._still(self._read_json(h))
                if result is None:                      # a newer one superseded it
                    return h._send(204, b"", "image/jpeg")
                body, warning = result
                extra = {"X-Divein-Warning": quote(warning)} if warning else None
                return h._send(200, body, "image/jpeg", extra)
            if method == "POST" and path == "/api/divein/layers":
                return h._send(200, self._layers(self._read_json(h)), "image/png")
            if method == "POST" and path in ("/api/divein/preview", "/api/divein/export"):
                payload = self._read_json(h)
                job = self._start(payload, preview=path.endswith("preview"))
                return h._json(202, self._job_payload(job))
            return h._error(404, "Not found.")
        except (BadRequest, ConfigError) as exc:
            return h._error(400, str(exc))
        except Exception as exc:                       # noqa: BLE001
            # Never leave the page with a dropped connection: say something.
            traceback.print_exc()
            return h._error(500, "Something went wrong drawing that: "
                                 + _scrub_paths(str(exc) or exc.__class__.__name__))

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

        # The page percent-encodes the name: browsers refuse to send anything
        # beyond Latin-1 in a header, and names like "Dom’s mix" are common.
        name = unquote(h.headers.get("X-Filename", "")) or "upload"
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
            self._durations[token] = info["duration"]
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

    # Settings that belong to controls on the page. If the Tuning box set them
    # too, one would silently win — so say where to change them instead.
    PAGE_CONTROLS = {("glyph", "mode"): "the Style buttons",
                     ("twitch", "enabled"): "the Kick twitch checkbox",
                     ("audio", "clip_seconds"): "the Clip length box",
                     ("audio", "drive"): "the Motion buttons"}

    def _config(self, d: dict) -> dict:
        overrides = d.get("overrides") or {}
        if not isinstance(overrides, dict):
            raise BadRequest("The Tuning box must contain a JSON object, like {}.")
        for (group, key), control in self.PAGE_CONTROLS.items():
            if isinstance(overrides.get(group), dict) and key in overrides[group]:
                raise BadRequest(f"{group}.{key} is set with {control} on the page, "
                                 f"not in Tuning. (Style, twitch and clip length live there.)")
        if "accent" in overrides:
            raise BadRequest("accent is set with the Colour buttons on the page, not in Tuning.")
        cfg = merge_config(overrides)
        colour = d.get("colour", cfg["accent"])
        if colour not in cfg["accents"]:
            raise BadRequest(f"Colour must be one of: {', '.join(cfg['accents'])}.")
        cfg["accent"] = colour
        mode = d.get("glyph", cfg["glyph"]["mode"])
        if mode not in ("rings", "bars", "line", "none"):
            raise BadRequest("Glyph style must be rings, bars, line or none.")
        cfg["glyph"]["mode"] = mode
        cfg["twitch"]["enabled"] = bool(d.get("twitch", cfg["twitch"]["enabled"]))
        motion = d.get("motion", cfg["audio"]["drive"])
        if motion not in ("kicks", "level"):
            raise BadRequest("Motion must follow kicks or level.")
        cfg["audio"]["drive"] = motion
        if d.get("clip_seconds") is not None:
            seconds = self._number(d["clip_seconds"], "Clip length")
            if not 0.5 <= seconds <= 120:
                raise BadRequest("Clip length must be between 0.5 and 120 seconds.")
            cfg["audio"]["clip_seconds"] = seconds
        return cfg

    @staticmethod
    def _number(value, what: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise BadRequest(f"{what} must be a number.")
        if not math.isfinite(number):
            raise BadRequest(f"{what} must be a real number.")
        return number

    def _crop(self, d: dict) -> dict:
        crop = d.get("crop") or {}
        if not isinstance(crop, dict):
            raise BadRequest("The crop wasn't understood. Try Reset under the photo.")
        out = {"zoom": self._number(crop.get("zoom", 1.0), "Zoom"),
               "cx": self._number(crop.get("cx", 0.5), "The crop position"),
               "cy": self._number(crop.get("cy", 0.5), "The crop position")}
        out["zoom"] = min(max(out["zoom"], 1.0), 10.0)
        out["cx"] = min(max(out["cx"], 0.0), 1.0)
        out["cy"] = min(max(out["cy"], 0.0), 1.0)
        return out

    def _scene(self, d: dict, cfg: dict, fmt: str) -> Scene:
        photo_token = d.get("photo")
        crop = self._crop(d)
        artist = str(d.get("artist", ""))
        episode = str(d.get("episode", "")).strip() or "00.00"
        key = json.dumps([photo_token, crop, artist, episode, fmt, cfg], sort_keys=True)
        photo = self._photo(photo_token)
        return self._scenes.get_or(key, lambda: Scene(cfg, photo, artist, episode,
                                                      crop=crop, fmt=fmt))

    def _starts(self, d: dict) -> list[float]:
        raw = d.get("starts", "")
        parts = raw if isinstance(raw, list) else re.split(r"[,\n;]+", str(raw))
        parts = [str(p).strip() for p in parts if str(p).strip()]
        if not parts:
            raise BadRequest("Add at least one clip start time, like 45:10.")
        length = self._audio_length(d.get("audio")) if d.get("audio") else None
        out = []
        for p in parts:
            try:
                # A leading minus is refused outright: "-0:05" would otherwise
                # read as 5 seconds, because "-0" times 60 is still 0.
                seconds = float("nan") if p.startswith("-") else gv.parse_timecode(p)
            except gv.RenderError:
                seconds = float("nan")
            if not math.isfinite(seconds) or seconds < 0:
                raise BadRequest(f"Couldn't read the start time {p!r}. Use mm:ss or h:mm:ss.")
            if length is not None and seconds >= length:
                raise BadRequest(f"The start time {p} is after the end of the mix "
                                 f"(it's {self._clock(length)} long).")
            out.append(seconds)
        return out

    @staticmethod
    def _clock(seconds: float) -> str:
        s = int(seconds)
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 \
            else f"{s // 60}:{s % 60:02d}"

    def _audio_length(self, token) -> float:
        path = self._resolve(token, "audio file")
        if token not in self._durations:
            self._durations[token] = self._duration(path)
        return self._durations[token]

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
    def _still(self, d: dict):
        """
        The live preview picture: (jpeg bytes, warning) — or None if a newer
        request arrived first. While dragging, only the newest picture matters,
        so older requests are dropped rather than drawn, and only one is drawn
        at a time.
        """
        seq = d.get("seq")
        seq = int(self._number(seq, "seq")) if seq is not None else None
        # Counted per open page: a reloaded page starts again from 1, and must
        # not be mistaken for an old request from the page it replaced.
        client = str(d.get("client", ""))[:64]
        latest = self._latest_still
        if seq is not None:
            with self._still_lock:
                if seq < latest.get(client, -1):
                    return None
                latest[client] = seq
                latest.move_to_end(client)
                while len(latest) > 50:
                    latest.popitem(last=False)
        with self._still_lock:
            if seq is not None and seq < latest.get(client, -1):
                return None
            return self._draw_still(d)

    def _draw_still(self, d: dict):
        cfg = self._config(d)
        fmt = "square" if d.get("format") == "square" else "portrait"
        scene = self._scene(d, cfg, fmt)
        analysis, frame = None, 0
        if d.get("audio") and d.get("time") is not None and fmt == "portrait":
            time = self._number(d["time"], "The preview time")
            start = self._starts(d)[0]
            seconds = cfg["audio"]["clip_seconds"]
            try:
                _, analysis = self._analysis(d, cfg, start, seconds)
            except ValueError as exc:
                raise BadRequest(str(exc))
            frame = frame_at(time, cfg["canvas"]["fps"], seconds)
        img = scene.frame(frame, analysis, debug=bool(d.get("debug")))
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=88)
        lacking = missing_glyphs(str(d.get("artist", "")) + str(d.get("episode", "")), cfg)
        warning = ("Squid Boy has no letter for: " + " ".join(lacking)
                   + " — it won't appear.") if lacking else ""
        return buffer.getvalue(), warning

    def _layers(self, d: dict) -> bytes:
        """Everything above the photo, for the page's quick drag draft."""
        cfg = self._config(d)
        fmt = "square" if d.get("format") == "square" else "portrait"
        artist = str(d.get("artist", ""))
        episode = str(d.get("episode", "")).strip() or "00.00"
        key = json.dumps(["layers", artist, episode, fmt, cfg], sort_keys=True)
        def make():
            buffer = io.BytesIO()
            Scene(cfg, None, artist, episode, fmt=fmt).layers().save(buffer, "PNG")
            return buffer.getvalue()
        return self._layer_cache.get_or(key, make)

    # ── preview and export jobs ──────────────────────────────────────────
    def _filename(self, d: dict, suffix: str) -> str:
        episode = str(d.get("episode", "")).strip() or "00.00"
        artist = " ".join(str(d.get("artist", "")).split()) or "Untitled"
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
                try:
                    samples, analysis = self._analysis(d, cfg, start, seconds)
                except ValueError as exc:
                    raise ValueError(f"Clip {i + 1} ({self._clock(start)}): {exc}")
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
                         for i, p in enumerate(files)] if job.status in ("done", "error") else []
        if data.get("error"):
            data["error"] = _scrub_paths(data["error"])
        return data

    def _job_route(self, h, rest: str):
        job_id, _, tail = rest.partition("/files/")
        job = self.jobs.get(job_id)
        if job is None or job.id not in self._files:
            return h._error(404, "No such job.")
        if not tail:
            return h._json(200, self._job_payload(job))
        if job.status not in ("done", "error"):
            return h._error(409, "That isn't finished yet.")
        try:
            path = self._files[job.id][int(tail)]
        except (ValueError, IndexError):
            return h._error(404, "No such file.")
        kind = "image/jpeg" if path.endswith(".jpg") else "video/mp4"
        # The download header only allows plain ASCII; the page's own
        # download link carries the real name, curly quotes and all.
        return h._send_file(path, kind, _ascii_name(os.path.basename(path)))
