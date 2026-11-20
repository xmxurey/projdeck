"""serve: a connection has REQUEST_TIMEOUT in total to send its request head, at most
MAX_CONNECTIONS are handled at once, and writing a response is not cut short by what is
left of the read deadline."""
import http.client
import socket
import threading
import time

import pytest

from projdeck import config, serve

TS = {"ip": "127.0.0.1", "dns_name": "demo-vm.tail0000.ts.net", "host_name": "demo-vm"}


@pytest.fixture
def start(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    started = []

    def go():
        srv = serve.make_server(config.DEFAULTS, "127.0.0.1", 0, lambda: {})
        t = threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True)
        t.start()
        started.append((srv, t))
        return srv.server_address[1]
    yield go
    for srv, t in started:
        srv.shutdown()
        srv.server_close()
        t.join(5)


def host(port):
    return "Host: 127.0.0.1:%d\r\n" % port


def closed_within(sock, seconds):
    """True when the peer closes (EOF or reset) within seconds."""
    sock.settimeout(seconds)
    try:
        return sock.recv(1) == b""
    except socket.timeout:
        return False
    except ConnectionError:
        return True


def get(port):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request("GET", "/static/base.css", headers={"Host": "127.0.0.1:%d" % port})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def test_dribbled_request_head_is_cut_off_after_the_total_deadline(monkeypatch, start):
    monkeypatch.setattr(serve, "REQUEST_TIMEOUT", 1)
    port = start()
    s = socket.create_connection(("127.0.0.1", port))
    t0 = time.monotonic()
    try:
        s.sendall(b"GET / HTTP/1.0\r\n")
        while time.monotonic() - t0 < 3:
            try:
                s.sendall(b"X-Slow: 1\r\n")
            except OSError:
                break
            s.settimeout(0.4)
            try:
                if s.recv(1) == b"":
                    break
            except socket.timeout:
                continue
            except ConnectionError:
                break
    finally:
        s.close()
    assert time.monotonic() - t0 < 2


def test_connections_over_the_limit_are_closed_and_slots_come_back(monkeypatch, start):
    monkeypatch.setattr(serve, "MAX_CONNECTIONS", 2)
    port = start()
    idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]
    time.sleep(0.3)  # let the server accept both
    third = socket.create_connection(("127.0.0.1", port))
    try:
        assert closed_within(third, 1)
        idle[0].close()
        deadline = time.monotonic() + 5
        while True:
            try:
                status, _ = get(port)
                if status == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            assert time.monotonic() < deadline, "the freed slot never came back"
            time.sleep(0.05)
    finally:
        third.close()
        idle[1].close()


def test_slot_is_released_when_the_handler_thread_cannot_start(monkeypatch, start):
    monkeypatch.setattr(serve, "MAX_CONNECTIONS", 1)
    port = start()
    real = threading.Thread.start
    fail = [True]

    def start_thread(self):
        if fail[0] and self.name.startswith("Thread-") and "process_request_thread" in repr(
                self._target):
            fail[0] = False
            raise RuntimeError("can't start new thread")
        return real(self)
    monkeypatch.setattr(threading.Thread, "start", start_thread)
    s = socket.create_connection(("127.0.0.1", port))
    assert closed_within(s, 2)
    s.close()
    assert fail == [False]
    assert get(port)[0] == 200  # the one slot was given back


def test_large_response_is_not_cut_by_the_rest_of_the_read_deadline(monkeypatch, start):
    monkeypatch.setattr(serve, "REQUEST_TIMEOUT", 2)
    body = "x" * (5 * 1024 * 1024)
    monkeypatch.setattr(serve.web, "render", lambda *a, **k: body)
    port = start()
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.connect(("127.0.0.1", port))
    try:
        s.sendall(b"GET / HTTP/1.0\r\n")
        time.sleep(1.9)  # most of the read deadline is gone ...
        s.sendall(host(port).encode())
        time.sleep(0.05)  # ... when the server starts its last recv of the head
        s.sendall(b"\r\n")
        time.sleep(0.7)  # a slow reader: longer than what is left of the deadline
        s.settimeout(10)
        data = bytearray()
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    head, _, payload = data.partition(b"\r\n\r\n")
    length = int([ln for ln in head.split(b"\r\n")
                  if ln.lower().startswith(b"content-length:")][0].split(b":")[1])
    assert length == len(body)
    assert len(payload) == length


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_ordinary_request_leaves_stderr_empty(start, capfd, method):
    port = start()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, "/static/base.css", headers={"Host": "127.0.0.1:%d" % port})
    r = c.getresponse()
    body = r.read()
    c.close()
    assert r.status == 200
    assert (body == b"") == (method == "HEAD")
    time.sleep(0.2)  # the handler thread finishes after the client has the reply
    assert capfd.readouterr().err == ""


def test_ipv6_server_has_the_connection_limit_too(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    monkeypatch.setattr(serve, "MAX_CONNECTIONS", 3)
    try:
        srv = serve.make_server(config.DEFAULTS, "::1", 0, lambda: {})
    except OSError:
        pytest.skip("no IPv6 loopback here")
    try:
        assert srv.address_family == socket.AF_INET6
        assert [srv._slots.acquire(blocking=False) for _ in range(4)] == [True] * 3 + [False]
    finally:
        srv.server_close()
