"""
Rendering a whole batch.

A batch is the unit the tool actually gets used in: eight or ten tracks,
rendered in one go, numbered so they sort into posting order.

Two rules drive the design here, and both come from things that hurt before:

* **The numbering is positional, not sequential.** Track 3 is `03` because it
  is third in the list, not because it is the third one that happened to
  succeed. If track 2 fails and everything after it shuffles up a number, the
  posting order changes silently and the wrong video goes out on the wrong day.

* **One bad track must not take the batch with it.** A batch takes a minute or
  more. Losing all of it because track four had no artwork is the kind of thing
  that makes people go back to doing it by hand.
"""
from __future__ import annotations

import io
import os
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import generate_video as gv
from app.render_service import RenderRequest, RenderService

# Tracks rendered side by side. Each render is mostly Pillow and ffmpeg, which
# work outside Python's one-at-a-time lock: three at once is about 1.6x quicker.
WORKERS = max(1, min(3, (os.cpu_count() or 2) - 1))


@dataclass
class BatchItem:
    """One track waiting to be rendered."""
    audio_path: str
    artwork_path: str | None
    clip_start: str
    track: str
    artist: str


@dataclass
class BatchResult:
    """What happened to one track."""
    item: BatchItem
    position: int
    status: str = "pending"            # pending | done | error
    started: bool = False              # being rendered right now (or was)
    output_path: str | None = None
    error: str = ""
    probe: dict = field(default_factory=dict)

    @property
    def filename(self) -> str:
        if self.output_path:
            return os.path.basename(self.output_path)
        # Even a failure gets its name, so the UI can show which slot it was.
        return gv.output_filename(self.item.artist, self.item.track,
                                  self.position, self.position)


def render_batch(service: RenderService, items: list[BatchItem],
                 cfg: gv.RenderConfig, progress=None,
                 results: list | None = None, workers: int | None = None) -> list[BatchResult]:
    """
    Render every item, several at a time, and report on each.

    Never raises for a bad track: a failure is recorded on that item's result
    and the batch carries on. Returns one BatchResult per item, in the order
    they were given, numbered by that order whichever finishes first.

    Pass `results` to have every track's result added to a list you already
    hold (all at the start, filled in as they finish), so finished videos can
    be shown while the rest are still rendering.
    """
    total = len(items)
    if results is None:
        results = []
    if not total:
        return results

    slots = [BatchResult(item=item, position=index + 1) for index, item in enumerate(items)]
    results.extend(slots)
    # The overall bar is the average of every track's own 0 to 1, reported
    # under a lock so it only ever moves forwards.
    done_so_far = [0.0] * total
    lock = threading.Lock()
    shown = [0.0]

    def reporter(index: int, label: str):
        def report(fraction: float, message: str = "") -> None:
            if progress is None:
                return
            with lock:
                done_so_far[index] = max(done_so_far[index], min(1.0, fraction))
                shown[0] = max(shown[0], sum(done_so_far) / total)
                progress(shown[0], f"{label} — {message}" if message else label)
        return report

    def render_one(index: int) -> None:
        result = slots[index]
        item, position = result.item, result.position
        result.started = True
        report = reporter(index, f"{position} of {total}: {item.track}")
        report(0.0, "Starting")
        try:
            request = RenderRequest(
                audio_path=item.audio_path, artwork_path=item.artwork_path,
                clip_start=item.clip_start, track=item.track,
                artist=item.artist, cfg=cfg,
                position=position, total=total,
            )
            result.output_path = service.render(request, progress=report)
            result.probe = service.probe(result.output_path)
            result.status = "done"
        except Exception as exc:                    # noqa: BLE001 - recorded, not swallowed
            result.status = "error"
            result.error = str(exc) or exc.__class__.__name__

        report(1.0, "Done" if result.status == "done" else "Failed")

    with ThreadPoolExecutor(max(1, workers or WORKERS)) as pool:
        list(pool.map(render_one, range(total)))

    if progress is not None:
        done = sum(1 for r in results if r.status == "done")
        failed = total - done
        summary = f"Finished — {done} of {total} rendered"
        if failed:
            summary += f", {failed} could not be made"
        progress(1.0, summary)
    return results


def zip_results(results: list[BatchResult]) -> bytes:
    """
    Package every successful render into one zip.

    Failures are left out rather than included as empty files — a zip of nine
    videos and one 0-byte placeholder is worse than a zip of nine videos plus a
    clear message about the tenth.
    """
    buffer = io.BytesIO()
    # Videos are already compressed; storing them is faster and the same size.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for result in results:
            if result.status == "done" and result.output_path \
                    and os.path.isfile(result.output_path):
                archive.write(result.output_path,
                              arcname=os.path.basename(result.output_path))
    return buffer.getvalue()
