"""
Reading just the sound out of a camera's MP4, without wading through the picture.

A two-hour camera file is 17GB, of which the sound is about 250MB. ffmpeg
reads the whole file to get at it, which off a USB drive takes a minute. But
the file has an index (its "moov" box, usually at the end) listing exactly
where every scrap of sound sits. So: read the index, read only those bytes,
wrap each scrap in the small header a bare AAC stream needs (ADTS), and let
ffmpeg decode that. Seconds instead of a minute.

Anything unexpected (not AAC, an unusual edit list, a damaged index) returns
None, and the caller reads the whole file the ordinary way instead.
"""
from __future__ import annotations

import os
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor

import numpy as np

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf"}


class _Unreadable(Exception):
    pass


def _boxes(data: bytes, start: int = 0, end: int | None = None):
    """(type, payload start, end) for each box in data[start:end]."""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        head = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            raise _Unreadable("a box runs past its container")
        yield kind, pos + head, pos + size
        pos += size


def _find_moov(f, file_size: int) -> bytes:
    pos = 0
    while pos + 8 <= file_size:
        f.seek(pos)
        head = f.read(16)
        size, kind = struct.unpack(">I4s", head[:8])
        if size == 1:
            size = struct.unpack(">Q", head[8:16])[0]
        elif size == 0:
            size = file_size - pos
        if size < 8:
            raise _Unreadable("a damaged top-level box")
        if kind == b"moov":
            f.seek(pos)
            return f.read(size)
        pos += size                               # skip the picture, mdat and all
    raise _Unreadable("no index (moov) found")


def _children(data: bytes, start: int, end: int) -> dict:
    return {kind: (a, b) for kind, a, b in _boxes(data, start, end)}


def _descriptor(data: bytes, pos: int):
    """An MPEG-4 descriptor: (tag, payload start, payload end)."""
    tag = data[pos]
    pos += 1
    length = 0
    for _ in range(4):
        byte = data[pos]
        pos += 1
        length = (length << 7) | (byte & 0x7F)
        if not byte & 0x80:
            break
    return tag, pos, pos + length


def _audio_config(data: bytes, start: int, end: int) -> bytes:
    """The AudioSpecificConfig from an mp4a sample entry's esds box."""
    if data[start + 4:start + 8] != b"mp4a":
        raise _Unreadable("the sound isn't AAC")
    entry = start + 8
    version = struct.unpack(">H", data[entry + 8:entry + 10])[0]
    children = entry + 28 + {0: 0, 1: 16, 2: 36}.get(version, -10_000)
    if children < entry:
        raise _Unreadable("an unknown sound description")
    for kind, a, b in _boxes(data, children, end):
        if kind != b"esds":
            continue
        tag, p, q = _descriptor(data, a + 4)
        if tag != 3:
            raise _Unreadable("an unexpected esds")
        flags = data[p + 2]
        p += 3 + (2 if flags & 0x80 else 0) + (2 if flags & 0x20 else 0)
        if flags & 0x40:
            p += 1 + data[p]
        tag, p, q = _descriptor(data, p)
        if tag != 4 or data[p] != 0x40:                   # 0x40: MPEG-4 audio
            raise _Unreadable("the sound isn't AAC")
        tag, p, q = _descriptor(data, p + 13)
        if tag != 5:
            raise _Unreadable("no AAC configuration")
        return data[p:q]
    raise _Unreadable("no esds box")


def _sound_track(moov: bytes):
    _, inside, end = next(_boxes(moov))              # into the moov box itself
    for kind, a, b in _boxes(moov, inside, end):
        if kind != b"trak":
            continue
        trak = _children(moov, a, b)
        mdia = _children(moov, *trak[b"mdia"])
        ha, _ = mdia[b"hdlr"]
        if moov[ha + 8:ha + 12] == b"soun":
            return trak, mdia
    raise _Unreadable("no sound track")


def _table(moov: bytes, box) -> tuple[int, int]:
    a, _ = box
    return a + 8, struct.unpack(">I", moov[a + 4:a + 8])[0]   # (entries start, count)


def _layout(moov: bytes):
    """Everything needed to find and decode the sound."""
    trak, mdia = _sound_track(moov)
    ma, _ = mdia[b"mdhd"]
    timescale = struct.unpack(">I", moov[ma + 20:ma + 24] if moov[ma] == 1
                              else moov[ma + 12:ma + 16])[0]
    skip = 0
    if b"edts" in trak:
        edts = _children(moov, *trak[b"edts"])
        if b"elst" in edts:
            ea, _ = edts[b"elst"]
            version = moov[ea]
            count = struct.unpack(">I", moov[ea + 4:ea + 8])[0]
            if count != 1:
                raise _Unreadable("an edit list this reader doesn't follow")
            skip = struct.unpack(">q", moov[ea + 16:ea + 24])[0] if version == 1 \
                else struct.unpack(">i", moov[ea + 12:ea + 16])[0]
            if skip < 0:
                raise _Unreadable("an edit list that delays the sound")
    minf = _children(moov, *mdia[b"minf"])
    stbl = _children(moov, *minf[b"stbl"])
    sa, sb = stbl[b"stsd"]
    entries = list(_boxes(moov, sa + 8, sb))
    if len(entries) != 1:
        raise _Unreadable("more than one kind of sound")
    config = _audio_config(moov, entries[0][1] - 8, entries[0][2])

    za, _ = stbl[b"stsz"]
    fixed, count = struct.unpack(">II", moov[za + 4:za + 12])
    sizes = np.full(count, fixed, dtype=np.int64) if fixed else \
        np.frombuffer(moov, dtype=">u4", count=count, offset=za + 12).astype(np.int64)
    pos, n = _table(moov, stbl[b"stsc"])
    runs = np.frombuffer(moov, dtype=">u4", count=3 * n, offset=pos).reshape(n, 3).astype(np.int64)
    if b"co64" in stbl:
        pos, n = _table(moov, stbl[b"co64"])
        offsets = np.frombuffer(moov, dtype=">u8", count=n, offset=pos).astype(np.int64)
    else:
        pos, n = _table(moov, stbl[b"stco"])
        offsets = np.frombuffer(moov, dtype=">u4", count=n, offset=pos).astype(np.int64)

    per_chunk = np.zeros(len(offsets), dtype=np.int64)       # samples in each chunk
    for k, (first, samples, _) in enumerate(runs):
        last = runs[k + 1][0] - 1 if k + 1 < len(runs) else len(offsets)
        per_chunk[first - 1:last] = samples
    if per_chunk.sum() != len(sizes):
        raise _Unreadable("the index doesn't add up")
    return config, timescale, skip, offsets, per_chunk, sizes


def _adts_header(config: bytes, length: int) -> bytes:
    bits = int.from_bytes(config[:2], "big")
    kind, rate, channels = bits >> 11, (bits >> 7) & 0xF, (bits >> 3) & 0xF
    if not 1 <= kind <= 4 or rate > 12 or not 1 <= channels <= 7:
        raise _Unreadable("an AAC kind a bare stream can't carry")
    total = length + 7
    return bytes([0xFF, 0xF1, ((kind - 1) << 6) | (rate << 2) | (channels >> 2),
                  ((channels & 3) << 6) | (total >> 11), (total >> 3) & 0xFF,
                  ((total & 7) << 5) | 0x1F, 0xFC])


def read_sound(path: str, sample_rate: int) -> np.ndarray | None:
    """The file's sound as mono samples at `sample_rate`, or None if it can't be read alone."""
    try:
        with open(path, "rb") as f:
            moov = _find_moov(f, os.path.getsize(path))
            config, timescale, skip, offsets, per_chunk, sizes = _layout(moov)
            _adts_header(config, 0)                          # can a bare stream carry it?
            ends = np.cumsum(per_chunk)
            total = np.concatenate([[0], np.cumsum(sizes)])
            lengths = total[ends] - total[ends - per_chunk]
            # The sound is in tens of thousands of small pieces. A drive answers
            # many requests at once far faster than one after another.
            with ThreadPoolExecutor(16) as pool:
                chunks = list(pool.map(lambda job: os.pread(f.fileno(), int(job[1]), int(job[0])),
                                       zip(offsets, lengths)))
        stream = bytearray()
        first = 0
        for chunk, count in zip(chunks, per_chunk):
            if len(chunk) != total[first + count] - total[first]:
                raise _Unreadable("the file ends before its index says")
            at = 0
            for size in sizes[first:first + count]:
                size = int(size)
                stream += _adts_header(config, size)
                stream += chunk[at:at + size]
                at += size
            first += count
    except (_Unreadable, OSError, struct.error, KeyError, IndexError, ValueError):
        return None
    # The edit list trims the encoder's warm-up from the start, as a full read does.
    trim = f"atrim=start={skip / timescale:.6f}," if skip else ""
    result = subprocess.run(["ffmpeg", "-v", "error", "-f", "aac", "-i", "pipe:0",
                             "-af", f"{trim}aresample={sample_rate}", "-ac", "1",
                             "-f", "f32le", "pipe:1"], input=bytes(stream), capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return None
    return np.frombuffer(result.stdout, dtype=np.float32)
