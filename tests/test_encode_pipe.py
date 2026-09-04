"""
Tests for the pipe between the frame generator and ffmpeg.

The engine writes hundreds of megabytes of raw frames into ffmpeg's input while
ffmpeg writes to its output and error pipes. If nobody is emptying those pipes,
a chatty ffmpeg fills the operating system's pipe buffer (64KB), stops reading
its input, and both sides wait for each other forever.

It has never happened in practice because the engine runs ffmpeg with `-v error`
and a successful run says almost nothing. But a *failing* run is exactly when
ffmpeg gets talkative, so the deadlock would arrive on the day something else
was already wrong — and there is no cancel button.
"""
import sys
import time

import pytest

import generate_video as gv


def test_a_noisy_process_does_not_deadlock_the_writer():
    """
    Stand-in for a failing ffmpeg: reads its input, and writes far more to
    stderr than a pipe buffer can hold.
    """
    noisy = [
        sys.executable, "-c",
        "import sys;"
        "sys.stderr.write('x' * 300000); sys.stderr.flush();"
        "d = sys.stdin.buffer.read();"
        "sys.stderr.write('done %d' % len(d)); sys.stderr.flush();"
        "sys.exit(1)",
    ]
    chunks = (b"F" * 100_000 for _ in range(20))     # ~2MB in

    start = time.time()
    with pytest.raises(gv.RenderError) as caught:
        gv.pipe_frames_to(noisy, chunks)
    assert time.time() - start < 20, "the write deadlocked against the error pipe"
    assert "done" in str(caught.value)


def test_a_process_that_exits_early_is_reported_not_hung():
    """ffmpeg rejecting its arguments closes the pipe immediately."""
    quitter = [sys.executable, "-c",
               "import sys; sys.stderr.write('bad arguments'); sys.exit(2)"]
    chunks = (b"F" * 100_000 for _ in range(50))

    start = time.time()
    with pytest.raises(gv.RenderError) as caught:
        gv.pipe_frames_to(quitter, chunks)
    assert time.time() - start < 20
    assert "bad arguments" in str(caught.value)


def test_a_successful_process_returns_quietly():
    fine = [sys.executable, "-c",
            "import sys; sys.stdin.buffer.read(); sys.exit(0)"]
    gv.pipe_frames_to(fine, (b"F" * 1000 for _ in range(10)))


def test_the_error_message_carries_what_the_tool_actually_said():
    """
    The message reaches a non-developer through the UI, so the real reason has
    to survive rather than being replaced with 'render failed'.
    """
    failing = [sys.executable, "-c",
               "import sys; sys.stdin.buffer.read();"
               "sys.stderr.write('No such file or directory'); sys.exit(1)"]
    with pytest.raises(gv.RenderError) as caught:
        gv.pipe_frames_to(failing, (b"F" * 1000 for _ in range(5)))
    assert "No such file or directory" in str(caught.value)
