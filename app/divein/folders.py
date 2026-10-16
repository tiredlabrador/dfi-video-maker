"""
Finding the camera footage folder without typing its location.

Footage is far too big to copy into the app, so the app reads it where it
lies, and needs its location on this Mac. A browser never tells a page where
a dropped folder lives, only its name and what's inside. So either the Mac's
own folder chooser is opened (it runs on this Mac too), or the drives are
searched for a folder with that name holding those exact videos.
"""
from __future__ import annotations

import os
import subprocess
import time
from collections import deque

MAX_DEPTH = 7            # /Volumes/Drive/Edits/2025/Session/Camera is 5 deep
TIME_LIMIT = 20.0        # seconds; a found folder is usually far quicker
SKIP_NAMES = {"Library", "System", "Applications", "node_modules", "$RECYCLE.BIN",
              "System Volume Information"}
SKIP_ENDINGS = (".app", ".photoslibrary", ".fcpbundle", ".bundle", ".framework",
                ".lrdata", ".cache")


def search_roots() -> list[str]:
    """Where footage lives: plugged-in drives first, then the usual home folders."""
    roots = []
    try:
        for name in sorted(os.listdir("/Volumes")):
            path = os.path.join("/Volumes", name)
            # The Mac's own disk appears here too, as a link back to "/".
            if not name.startswith(".") and os.path.isdir(path) and not os.path.islink(path):
                roots.append(path)
    except OSError:
        pass
    home = os.path.expanduser("~")
    roots += [os.path.join(home, n) for n in ("Movies", "Desktop", "Downloads", "Documents", "Pictures")]
    return roots


def _holds(entries: dict[str, int], files: list[dict]) -> bool:
    return all(entries.get(f["name"]) == f["size"] for f in files)


def locate_folder(name: str | None, files: list[dict], roots: list[str] | None = None,
                  time_limit: float = TIME_LIMIT) -> str | None:
    """
    The folder called `name` (any name, if None) holding all of `files`
    ({"name", "size"}, sizes in bytes). Videos to the exact byte are proof
    enough, so the first such folder is the answer (a second would be a copy
    of the same footage). With only a name to go on, None unless exactly one
    folder has it: better to ask than to guess.
    """
    files = [f for f in files or [] if isinstance(f, dict) and f.get("name")]
    if not name and not files:
        return None
    deadline = time.monotonic() + time_limit
    found: list[str] = []
    queue = deque((os.path.abspath(r), 0) for r in (roots if roots is not None else search_roots()))
    seen = set()
    while queue and time.monotonic() < deadline:
        folder, depth = queue.popleft()
        try:
            real = os.path.realpath(folder)
            if real in seen:
                continue
            seen.add(real)
            entries, subfolders = {}, []
            with os.scandir(folder) as listing:
                for entry in listing:
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in SKIP_NAMES and not entry.name.endswith(SKIP_ENDINGS):
                            subfolders.append(entry.path)
                    elif files and entry.is_file(follow_symlinks=False):
                        entries[entry.name] = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        if (not name or os.path.basename(folder) == name) and _holds(entries, files):
            if files:
                return folder
            found.append(folder)
            if len(found) > 1:
                return None
        if depth < MAX_DEPTH:
            queue.extend((s, depth + 1) for s in sorted(subfolders))
    return found[0] if len(found) == 1 else None


def choose_folder(start: str | None = None) -> str | None:
    """Open the Mac's own folder chooser. The chosen folder, or None if closed."""
    where = ""
    if start and os.path.isdir(start):
        where = f' default location (POSIX file "{_quote(start)}")'
    script = ("activate\n"
              f'POSIX path of (choose folder with prompt "Choose the camera footage folder"{where})')
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                            timeout=600)
    if result.returncode != 0:
        if "-128" in result.stderr:                  # Cancel
            return None
        raise RuntimeError("The Mac's folder chooser didn't open: " + result.stderr.strip())
    path = result.stdout.strip()
    return path.rstrip("/") or path or None


def _quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
