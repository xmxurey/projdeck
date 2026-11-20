"""Independent spec tests for T15: per-connection header deadline, connection cap."""
import http.client
import socket
import threading
import time

import pytest

from projdeck import config, serve

TS = {"ip": "127.0.0.1", "dns_name": "demo-vm.tail0000.ts.net", "host_name": "demo-vm"}
NOW = 1_800_000_000


def board(pad=0):
    return {"hosts": [{"name": "s-host", "live_at": NOW, "collect_at": NOW, "stale_since": None,
                       "error": None, "source": "agents"}],
            "summary": {"waiting": 1, "busy": 0, "sub": 0, "idle": 0, "open_todos": 0,
                        "todo_projects": 0, "today": {"busy": 0, "sub": 0, "wait": 0}},
            "waiting": [{"id": "a", "host": "s-host", "project": "/home/u/p", "name": "n-a",
                         "title": "", "state": "waiting", "idle_since": NOW - 600,
                         "question": "q" * pad, "last_reply": "",
                         "last_human": {"ts": 0, "text": ""}, "last_input": "human",
                         "subagents_running": 0, "segments": [], "attach": "zellij attach z-a",
                         "tasks": None, "tasks_owner": None, "errors": []}],
            "busy": [], "idle": [], "ended_today": [], "stale": [], "stale_host_sessions": [],
            "top_todos": [], "backlogs": [], "tiles": [],
            "lanes": {"start": NOW - 4 * 3600, "end": NOW, "rows": [], "more": 0}}


@pytest.fixture
def start(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    started = []

    def _start(host="127.0.0.1", snap=None):
        snap = snap or (lambda: board())
        srv = serve.make_server(config.DEFAULTS, host, 0, snap)
        t = threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True)
        t.start()
        started.append((srv, t))
        return srv.server_address[1]
    yield _start
    for srv, t in started:
        srv.shutdown()
        srv.server_close()
        t.join(5)
        assert not t.is_alive()


def connect(port, family=socket.AF_INET, rcvbuf=None):
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(5)
    if rcvbuf:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    s.connect(("::1" if family == socket.AF_INET6 else "127.0.0.1", port))
    return s


def wait_closed(s, limit):
    """Drain until the peer closes. Returns (closed, bytes_received, elapsed)."""
    t0 = time.monotonic()
    got = b""
    while time.monotonic() - t0 < limit:
        s.settimeout(max(0.05, limit - (time.monotonic() - t0)))
        try:
            d = s.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            return True, got, time.monotonic() - t0
        if not d:
            return True, got, time.monotonic() - t0
        got += d
    return False, got, time.monotonic() - t0


def get(port, path="/", method="GET", family=socket.AF_INET):
    s = connect(port, family)
    try:
        s.sendall(("%s %s HTTP/1.0\r\nHost: demo-vm\r\n\r\n" % (method, path)).encode())
        data = bytearray()
        s.settimeout(5)
        while True:
            d = s.recv(65536)
            if not d:
                break
            data += d
    finally:
        s.close()
    head, _, body = bytes(data).partition(b"\r\n\r\n")
    return int(head.split()[1]), head, body


# ---- acceptance 1

def test_trickled_headers_are_cut_off_by_total_deadline(start, monkeypatch):
    monkeypatch.setattr(serve, "REQUEST_TIMEOUT", 1)
    port = start()
    s = connect(port)
    t0 = time.monotonic()
    try:
        s.sendall(b"GET / HTTP/1.0\r\n")
        closed = False
        while time.monotonic() - t0 < 2.6:
            try:
                s.sendall(b"X-Slow: 1\r\n")
            except OSError:
                closed = True
                break
            s.settimeout(0.4)
            try:
                if s.recv(100) == b"":
                    closed = True
                    break
            except socket.timeout:
                pass
            except OSError:
                closed = True
                break
        elapsed = time.monotonic() - t0
    finally:
        s.close()
    assert closed, "server never closed a trickling connection"
    assert elapsed <= 2.2, elapsed


def test_silent_connection_is_closed_after_timeout(start, monkeypatch):
    monkeypatch.setattr(serve, "REQUEST_TIMEOUT", 1)
    port = start()
    s = connect(port)
    try:
        closed, got, el = wait_closed(s, 2.5)
    finally:
        s.close()
    assert closed and el <= 2.2 and el >= 0.5, (closed, el)


def test_stalled_connection_does_not_block_others_and_slot_is_freed(start, monkeypatch):
    monkeypatch.setattr(serve, "REQUEST_TIMEOUT", 1)
    monkeypatch.setattr(serve, "MAX_CONNECTIONS", 1)
    port = start()
    s = connect(port)
    try:
        time.sleep(0.2)
        closed, got, _ = wait_closed(connect(port), 1.0)  # over the cap: closed at once
        assert closed and got == b""
        closed, _, el = wait_closed(s, 2.5)  # stalled one times out
        assert closed
    finally:
        s.close()
    time.sleep(0.2)
    assert get(port, "/api/state.json")[0] == 200  # slot released after timeout


# ---- acceptance 2

def test_constants():
    assert serve.MAX_CONNECTIONS == 32
    assert serve.REQUEST_TIMEOUT == 30


@pytest.mark.parametrize("family,host", [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")])
def test_connection_cap(start, monkeypatch, family, host):
    monkeypatch.setattr(serve, "MAX_CONNECTIONS", 2)
    try:
        port = start(host)
    except OSError:
        pytest.skip("no IPv6 loopback")
    a = connect(port, family)
    b = connect(port, family)
    time.sleep(0.4)
    c = connect(port, family)
    try:
        t0 = time.monotonic()
        closed, got, el = wait_closed(c, 1.5)
        assert closed, "third connection was not closed"
        assert got == b"", "over-cap connection got a response: %r" % got[:40]
        assert el < 1.0
        # idle ones are still held (not closed by the server)
        a.settimeout(0.3)
        with pytest.raises(socket.timeout):
            a.recv(10)
        a.close()
        time.sleep(0.5)
        st, _, body = get(port, "/api/state.json", family=family)
        assert st == 200 and body
    finally:
        for x in (a, b, c):
            x.close()


def test_slots_are_released_after_many_requests_and_rejections(start, monkeypatch):
    monkeypatch.setattr(serve, "MAX_CONNECTIONS", 2)
    port = start()
    for _ in range(8):  # sequential requests far exceed the cap
        assert get(port, "/api/state.json")[0] == 200
    a, b = connect(port), connect(port)
    time.sleep(0.3)
    try:
        for _ in range(6):  # rejected connections must not leak slots
            c = connect(port)
            assert wait_closed(c, 1.0)[0]
            c.close()
    finally:
        a.close()
        b.close()
    time.sleep(0.5)
    for _ in range(4):
        assert get(port, "/api/state.json")[0] == 200


# ---- acceptance 3

def test_big_body_survives_slow_header_and_slow_reader(start, monkeypatch):
    monkeypatch.setattr(serve, "REQUEST_TIMEOUT", 2)
    port = start(snap=lambda: board(pad=5_500_000))
    s = connect(port, rcvbuf=4096)
    try:
        s.sendall(b"GET /api/state.json HTTP/1.0\r\n")
        time.sleep(1.9)  # Host header arrives near the end of the read phase
        s.sendall(b"Host: demo-vm\r\n")
        s.sendall(b"\r\n")
        time.sleep(0.7)
        data = bytearray()
        s.settimeout(10)
        while True:
            d = s.recv(65536)
            if not d:
                break
            data += d
    finally:
        s.close()
    head, _, body = bytes(data).partition(b"\r\n\r\n")
    assert head.split()[1] == b"200"
    clen = [int(l.split(b":")[1]) for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")]
    assert clen and clen[0] >= 5_000_000
    assert len(body) == clen[0]


# ---- acceptance 4

@pytest.mark.parametrize("family,host", [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")])
def test_get_and_head_are_complete_and_stderr_is_empty(start, capfd, family, host):
    try:
        port = start(host)
    except OSError:
        pytest.skip("no IPv6 loopback")
    capfd.readouterr()
    for path in ("/", "/api/state.json", "/static/base.css", "/fragment/home"):
        st, head, body = get(port, path, family=family)
        assert st == 200
        clen = [int(l.split(b":")[1]) for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")][0]
        assert len(body) == clen > 0, path
        st, head, body = get(port, path, "HEAD", family=family)
        assert st == 200 and body == b""
        assert b"content-length" in head.lower()
    time.sleep(0.3)
    err = capfd.readouterr().err
    assert err == "", err
    assert "Traceback" not in err


def test_stderr_clean_with_http_client(start, capfd):
    port = start()
    capfd.readouterr()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/api/state.json", headers={"Host": "127.0.0.1"})
    r = c.getresponse()
    assert r.status == 200 and len(r.read()) == int(r.getheader("Content-Length"))
    c.close()
    time.sleep(0.3)
    assert capfd.readouterr().err == ""
