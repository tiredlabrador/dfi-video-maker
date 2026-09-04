"""
Tests for the background job store.

Rendering takes several seconds, so it cannot happen inside the web request or
the browser would just hang. Jobs run on a background thread and the UI polls
for progress. This is that machinery.
"""
import threading
import time

import pytest

from app.jobs import JobStore


def wait_for(predicate, timeout=5.0):
    """Poll until predicate() is true, or fail the test."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_new_job_starts_queued_and_gets_an_id():
    store = JobStore()
    gate = threading.Event()
    job = store.submit("render", lambda progress: gate.wait(2))
    assert job.id
    assert job.status in ("queued", "running")
    gate.set()
    assert wait_for(lambda: job.status == "done")


def test_a_finished_job_reports_done_and_its_result():
    store = JobStore()
    job = store.submit("render", lambda progress: "/tmp/out.mp4")
    assert wait_for(lambda: job.status == "done")
    assert job.result == "/tmp/out.mp4"
    assert job.error is None
    assert job.progress == 1.0


def test_a_failing_job_reports_the_message_not_a_traceback():
    store = JobStore()

    def boom(progress):
        raise ValueError("No artwork found for this track.")

    job = store.submit("render", boom)
    assert wait_for(lambda: job.status == "error")
    assert job.error == "No artwork found for this track."
    assert "Traceback" not in job.error
    assert job.result is None


def test_the_traceback_is_still_kept_for_diagnosis():
    store = JobStore()

    def boom(progress):
        raise ValueError("nope")

    job = store.submit("render", boom)
    assert wait_for(lambda: job.status == "error")
    assert "Traceback" in job.traceback
    assert "ValueError" in job.traceback


def test_a_job_can_report_progress_and_a_message_while_it_runs():
    store = JobStore()
    seen = []
    release = threading.Event()

    def work(progress):
        progress(0.25, "Loading artwork")
        seen.append("quarter")
        release.wait(2)
        progress(0.75, "Encoding")
        return "done"

    job = store.submit("render", work)
    assert wait_for(lambda: seen == ["quarter"])
    assert job.progress == 0.25
    assert job.message == "Loading artwork"
    release.set()
    assert wait_for(lambda: job.status == "done")


def test_progress_is_clamped_to_the_zero_to_one_range():
    store = JobStore()
    store.submit("render", lambda progress: progress(5.0, "over")).wait(5)
    job = store.submit("render", lambda progress: (progress(-2.0, "under"), "x")[1])
    assert wait_for(lambda: job.status == "done")
    assert 0.0 <= job.progress <= 1.0


def test_jobs_can_be_looked_up_by_id():
    store = JobStore()
    job = store.submit("render", lambda progress: "x")
    assert store.get(job.id) is job
    assert store.get("no-such-job") is None


def test_jobs_are_listed_newest_first():
    store = JobStore()
    first = store.submit("render", lambda progress: "a")
    first.wait(5)
    second = store.submit("render", lambda progress: "b")
    second.wait(5)
    assert [j.id for j in store.list()][:2] == [second.id, first.id]


def test_a_job_serialises_to_json_safe_values_for_the_ui():
    store = JobStore()
    job = store.submit("render", lambda progress: "/tmp/x.mp4", label="Fantasy")
    assert wait_for(lambda: job.status == "done")
    data = job.as_dict()
    assert data["id"] == job.id
    assert data["status"] == "done"
    assert data["label"] == "Fantasy"
    assert data["progress"] == 1.0
    # The UI must never be handed a local filesystem path it cannot use.
    assert "/tmp/x.mp4" not in str(data)


def test_wait_returns_the_job_so_it_can_be_chained():
    store = JobStore()
    job = store.submit("render", lambda progress: "x")
    assert job.wait(5) is job
    assert job.status == "done"


def test_many_jobs_run_without_corrupting_each_others_state():
    store = JobStore()
    jobs = [store.submit("render", (lambda n: lambda progress: n)(i))
            for i in range(20)]
    for job in jobs:
        job.wait(5)
    assert [j.result for j in jobs] == list(range(20))
    assert all(j.status == "done" for j in jobs)
