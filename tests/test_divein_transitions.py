"""
Tests for spotting the rough moments a DJ mix moves from one record to the next.

Made-up mixes are used because they have a right answer: records of known
length, crossfaded at known moments. Each has its own key and sound, and moves
through its own chords, as real records do, so a chord change inside a record
mustn't count as a transition.
"""
import numpy as np
import pytest

from app.divein.transitions import SR, find_transitions

KEYS = [0, 5, 2, 7, 3, 9, 4, 11]                 # semitones above A
PROGRESSIONS = [[0, 5, 7, 3], [0, 3, 8, 7], [0, 7, 9, 5], [0, 4, 5, 7]]


def record(seconds, index, rng):
    """A loop-based record: kick, hats, and a four-chord pad in its own key."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    bpm = 120 + 4 * (index % 3)
    beat = 60 / bpm
    out = np.zeros(n)
    phase = t % beat                                  # kick on every beat
    out += 0.6 * np.sin(2 * np.pi * 55 * phase * (1 + 2 * np.exp(-phase * 40))) \
        * np.exp(-phase * 18)
    hat = (t + beat / 2) % beat                       # hats off the beat
    out += 0.08 * rng.normal(size=n) * np.exp(-hat * 60) * (1 + index % 2)
    chords = PROGRESSIONS[index % len(PROGRESSIONS)]
    bar = 4 * beat
    root = 220 * 2 ** (KEYS[index % len(KEYS)] / 12)
    which = (t // (2 * bar)).astype(int) % 4          # a new chord every two bars
    for step in range(4):
        mask = which == step
        base = root * 2 ** (chords[step] / 12)
        for interval in (0, 4 if index % 2 else 3, 7):
            f = base * 2 ** (interval / 12)
            out[mask] += 0.15 * np.sin(2 * np.pi * f * t[mask])
    return out


def mix(lengths, fade=16.0, seed=1):
    """Records end to end, each crossfaded into the next. Returns (audio, changes)."""
    rng = np.random.default_rng(seed)
    out = np.zeros(0)
    changes = []
    f = int(fade * SR)
    for i, seconds in enumerate(lengths):
        rec = record(seconds, i, rng)
        if not out.size:
            out = rec
            continue
        ramp = np.linspace(0, 1, f)
        start = out.size - f
        changes.append((start + f / 2) / SR)          # the middle of the blend
        blended = out[start:] * (1 - ramp) + rec[:f] * ramp
        out = np.concatenate([out[:start], blended, rec[f:]])
    return out.astype(np.float32), changes


def matched(found, expected, within):
    return [any(abs(f - e) <= within for f in found) for e in expected]


def test_the_changes_between_records_are_found_roughly_where_they_happen():
    audio, changes = mix([170, 140, 200, 150, 180, 160])
    found = find_transitions(audio)
    assert all(matched(found, changes, within=15)), (found, changes)
    assert len(found) <= len(changes) + 1                  # at most one false alarm


def test_one_long_record_has_no_transitions():
    audio, _ = mix([600])
    assert find_transitions(audio) == []


def test_a_short_quick_blend_is_still_found():
    audio, changes = mix([150, 150, 150], fade=4)
    assert all(matched(find_transitions(audio), changes, within=15))


def test_a_mix_too_short_to_judge_has_none():
    audio, _ = mix([40])
    assert find_transitions(audio) == []


def test_transitions_come_back_in_order_as_whole_seconds():
    audio, _ = mix([170, 140, 200, 150])
    found = find_transitions(audio)
    assert found == sorted(found) and all(float(f).is_integer() for f in found)


def test_silence_does_not_break_it():
    assert find_transitions(np.zeros(SR * 300, dtype=np.float32)) == []


# ── over the app's own server ────────────────────────────────────────────
import json
import threading
import urllib.error
import urllib.request
import wave

from app.server import create_server


@pytest.fixture
def server(tmp_path):
    srv = create_server(host="127.0.0.1", port=0, work_dir=str(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


def upload(server, audio, tmp_path):
    path = tmp_path / "mix.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((audio / np.abs(audio).max() * 30000).astype(np.int16).tobytes())
    req = urllib.request.Request(f"{server.base_url}/api/divein/upload?kind=audio",
                                 data=path.read_bytes(), method="POST",
                                 headers={"Content-Type": "application/octet-stream",
                                          "X-Filename": "mix.wav"})
    return json.load(urllib.request.urlopen(req, timeout=60))["token"]


def test_the_page_can_ask_where_the_transitions_are(server, tmp_path):
    audio, changes = mix([170, 140, 200])
    token = upload(server, audio, tmp_path)
    found = json.load(urllib.request.urlopen(
        f"{server.base_url}/api/divein/transitions?audio={token}", timeout=60))["times"]
    assert all(matched(found, changes, within=15))


def test_transitions_of_an_unknown_mix_are_refused(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(f"{server.base_url}/api/divein/transitions?audio=nope.mp3",
                               timeout=10)
    assert caught.value.code == 400


def test_a_long_silence_after_the_music_is_not_full_of_transitions():
    """Recorders get left running: silence mustn't look like a new record every minute."""
    audio, _ = mix([300])
    audio = np.concatenate([audio, np.zeros(300 * SR, dtype=np.float32)])
    found = find_transitions(audio)
    assert all(abs(t - 300) <= 40 for t in found), found
