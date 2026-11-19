import http.client
import json
import os
import socket
import threading

import pytest

from projdeck import agent, board, cli, config, serve, sync

NOW = 1_800_000_000
TS = {"ip": "127.0.0.1", "dns_name": "demo-vm.tail0000.ts.net", "host_name": "demo-vm"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SECURITY = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}


def sess(sid, state, **kw):
    d = {"id": sid, "host": "s-host", "project": "/home/someuser/dev/projdeck", "name": "n-" + sid,
         "title": "", "state": state, "idle_since": NOW - 600, "question": "<script>x</script>",
         "last_reply": "", "last_human": {"ts": 0, "text": ""}, "last_input": "human",
         "subagents_running": 0, "segments": [], "attach": "zellij attach z-" + sid,
         "tasks": None, "tasks_owner": None, "errors": []}
    d.update(kw)
    return d


def make_board():
    return {"hosts": [{"name": "s-host", "live_at": NOW, "collect_at": NOW, "stale_since": None,
                       "error": None, "source": "agents"}],
            "summary": {"waiting": 1, "busy": 0, "sub": 0, "idle": 0, "open_todos": 0,
                        "todo_projects": 0, "today": {"busy": 0, "sub": 0, "wait": 0}},
            # a session carrying prompts must not leak them through the JSON API
            "waiting": [sess("a", "waiting", prompts=[{"ts": NOW, "text": "secret prompt"}])],
            "busy": [], "idle": [], "ended_today": [], "stale": [], "stale_host_sessions": [],
            "top_todos": [], "backlogs": [], "tiles": [],
            "lanes": {"start": NOW - 4 * 3600, "end": NOW, "rows": [], "more": 0}}


class Counter:
    def __init__(self, value):
        self.value, self.calls = value, 0

    def __call__(self):
        self.calls += 1
        return self.value


@pytest.fixture
def no_collect(monkeypatch):
    """Any collection path raises and is counted."""
    hits = []

    def boom(name):
        def f(*a, **k):
            hits.append(name)
            raise AssertionError("collection triggered: " + name)
        return f
    for mod, name in ((sync, "sync_live"), (sync, "sync_all"), (sync, "sync_host"),
                      (sync, "run_agent"), (sync, "load_caches"), (sync, "load_lives"),
                      (agent, "act_live"), (agent, "collect"), (board, "build")):
        monkeypatch.setattr(mod, name, boom(name))
    monkeypatch.setattr(serve.subprocess, "run", boom("subprocess.run"))
    return hits


@pytest.fixture
def running(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    started = []

    def start(snapshot_fn=None, cfg=None):
        snap = snapshot_fn or Counter(make_board())
        srv = serve.make_server(cfg or config.DEFAULTS, "127.0.0.1", 0, snap)
        t = threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True)
        t.start()
        started.append((srv, t))
        return srv.server_address[1], snap
    yield start
    for srv, t in started:
        srv.shutdown()
        srv.server_close()
        t.join(5)


def request(port, path="/", method="GET", host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request(method, path, headers={"Host": host if host is not None else "127.0.0.1:%d" % port})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        c.close()


def assert_security(headers):
    for k, v in SECURITY.items():
        assert headers.get(k) == v, k
    assert "unsafe-inline" not in headers["Content-Security-Policy"]


# ----- acceptance 1: Host whitelist and methods

def test_host_whitelist(running):
    port, _ = running()
    st, hd, _ = request(port, host="evil.example")
    assert st == 403
    assert_security(hd)
    for host in ("demo-vm.tail0000.ts.net:%d" % port, "demo-vm", "demo-vm.tail0000.ts.net",
                 "demo-vm:%d" % port, "127.0.0.1", "127.0.0.1:%d" % port, "DEMO-VM"):
        assert request(port, host=host)[0] == 200, host


def test_host_with_other_port_or_suffix_is_rejected(running):
    port, _ = running()
    for host in ("demo-vm:%d" % (port + 1), "demo-vm.evil.example", "evil.demo-vm", "",
                 "127.0.0.1.evil.example", "demo-vm:%d:%d" % (port, port)):
        assert request(port, host=host)[0] == 403, host


def test_missing_host_header_is_rejected(running):
    port, _ = running()
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        s.sendall(b"GET / HTTP/1.0\r\n\r\n")
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    finally:
        s.close()
    assert data.split(b"\r\n", 1)[0].split()[1] == b"403"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "FOO"])
def test_other_methods_are_405(running, method):
    port, snap = running()
    st, hd, _ = request(port, "/", method)
    assert st == 405
    assert hd.get("Allow") == "GET, HEAD"
    assert_security(hd)
    assert snap.calls == 0


def test_head_has_headers_and_no_body(running):
    port, _ = running()
    st, hd, body = request(port, "/api/state.json", "HEAD")
    assert st == 200 and body == b""
    assert hd["Content-Type"].startswith("application/json")
    assert int(hd["Content-Length"]) > 0
    assert_security(hd)


# ----- acceptance 2: fixed routes

ROUTES = {"/": "text/html", "/projects": "text/html", "/fragment/home": "text/html",
          "/fragment/projects": "text/html", "/api/state.json": "application/json",
          "/static/base.css": "text/css", "/static/poll.js": "text/javascript",
          "/static/app.css": "text/css"}


def test_routes_content_types_and_headers(running):
    port, _ = running()
    for path, ctype in ROUTES.items():
        st, hd, body = request(port, path)
        assert st == 200, path
        assert hd["Content-Type"] == ctype + "; charset=utf-8", path
        assert int(hd["Content-Length"]) == len(body) > 0, path
        assert_security(hd)


def test_pages_render_board(running):
    port, _ = running()
    page = request(port, "/")[2].decode()
    assert page.startswith("<!doctype html>") and 'data-poll="/fragment/home"' in page
    assert "<script>x</script>" not in page and "&lt;script&gt;x&lt;/script&gt;" in page
    assert 'data-poll="/fragment/projects"' in request(port, "/projects")[2].decode()
    frag = request(port, "/fragment/home")[2].decode()
    assert "<html" not in frag and "<main" not in frag and "n-a" in frag
    assert "<html" not in request(port, "/fragment/projects")[2].decode()


def test_static_files_are_the_shipped_copies(running):
    port, _ = running()
    root = os.path.dirname(serve.__file__)
    for path, rel in (("/static/base.css", "vendor/webkit/base.css"),
                      ("/static/poll.js", "vendor/webkit/poll.js"),
                      ("/static/app.css", "web/app.css")):
        with open(os.path.join(root, rel), "rb") as f:
            assert request(port, path)[2] == f.read()


def test_state_json_parses_and_has_no_prompts(running):
    port, _ = running()
    body = request(port, "/api/state.json")[2]
    data = json.loads(body.decode("utf-8"))
    assert data["waiting"][0]["id"] == "a" and data["waiting"][0]["question"] == "<script>x</script>"
    assert b"prompts" not in body and b"secret prompt" not in body


@pytest.mark.parametrize("path", ["/x", "/static/../serve.py", "/static/", "/static/webkit.py",
                                  "/fragment/", "/api/", "/index.html", "//x", "/projects/",
                                  "/static/%2e%2e/serve.py", "/../projdeck/serve.py"])
def test_unknown_paths_are_404(running, path):
    port, snap = running()
    st, hd, body = request(port, path)
    assert st == 404
    assert_security(hd)
    assert b"import" not in body
    assert snap.calls == 0


def test_query_string_is_ignored(running):
    port, _ = running()
    assert request(port, "/fragment/home?t=1")[0] == 200


def test_render_failure_is_500_with_headers(running, capsys):
    def broken():
        raise RuntimeError("boom")
    port, _ = running(broken)
    st, hd, body = request(port, "/")
    assert st == 500 and b"boom" not in body
    assert_security(hd)
    assert "boom" in capsys.readouterr().err


# ----- acceptance 5: request threads only call snapshot_fn

def test_requests_only_call_snapshot_fn(running, no_collect):
    port, snap = running()
    for path in ROUTES:
        assert request(port, path)[0] == 200
    request(port, "/api/state.json", "HEAD")
    assert snap.calls == 6  # 4 pages, state.json and its HEAD; statics do not touch it
    assert no_collect == []


# ----- tailscale_self / allowed_hosts / resolve_bind

class Proc:
    def __init__(self, out, code=0):
        self.stdout, self.returncode = out, code


STATUS = {"Self": {"DNSName": "demo-vm.tail0000.ts.net.", "HostName": "demo-vm",
                   "TailscaleIPs": ["fd7a:115c:a1e0::1", "100.101.102.103"]}}


def test_tailscale_self_parses_status(monkeypatch):
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        assert kw.get("timeout")
        return Proc(json.dumps(STATUS))
    monkeypatch.setattr(serve.subprocess, "run", run)
    assert serve.tailscale_self() == {"ip": "100.101.102.103", "dns_name": "demo-vm.tail0000.ts.net",
                                      "host_name": "demo-vm"}
    assert seen == [["tailscale", "status", "--json"]]


@pytest.mark.parametrize("result", [
    FileNotFoundError("tailscale"), serve.subprocess.TimeoutExpired("tailscale", 5),
    Proc("", 1), Proc("not json"), Proc("[]"), Proc(json.dumps({"Self": None})),
    Proc(json.dumps({"Self": {"TailscaleIPs": ["fd7a::1"]}})),
])
def test_tailscale_self_none_when_unavailable(monkeypatch, result):
    def run(argv, **kw):
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(serve.subprocess, "run", run)
    assert serve.tailscale_self() is None


def test_allowed_hosts():
    assert serve.allowed_hosts("100.101.102.103", 8787, dict(TS, ip="100.101.102.103")) == {
        "100.101.102.103", "100.101.102.103:8787", "demo-vm.tail0000.ts.net",
        "demo-vm.tail0000.ts.net:8787", "demo-vm", "demo-vm:8787"}
    assert serve.allowed_hosts("100.101.102.103", 8787, None) == {"100.101.102.103", "100.101.102.103:8787"}


def cfg_with(**serve_kw):
    cfg = config.load_config("/nonexistent/config.json")
    cfg["serve"] = dict(cfg["serve"], **serve_kw)
    return cfg


def test_resolve_bind_order(monkeypatch):
    calls = []
    monkeypatch.setattr(serve, "tailscale_self", lambda: calls.append(1) or dict(TS, ip="100.101.102.103"))
    assert serve.resolve_bind(cfg_with(bind="100.100.1.1"), "127.0.0.1") == "127.0.0.1"
    assert serve.resolve_bind(cfg_with(bind="100.100.1.1"), None) == "100.100.1.1"
    assert calls == []
    assert serve.resolve_bind(cfg_with(), None) == "100.101.102.103"


@pytest.mark.parametrize("cli_bind,cfg_bind,ts", [
    ("0.0.0.0", None, TS), ("192.168.1.5", None, TS), (None, "192.168.1.5", TS),
    (None, None, None), ("::", None, TS), ("localhost", None, TS), (None, "0.0.0.0", None),
    (None, None, dict(TS, ip="10.0.0.1")),
])
def test_resolve_bind_rejects(monkeypatch, cli_bind, cfg_bind, ts):
    monkeypatch.setattr(serve, "tailscale_self", lambda: ts)
    with pytest.raises(ValueError):
        serve.resolve_bind(cfg_with(bind=cfg_bind), cli_bind)


def test_make_server_refuses_wildcard(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: None)
    with pytest.raises(ValueError):
        serve.make_server(config.DEFAULTS, "0.0.0.0", 0, lambda: make_board())


# ----- acceptance 3: proj serve exit codes

@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    def setup(cfg=None):
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg or {}))
        monkeypatch.setenv("PROJ_CONFIG", str(p))
        monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    return setup


@pytest.fixture
def no_listen(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("must not listen")
    monkeypatch.setattr(serve, "ThreadingHTTPServer", refuse)


@pytest.mark.parametrize("argv,cfg,ts,why", [
    (["serve", "--bind", "0.0.0.0"], None, TS, "0.0.0.0"),
    (["serve"], {"serve": {"bind": "192.168.1.5"}}, TS, "192.168.1.5"),
    (["serve"], None, None, "tailscale"),
    (["serve", "--bind", "127.0.0.1", "--port", "0"], None, TS, "port"),
    (["serve", "--bind", "127.0.0.1", "--port", "70000"], None, TS, "port"),
])
def test_cli_serve_bad_address_exits_2(cli_env, no_listen, monkeypatch, capsys, argv, cfg, ts, why):
    cli_env(cfg)
    monkeypatch.setattr(serve, "tailscale_self", lambda: ts)
    assert cli.main(argv) == 2
    assert why in capsys.readouterr().err


def test_cli_serve_port_in_use_exits_2(cli_env, monkeypatch, capsys):
    cli_env()
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    busy = socket.socket()
    try:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        assert cli.main(["serve", "--bind", "127.0.0.1", "--port", str(port)]) == 2
    finally:
        busy.close()
    assert str(port) in capsys.readouterr().err


def test_cli_serve_listens_on_resolved_address(cli_env, monkeypatch, capsys):
    cli_env({"serve": {"bind": "127.0.0.1", "port": 8787}})
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    seen = {}
    real = serve.ThreadingHTTPServer

    class Probe(real):
        def serve_forever(self, poll_interval=0.5):
            seen["addr"] = self.server_address
            raise KeyboardInterrupt
    monkeypatch.setattr(serve, "ThreadingHTTPServer", Probe)
    assert cli.main(["serve", "--port", "0"]) == 2  # --port 0 rejected before listening
    assert "addr" not in seen
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    assert cli.main(["serve", "--port", str(free)]) == 0
    assert seen["addr"] == ("127.0.0.1", free)


# ----- acceptance 4: config

def write_cfg(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_config_serve_defaults(tmp_path):
    cfg = config.load_config(str(tmp_path / "missing.json"))
    assert cfg["serve"] == {"bind": None, "port": 8787, "push_statusline": True}
    assert cfg["dashboard_url"] is None
    cfg = config.load_config(write_cfg(tmp_path, {"ssh_timeout": 5}))
    assert cfg["serve"] == {"bind": None, "port": 8787, "push_statusline": True}


def test_config_serve_shallow_merge(tmp_path):
    cfg = config.load_config(write_cfg(tmp_path, {"serve": {"port": 9000},
                                                  "dashboard_url": "http://100.101.102.103:8787/"}))
    assert cfg["serve"] == {"bind": None, "port": 9000, "push_statusline": True}
    assert cfg["dashboard_url"] == "http://100.101.102.103:8787/"
    cfg = config.load_config(write_cfg(tmp_path, {"serve": {"bind": "100.101.102.103",
                                                            "push_statusline": False}}))
    assert cfg["serve"] == {"bind": "100.101.102.103", "port": 8787, "push_statusline": False}
    assert config.DEFAULTS["serve"]["port"] == 8787  # defaults are not mutated


@pytest.mark.parametrize("serve_cfg", [
    {"port": 0}, {"port": 70000}, {"port": "8787"}, {"port": True}, {"port": 80.5},
    {"bind": "192.168.1.5"}, {"bind": "0.0.0.0"}, {"bind": 1}, {"push_statusline": "no"}, [],
])
def test_config_serve_rejects(tmp_path, serve_cfg):
    with pytest.raises(ValueError):
        config.load_config(write_cfg(tmp_path, {"serve": serve_cfg}))


def test_config_dashboard_url_must_be_string(tmp_path):
    with pytest.raises(ValueError):
        config.load_config(write_cfg(tmp_path, {"dashboard_url": 5}))


# ----- review round 1: whitelist without tailscale info is announced (I-1)

def test_no_tailscale_whitelist_is_ip_only_and_says_so(running, monkeypatch, capsys):
    monkeypatch.setattr(serve, "tailscale_self", lambda: None)
    port, _ = running()
    assert request(port, host="demo-vm")[0] == 403
    assert request(port, host="demo-vm.tail0000.ts.net:%d" % port)[0] == 403
    assert request(port, host="127.0.0.1:%d" % port)[0] == 200
    err = capsys.readouterr().err
    assert "127.0.0.1" in err and "403" in err and "tailscale" in err
    assert len(err.strip().splitlines()) == 1


def test_missing_dns_name_is_announced(running, monkeypatch, capsys):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS, dns_name=None))
    port, _ = running()
    assert request(port, host="demo-vm")[0] == 200
    assert request(port, host="demo-vm.tail0000.ts.net")[0] == 403
    err = capsys.readouterr().err
    assert "MagicDNS" in err and "403" in err and "demo-vm" in err


def test_full_tailscale_info_prints_nothing(running, capsys):
    running()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("argv,cfg", [
    (["serve"], None),
    (["serve", "--bind", "127.0.0.1"], None),
    (["serve"], {"serve": {"bind": "127.0.0.1"}}),
])
def test_cli_serve_probes_tailscale_once_and_whitelists_its_names(cli_env, monkeypatch, argv, cfg):
    cli_env(cfg)
    calls = []
    monkeypatch.setattr(serve, "tailscale_self", lambda: calls.append(1) or dict(TS))
    seen = {}
    real = serve.ThreadingHTTPServer

    class Probe(real):
        def serve_forever(self, poll_interval=0.5):
            port = self.server_address[1]
            t = threading.Thread(target=lambda: seen.update(
                fin=request(port, host="demo-vm")[0],
                dns=request(port, host="demo-vm.tail0000.ts.net:%d" % port)[0]))
            t.start()
            self.handle_request()
            self.handle_request()
            t.join(5)
            raise KeyboardInterrupt
    monkeypatch.setattr(serve, "ThreadingHTTPServer", Probe)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    assert cli.main(argv + ["--port", str(free)]) == 0
    assert calls == [1]
    assert seen == {"fin": 200, "dns": 200}


# ----- review round 1: config errors name the file, the key and the valid values (I-2)

@pytest.mark.parametrize("bind", ["192.168.1.5", "garbage", 1, 2130706433, "0.0.0.0"])
def test_config_bad_bind_message(tmp_path, bind):
    path = write_cfg(tmp_path, {"serve": {"bind": bind}})
    with pytest.raises(ValueError) as e:
        config.load_config(path)
    msg = str(e.value)
    for part in (path, '"serve.bind"', "null", "loopback", "100.64.0.0/10", repr(bind)):
        assert part in msg, part


@pytest.mark.parametrize("port", [0, 70000, "8787", True])
def test_config_bad_port_message(tmp_path, port):
    path = write_cfg(tmp_path, {"serve": {"port": port}})
    with pytest.raises(ValueError) as e:
        config.load_config(path)
    msg = str(e.value)
    for part in (path, '"serve.port"', "1-65535", repr(port)):
        assert part in msg, part


def test_config_bad_push_statusline_message(tmp_path):
    path = write_cfg(tmp_path, {"serve": {"push_statusline": "no"}})
    with pytest.raises(ValueError) as e:
        config.load_config(path)
    assert path in str(e.value) and '"serve.push_statusline"' in str(e.value)


def test_cli_bad_port_names_the_flag(cli_env, no_listen, monkeypatch, capsys):
    cli_env()
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    assert cli.main(["serve", "--bind", "127.0.0.1", "--port", "0"]) == 2
    err = capsys.readouterr().err
    assert "--port" in err and "1-65535" in err
