"""
Tests for picking the port the app runs on.

Found when restarting the app: right after the previous copy stopped, its port
was briefly in the operating system's "just closed" state. The server itself
can reuse a port in that state, but the launcher's check couldn't, so it
wrongly decided the port was taken and fell back to a random one — and the
Raycast shortcut, which looks on the usual port, found nothing.
"""
import socket

from app.__main__ import _first_free_port


def test_a_port_that_has_only_just_been_closed_is_still_used():
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen()
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    conn.close()                      # server side closes first: TIME_WAIT
    client.close()
    server.close()
    assert _first_free_port("127.0.0.1", port) == port


def test_a_port_genuinely_in_use_is_not_used():
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    try:
        assert _first_free_port("127.0.0.1", busy.getsockname()[1]) == 0
    finally:
        busy.close()
