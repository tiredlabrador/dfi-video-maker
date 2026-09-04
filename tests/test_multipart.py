"""
Tests for the multipart/form-data parser.

Python 3.13 removed the stdlib `cgi` module, which is what everyone used to
parse file uploads. The local app has to parse them itself, so this is our own
small parser and it needs to be right.
"""
import pytest

from app.multipart import ParseError, parse_multipart


def build(parts, boundary="BOUNDARY123"):
    """Assemble a multipart body the way a browser would."""
    chunks = []
    for part in parts:
        head = f'Content-Disposition: form-data; name="{part["name"]}"'
        if "filename" in part:
            head += f'; filename="{part["filename"]}"'
        head += "\r\n"
        if "content_type" in part:
            head += f'Content-Type: {part["content_type"]}\r\n'
        chunks.append(
            b"--" + boundary.encode() + b"\r\n"
            + head.encode() + b"\r\n"
            + part["value"]
        )
    body = b"\r\n".join(chunks) + b"\r\n--" + boundary.encode() + b"--\r\n"
    return body, f"multipart/form-data; boundary={boundary}"


def test_reads_a_plain_text_field():
    body, ctype = build([{"name": "clip_start", "value": b"1:30"}])
    fields, files = parse_multipart(body, ctype)
    assert fields["clip_start"] == "1:30"
    assert files == {}


def test_reads_an_uploaded_file_with_its_name_and_bytes():
    body, ctype = build([
        {"name": "audio", "filename": "track.mp3",
         "content_type": "audio/mpeg", "value": b"\xff\xfbID3-ish bytes"},
    ])
    fields, files = parse_multipart(body, ctype)
    assert fields == {}
    assert files["audio"].filename == "track.mp3"
    assert files["audio"].content == b"\xff\xfbID3-ish bytes"


def test_reads_fields_and_files_together_in_one_body():
    body, ctype = build([
        {"name": "track", "value": "Fantasy".encode()},
        {"name": "audio", "filename": "a.mp3", "value": b"AUDIO"},
        {"name": "artwork", "filename": "art.png", "value": b"PNG"},
    ])
    fields, files = parse_multipart(body, ctype)
    assert fields["track"] == "Fantasy"
    assert set(files) == {"audio", "artwork"}
    assert files["artwork"].content == b"PNG"


def test_binary_content_survives_bytes_that_look_like_a_boundary_char():
    # Real MP3s contain 0x2D ('-') runs; a naive split on "--" would corrupt them.
    payload = b"\x00--not-a-boundary--\xff" * 50
    body, ctype = build([{"name": "audio", "filename": "a.mp3", "value": payload}])
    _, files = parse_multipart(body, ctype)
    assert files["audio"].content == payload


def test_empty_file_input_is_reported_as_no_file():
    # A browser sends an empty part with filename="" when nothing was chosen.
    body, ctype = build([{"name": "artwork", "filename": "", "value": b""}])
    _, files = parse_multipart(body, ctype)
    assert "artwork" not in files


def test_utf8_text_field_decodes():
    body, ctype = build([{"name": "artist", "value": "Björk".encode("utf-8")}])
    fields, _ = parse_multipart(body, ctype)
    assert fields["artist"] == "Björk"


def test_missing_boundary_is_a_clear_error():
    with pytest.raises(ParseError):
        parse_multipart(b"whatever", "multipart/form-data")


def test_wrong_content_type_is_a_clear_error():
    with pytest.raises(ParseError):
        parse_multipart(b"whatever", "application/json")
