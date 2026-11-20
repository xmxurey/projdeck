"""SIGTERM while a connection is in flight must not put a traceback in the journal.

socketserver closes the request socket when an exception that is not an Exception (our
SIGTERM, _Terminated) unwinds process_request; a handler thread already started on that
socket then fails with EBADF. systemd stops the service with SIGTERM, so this ran at every
restart that caught a client mid-request."""
import os
import signal
import socket
import subprocess
import sys
import time

import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHILD = """
import copy, os, signal, socketserver, sys, time
from projdeck import config, serve
if os.environ.get("TEST_SIGTERM_INSIDE_PROCESS_REQUEST"):
    # the signal lands right after the handler thread started, inside process_request
    orig = socketserver.ThreadingMixIn.process_request
    def hooked(self, request, client_address):
        orig(self, request, client_address)
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(2)
    socketserver.ThreadingMixIn.process_request = hooked
cfg = copy.deepcopy(config.DEFAULTS)
cfg["hosts"] = [{"name": "loc", "local": True}]
cfg["serve"] = dict(cfg["serve"], push_statusline=False)
sys.exit(serve.serve(cfg, "127.0.0.1", int(sys.argv[1])) or 0)
"""

HALF_REQUEST = b"GET /api/state.json HTTP/1.1\r\nHost: 127.0.0.1\r\n"  # no blank line: head unfinished


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve_then_sigterm(tmp_path, inside_process_request):
    port = _free_port()
    home = tmp_path / "h"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), PROJ_CACHE=str(tmp_path / "cache"),
               PROJ_CONFIG=str(tmp_path / "no-config.json"), PYTHONPATH=ROOT, PYTHONUNBUFFERED="1")
    env.pop("TEST_SIGTERM_INSIDE_PROCESS_REQUEST", None)
    if inside_process_request:
        env["TEST_SIGTERM_INSIDE_PROCESS_REQUEST"] = "1"
    errfile = tmp_path / "serve.err"
    with open(str(errfile), "w") as errf:
        p = subprocess.Popen([sys.executable, "-c", CHILD, str(port)], cwd=ROOT, env=env,
                             stdout=subprocess.DEVNULL, stderr=errf)
        try:
            deadline = time.time() + 30
            while "http://" not in errfile.read_text() and p.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            assert p.poll() is None, errfile.read_text()
            client = socket.create_connection(("127.0.0.1", port), 5)
            client.sendall(HALF_REQUEST)
            if not inside_process_request:
                time.sleep(0.3)  # the handler thread is reading the head
                p.send_signal(signal.SIGTERM)
            p.wait(30)
            client.close()
        finally:
            if p.poll() is None:
                p.kill()
                p.wait(10)
    return p.returncode, errfile.read_text()


def test_sigterm_with_half_a_request_in_flight_prints_no_traceback(tmp_path):
    rc, err = _serve_then_sigterm(tmp_path, inside_process_request=False)
    assert rc == 0, err[-800:]
    assert "Traceback" not in err, err[-800:]


def test_sigterm_inside_process_request_prints_no_traceback(tmp_path):
    """The deterministic form of the race: the signal arrives while the request is handed over."""
    rc, err = _serve_then_sigterm(tmp_path, inside_process_request=True)
    assert rc == 0, err[-800:]
    assert "Traceback" not in err, err[-800:]
    assert "Bad file descriptor" not in err


def test_ebadf_without_sigterm_keeps_its_traceback(tmp_path, capsys):
    """Only shutdown noise is silenced: an EBADF in a handler with no SIGTERM is a bug."""
    import errno
    from projdeck import serve
    serve._sigterm_received.clear()
    srv = serve.make_server({}, "127.0.0.1", 0, lambda: {}, ts_self=None)
    try:
        try:
            raise OSError(errno.EBADF, "Bad file descriptor")
        except OSError:
            srv.handle_error(None, ("127.0.0.1", 1))
        assert "Bad file descriptor" in capsys.readouterr().err
        serve._sigterm_received.set()
        try:
            raise OSError(errno.EBADF, "Bad file descriptor")
        except OSError:
            srv.handle_error(None, ("127.0.0.1", 1))
        assert capsys.readouterr().err == ""
    finally:
        serve._sigterm_received.clear()
        srv.server_close()
