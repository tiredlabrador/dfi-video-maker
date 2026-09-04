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
import zipfile
from dataclasses import dataclass, field

import generate_video as gv
from app.render_service import RenderRequest, RenderService


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
                 results: list | None = None) -> list[BatchResult]:
    """
    Render every item, in order, and report on each.

    Never raises for a bad track: a failure is recorded on that item's result
    and the batch carries on. Returns one BatchResult per item, in the order
    they were given.

    Pass `results` to have each track appended to a list you already hold, so
    finished videos can be shown while the rest are still rendering rather than
    all appearing at the end.
    """
    total = len(items)
    if results is None:
        results = []
    if not total:
        return results

    for index, item in enumerate(items):
        position = index + 1
        result = BatchResult(item=item, position=position)
        results.append(result)

        # Each track owns one slice of the overall bar, so progress only ever
        # moves forwards even though each render counts 0 to 1 internally.
        slice_start = index / total
        slice_size = 1.0 / total
        label = f"{position} of {total}: {item.track}"

        def report(fraction: float, message: str = "", _s=slice_start,
                   _w=slice_size, _l=label) -> None:
            if progress is not None:
                progress(_s + _w * fraction, f"{_l} — {message}" if message else _l)

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
