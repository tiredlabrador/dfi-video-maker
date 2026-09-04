"""
Start the DFI Video Maker and open it in your browser.

    python3 -m app

Stops with Ctrl-C. Nothing it makes leaves your machine.
"""
from __future__ import annotations

import argparse
import shutil
import socket
import sys
import threading
import webbrowser

from app.server import create_server

BANNER = """
  ┌────────────────────────────────────────────┐
  │  DFI Video Maker                           │
  │  {url:<42}│
  │                                            │
  │  Leave this window open while you work.    │
  │  Press Ctrl-C here when you're finished.   │
  └────────────────────────────────────────────┘
"""


def _first_free_port(host: str, preferred: int) -> int:
    """Use the usual port if it is free, otherwise let the system pick one."""
    with socket.socket() as probe:
        try:
            probe.bind((host, preferred))
            return preferred
        except OSError:
            return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the DFI Video Maker locally.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open a browser window automatically.")
    parser.add_argument("--verbose", action="store_true",
                        help="Log every request.")
    args = parser.parse_args(argv)

    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        print(f"\n  {' and '.join(missing)} is not installed.\n"
              f"  On a Mac, install it with:  brew install ffmpeg\n",
              file=sys.stderr)
        return 1

    host = "127.0.0.1"
    server = create_server(host=host,
                           port=_first_free_port(host, args.port),
                           verbose=args.verbose)
    url = f"http://{host}:{server.server_address[1]}/"
    print(BANNER.format(url=url))

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped. Your videos are still in the folder you saved them to.\n")
    finally:
        server.shutdown()
        server.server_close()
        # Delete the working copies. Anything worth keeping has already been
        # downloaded; without this the temp folder grows every single run.
        server.render_service.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
