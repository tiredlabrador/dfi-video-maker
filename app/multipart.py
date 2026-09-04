"""
A small multipart/form-data parser.

Python 3.13 removed the stdlib `cgi` module, which is how file uploads used to
be parsed. Rather than add a web framework as a dependency (every extra package
is one more thing that can fail on someone else's laptop), the local app parses
uploads itself. This is the whole of that job.

Deliberately byte-oriented: MP3s are binary and contain byte sequences that look
like boundary markers, so the body is split on the exact delimiter and never
decoded as text.
"""
from __future__ import annotations

from dataclasses import dataclass


class ParseError(ValueError):
    """The request body was not a multipart form we could read."""


@dataclass
class UploadedFile:
    """One file from the form: its original name and its raw bytes."""
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


def _boundary_from(content_type: str) -> bytes:
    if "multipart/form-data" not in content_type:
        raise ParseError(
            f"Expected a multipart/form-data upload, got {content_type!r}."
        )
    for piece in content_type.split(";"):
        piece = piece.strip()
        if piece.startswith("boundary="):
            value = piece[len("boundary="):].strip().strip('"')
            if value:
                return value.encode("ascii")
    raise ParseError("The upload had no boundary marker in its Content-Type.")


def _split_headers(raw: bytes) -> tuple[dict[str, str], bytes]:
    """Split one part into its headers and its body."""
    head, _, body = raw.partition(b"\r\n\r\n")
    headers: dict[str, str] = {}
    for line in head.split(b"\r\n"):
        if not line:
            continue
        name, _, value = line.decode("utf-8", "replace").partition(":")
        headers[name.strip().lower()] = value.strip()
    return headers, body


def _disposition_params(value: str) -> dict[str, str]:
    """Pull name=... and filename=... out of a Content-Disposition header."""
    params: dict[str, str] = {}
    for piece in value.split(";")[1:]:
        key, _, val = piece.strip().partition("=")
        params[key.strip().lower()] = val.strip().strip('"')
    return params


def parse_multipart(body: bytes, content_type: str):
    """
    Parse a multipart body into (fields, files).

    `fields` maps name -> text value. `files` maps name -> UploadedFile, and a
    file input the user left empty is simply absent rather than present-and-blank.
    """
    boundary = _boundary_from(content_type)
    delimiter = b"--" + boundary

    fields: dict[str, str] = {}
    files: dict[str, UploadedFile] = {}

    for chunk in body.split(delimiter):
        # The preamble before the first delimiter and the "--" terminator after
        # the last one are not parts.
        if chunk in (b"", b"--", b"--\r\n") or chunk.startswith(b"--"):
            continue
        raw = chunk.lstrip(b"\r\n")
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]

        headers, content = _split_headers(raw)
        disposition = headers.get("content-disposition", "")
        if "form-data" not in disposition:
            continue
        params = _disposition_params(disposition)
        name = params.get("name")
        if not name:
            continue

        if "filename" in params:
            if not params["filename"]:
                continue          # an empty file input: the user chose nothing
            files[name] = UploadedFile(
                filename=params["filename"],
                content=content,
                content_type=headers.get("content-type",
                                         "application/octet-stream"),
            )
        else:
            fields[name] = content.decode("utf-8", "replace")

    return fields, files
