"""
Background jobs.

A render takes several seconds. If it ran inside the web request the browser
would sit there with a spinner and no idea what was happening, and any hiccup
would look like a crash. So work runs on a background thread and the page asks
"how's it going?" every so often.

Deliberately small: no queue library, no broker, no dependencies. One thread per
job, a lock around the shared state, and that's the whole design.
"""
from __future__ import annotations

import threading
import traceback
import uuid
from typing import Any, Callable


class Job:
    """One unit of background work and everything the UI needs to know about it."""

    def __init__(self, kind: str, label: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.label = label
        self.status = "queued"        # queued -> running -> done | error
        self.progress = 0.0           # 0.0 to 1.0
        self.message = ""             # plain-English "what's happening now"
        self.result: Any = None       # whatever the work returned
        self.error: str | None = None      # a message a human can act on
        self.traceback: str = ""           # the technical detail, for the log
        self._lock = threading.Lock()
        self._finished = threading.Event()

    # -- reporting, called from the worker thread -------------------------
    def _set_progress(self, fraction: float, message: str = "") -> None:
        with self._lock:
            self.progress = max(0.0, min(1.0, float(fraction)))
            if message:
                self.message = message

    def wait(self, timeout: float | None = None) -> "Job":
        """Block until the job finishes. Returns the job, so calls can chain."""
        self._finished.wait(timeout)
        return self

    def as_dict(self) -> dict:
        """
        The view of this job that is safe to send to the browser.

        Note what is *absent*: `result` is usually a local filesystem path, and
        the page has no business knowing where on the disk a file lives. It asks
        for the file by job id instead.
        """
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "label": self.label,
                "status": self.status,
                "progress": round(self.progress, 4),
                "message": self.message,
                "error": self.error,
            }


class JobStore:
    """Holds every job this run of the app has started."""

    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def submit(self, kind: str, work: Callable[[Callable], Any],
               label: str = "") -> Job:
        """
        Start `work` on a background thread and return its Job straight away.

        `work` is called with one argument: a `progress(fraction, message)`
        function it should call as it goes, so the page can show something
        honest rather than an indeterminate spinner.
        """
        job = Job(kind, label)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)

        def runner():
            with job._lock:
                job.status = "running"
            try:
                result = work(job._set_progress)
            except Exception as exc:                # noqa: BLE001 - reported, not swallowed
                with job._lock:
                    # Fill in the detail BEFORE flipping the status. Anything
                    # polling this job watches `status`, so if status changed
                    # first there would be a moment where the job reads as
                    # failed but carries no reason why.
                    job.error = str(exc) or exc.__class__.__name__
                    job.traceback = traceback.format_exc()
                    job.status = "error"
            else:
                with job._lock:
                    job.result = result
                    job.progress = 1.0
                    job.status = "done"          # last, for the same reason
            finally:
                job._finished.set()

        threading.Thread(target=runner, name=f"job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        """Every job, newest first."""
        with self._lock:
            return [self._jobs[i] for i in reversed(self._order)]
