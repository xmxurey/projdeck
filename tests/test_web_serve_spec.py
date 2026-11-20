"""Independent tests for T23: `proj serve` HTTP layer, bind rules, config keys (design 3.4, 3.8)."""
import http.client
import json
import socket
import socketserver
import threading

import pytest

from projdeck import cli, config, serve, sync, agent

TS = {"ip": "127.0.0.1", "dns_name": "demo-vm.tail0000.ts.net", "host_name": "demo-vm"}
NOW = 1_800_000_000
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SEC = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
       "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}
ROUTES = {
    "/": "text/html", "/projects": "text/html", "/fragment/home": "text/html",
    "/fragment/projects": "text/html", "/api/state.json": "application/json",
    "/static/base.css": "text/css", "/static/poll.js": "javascript", "/static/app.css": "text/css",
}


def board():
    return {"hosts": [], "summary": {}, "waiting": [], "busy": [], "idle": [], "ended_today": [],
            "stale": [], "stale_host_sessions": [],
            "lanes": {"start": NOW - 3 * 3600, "end": NOW, "rows": [], "more": 0},
            "top_todos": [], "backlogs": [], "tiles": []}


class Srv:
    def __init__(self, snapshot_fn=None):
        self.calls = []

        def snap():
            self.calls.append(threading.current_thread().name)
            return board()
        self.server = serve.make_server(config.load_config(), "127.0.0.1", 0, snapshot_fn or snap)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True)
        self.thread.start()

    def req(self, method, path, host="default", raw_host=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            if host == "default":
                host = "127.0.0.1:%d" % self.port
            if host is not None:
                c.putheader("Host", host)
            c.putheader("Content-Length", "0") if method in ("POST", "PUT", "DELETE") else None
            c.endheaders()
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    s = Srv()
    yield s
    s.close()


def hdr(headers, name):
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


# ---- acceptance 1: Host and method

def test_evil_host_is_403(srv):
    assert srv.req("GET", "/", host="evil.example")[0] == 403


@pytest.mark.parametrize("host", ["demo-vm.tail0000.ts.net:{p}", "demo-vm.tail0000.ts.net",
                                  "demo-vm", "demo-vm:{p}", "127.0.0.1", "127.0.0.1:{p}"])
def test_whitelisted_hosts_200(srv, host):
    assert srv.req("GET", "/", host=host.format(p=srv.port))[0] == 200


def test_wrong_port_in_host_is_403(srv):
    assert srv.req("GET", "/", host="demo-vm:%d" % (srv.port + 1))[0] == 403


@pytest.mark.parametrize("host", ["", "evil.example:%PORT%", "demo-vm.evil.example", "evildemo-vm",
                                  "demo-vm@evil.example", "demo-vm, evil.example", "[::1]x", "a" * 70000,
                                  "demo-vm\x00", "localhost"])
def test_malformed_or_foreign_host_never_200(srv, host):
    host = host.replace("%PORT%", str(srv.port))
    try:
        status = srv.req("GET", "/", host=host)[0]
    except (http.client.HTTPException, ConnectionError, OSError):
        return  # connection refused outright is also a rejection
    assert status in (400, 403, 431)


def test_missing_host_header_not_200(srv):
    try:
        status = srv.req("GET", "/", host=None)[0]
    except (http.client.HTTPException, ConnectionError, OSError):
        return
    assert status in (400, 403)


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_other_methods_405_with_good_host(srv, method):
    assert srv.req(method, "/")[0] == 405


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_other_methods_405_even_with_bad_host(srv, method):
    # ruling: method check precedes the Host check
    assert srv.req(method, "/", host="evil.example")[0] == 405


def test_head_ok_without_body_and_with_headers(srv):
    st, h, body = srv.req("HEAD", "/")
    assert st == 200 and body == b""
    for k, v in SEC.items():
        assert hdr(h, k) == v


def test_head_bad_host_403(srv):
    assert srv.req("HEAD", "/", host="evil.example")[0] == 403


# ---- acceptance 2: routes, content types, headers

@pytest.mark.parametrize("path,ctype", sorted(ROUTES.items()))
def test_route_content_type_and_headers(srv, path, ctype):
    st, h, body = srv.req("GET", path)
    assert st == 200
    assert ctype in hdr(h, "Content-Type")
    assert len(body) > 0
    for k, v in SEC.items():
        assert hdr(h, k) == v
    assert "unsafe-inline" not in hdr(h, "Content-Security-Policy")


def test_state_json_parses_and_has_no_prompts(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    b = board()
    b["summary"] = {"waiting": 2, "busy": 1, "sub": 0, "idle": 3, "unknown": 0,
                    "open_todos": 4, "todo_projects": 2}
    b["waiting"] = [{"id": "w1"}, {"id": "w2"}]
    b["busy"] = [{"id": "b1"}]
    b["idle"] = [{"id": "i1"}, {"id": "i2"}, {"id": "i3"}]
    s = Srv(lambda: b)
    try:
        st, h, body = s.req("GET", "/api/state.json")
    finally:
        s.close()
    assert st == 200
    d = json.loads(body.decode("utf-8"))
    assert "prompts" not in body.decode("utf-8")
    assert d["summary"] == b["summary"]
    assert [len(d[k]) for k in ("waiting", "busy", "idle")] == [2, 1, 3]
    for k in ("hosts", "summary", "waiting", "busy", "idle"):
        assert k in d


def test_pages_have_no_inline_style_script_or_external_urls(srv):
    for p in ("/", "/projects"):
        html = srv.req("GET", p)[2].decode("utf-8")
        assert 'style="' not in html
        assert "http://" not in html and "https://" not in html
        assert "<script>" not in html.replace("<script >", "<script>")


@pytest.mark.parametrize("path", ["/static/../serve.py", "/static/%2e%2e/serve.py", "/static/%2e%2e%2fserve.py",
                                  "/static/..%2fprojdeck/serve.py", "/x", "/static/", "/static/nope.css",
                                  "/static/base.css/", "/api/state.json/x", "/fragment/", "/projects/",
                                  "//etc/passwd", "/static/../../../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd",
                                  "/index.html", "/serve.py"])
def test_unknown_and_traversal_paths_404_with_headers(srv, path):
    st, h, body = srv.req("GET", path)
    assert st == 404
    assert b"import " not in body and b"root:" not in body
    for k, v in SEC.items():
        assert hdr(h, k) == v


def test_query_string_does_not_change_routing(srv):
    plain = srv.req("GET", "/api/state.json")
    assert plain[0] == 200
    with_query = srv.req("GET", "/api/state.json?x=1")
    assert with_query[0] == 200
    assert with_query[2] == plain[2]
    css = srv.req("GET", "/static/base.css")[2]
    assert srv.req("GET", "/static/base.css?v=2")[0] == 200
    assert srv.req("GET", "/static/base.css?v=2")[2] == css
    assert srv.req("GET", "/x?a=/")[0] == 404


@pytest.mark.parametrize("kw", [dict(host="evil.example"), dict()])
def test_error_responses_carry_security_headers(srv, kw):
    for method in ("GET", "POST"):
        st, h, _ = srv.req(method, "/", **kw)
        for k, v in SEC.items():
            assert hdr(h, k) == v, (st, k)


# ---- acceptance 5: handler only reads the snapshot

def test_request_only_calls_snapshot_fn_and_no_collection(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    forbidden = []

    def boom(name):
        def f(*a, **k):
            forbidden.append(name)
            raise AssertionError(name)
        return f
    for mod, names in ((sync, ["sync_all", "sync_live", "sync_host", "run_agent", "load_caches", "load_lives"]),
                       (agent, ["collect", "act_live", "live_agents"])):
        for n in names:
            monkeypatch.setattr(mod, n, boom(n))
    s = Srv()
    try:
        for p in sorted(ROUTES):
            assert s.req("GET", p)[0] == 200
        s.req("HEAD", "/")
    finally:
        s.close()
    assert forbidden == []
    assert s.calls  # snapshot_fn was consulted


def test_snapshot_fn_result_is_what_is_served(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    b = board()
    b["summary"] = {"marker": "zz-unique-marker"}
    s = Srv(lambda: b)
    try:
        body = s.req("GET", "/api/state.json")[2].decode("utf-8")
    finally:
        s.close()
    assert "zz-unique-marker" in body


# ---- make_server binding

def test_make_server_binds_loopback_only(srv):
    assert srv.server.server_address[0] == "127.0.0.1"
    assert srv.port > 0


def test_make_server_rejects_non_tailnet_bind(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    for bad in ("0.0.0.0", "192.168.1.5", "8.8.8.8", ""):
        with pytest.raises((ValueError, OSError)):
            serve.make_server(config.load_config(), bad, 0, board).server_close()


# ---- allowed_hosts / resolve_bind

def test_allowed_hosts_with_tailscale():
    hs = serve.allowed_hosts("100.101.102.103", 8787, TS)
    assert {"100.101.102.103", "demo-vm.tail0000.ts.net", "demo-vm"} <= set(hs)
    assert "evil.example" not in hs


def test_allowed_hosts_strips_trailing_dot():
    ts = dict(TS, dns_name="demo-vm.tail0000.ts.net.")
    hs = serve.allowed_hosts("127.0.0.1", 1, ts)
    assert "demo-vm.tail0000.ts.net" in hs
    assert "demo-vm.tail0000.ts.net." not in hs


def test_allowed_hosts_without_tailscale_only_ip():
    hs = serve.allowed_hosts("127.0.0.1", 8787, None)
    assert "127.0.0.1" in hs
    assert not any("demo-vm" in x for x in hs)


def test_resolve_bind_priority_and_validation(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: {"ip": "100.101.102.103", "dns_name": "a.ts.net",
                                                         "host_name": "a"})
    cfg = config.load_config()
    assert serve.resolve_bind(cfg, None) == "100.101.102.103"
    cfg["serve"]["bind"] = "127.0.0.1"
    assert serve.resolve_bind(cfg, None) == "127.0.0.1"
    assert serve.resolve_bind(cfg, "100.64.0.1") == "100.64.0.1"
    for bad in ("0.0.0.0", "192.168.1.5", "100.128.0.1", "::"):
        with pytest.raises(ValueError):
            serve.resolve_bind(cfg, bad)


def test_resolve_bind_nothing_available_raises(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: None)
    with pytest.raises(ValueError):
        serve.resolve_bind(config.load_config(), None)


def test_resolve_bind_bad_config_bind_raises(monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    cfg = config.load_config()
    cfg["serve"]["bind"] = "192.168.1.5"
    with pytest.raises(ValueError):
        serve.resolve_bind(cfg, None)


# ---- acceptance 3: proj serve command

@pytest.fixture
def no_listen(monkeypatch):
    def refuse(self):
        raise AssertionError("must not bind a socket")
    monkeypatch.setattr(socketserver.TCPServer, "server_bind", refuse)


def write_cfg(tmp_path, monkeypatch, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    monkeypatch.setenv("PROJ_CONFIG", str(p))


def test_cli_bind_all_interfaces_exit_2(no_listen, monkeypatch, capsys):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    assert cli.main(["serve", "--bind", "0.0.0.0", "--port", "0"]) == 2
    assert capsys.readouterr().err.strip() != ""


def test_cli_config_bind_lan_exit_2_names_key(no_listen, monkeypatch, tmp_path, capsys):
    write_cfg(tmp_path, monkeypatch, {"serve": {"bind": "192.168.1.5"}})
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "bind" in err and "192.168.1.5" in err


def test_cli_no_bind_no_tailscale_exit_2(no_listen, monkeypatch, capsys):
    monkeypatch.setattr(serve, "tailscale_self", lambda: None)
    assert cli.main(["serve"]) == 2
    assert capsys.readouterr().err.strip() != ""


def test_cli_port_in_use_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        assert cli.main(["serve", "--bind", "127.0.0.1", "--port", str(s.getsockname()[1])]) == 2
    finally:
        s.close()
    assert capsys.readouterr().err.strip() != ""


def test_cli_bad_port_exit_2(no_listen, monkeypatch):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    assert cli.main(["serve", "--bind", "127.0.0.1", "--port", "70000"]) == 2


# ---- acceptance 4: config

def test_config_defaults():
    cfg = config.load_config()
    assert cfg["serve"] == {"bind": None, "port": 8787, "push_statusline": True}
    assert cfg["dashboard_url"] is None


@pytest.mark.parametrize("port", [0, 70000, -1, 65536, "8787", 1.5, None])
def test_config_bad_port_raises(tmp_path, port):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"serve": {"port": port}}))
    with pytest.raises(ValueError):
        config.load_config(str(p))


@pytest.mark.parametrize("port", [1, 8787, 65535])
def test_config_good_port_accepted(tmp_path, port):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"serve": {"port": port}}))
    cfg = config.load_config(str(p))
    assert cfg["serve"]["port"] == port
    assert cfg["serve"]["push_statusline"] is True and cfg["serve"]["bind"] is None


def test_config_partial_serve_is_shallow_merged(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"serve": {"push_statusline": False}}))
    assert config.load_config(str(p))["serve"] == {"bind": None, "port": 8787, "push_statusline": False}


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.5", "garbage", "10.0.0.1"])
def test_config_bad_bind_raises_naming_key(tmp_path, bind):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"serve": {"bind": bind}}))
    with pytest.raises(ValueError) as e:
        config.load_config(str(p))
    assert "bind" in str(e.value)


@pytest.mark.parametrize("bind", ["127.0.0.1", "100.101.102.103", "100.64.0.0", "100.127.255.255"])
def test_config_good_bind_accepted(tmp_path, bind):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"serve": {"bind": bind}}))
    assert config.load_config(str(p))["serve"]["bind"] == bind


def test_config_dashboard_url_passthrough(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"dashboard_url": "http://100.101.102.103:8787"}))
    assert config.load_config(str(p))["dashboard_url"] == "http://100.101.102.103:8787"


def test_config_defaults_not_mutated_between_loads(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"serve": {"port": 9000}}))
    config.load_config(str(p))
    assert config.load_config()["serve"]["port"] == 8787


# ---- round 2: error texts, single tailscale_self call, whitelist hint

BAD_BINDS = ["192.168.1.5", "garbage", 2130706433, 127, True, ["127.0.0.1"]]


@pytest.mark.parametrize("bind", BAD_BINDS)
def test_r2_bad_bind_load_config_message(tmp_path, bind):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"serve": {"bind": bind}}))
    with pytest.raises(ValueError) as e:
        config.load_config(str(p))
    msg = str(e.value)
    assert str(p) in msg and "serve.bind" in msg and "100.64.0.0/10" in msg


@pytest.mark.parametrize("bind", BAD_BINDS)
def test_r2_bad_bind_cli_exit_2_message_no_listen(no_listen, monkeypatch, tmp_path, capsys, bind):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"serve": {"bind": bind}}))
    monkeypatch.setenv("PROJ_CONFIG", str(p))
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert str(p) in err and "serve.bind" in err and "100.64.0.0/10" in err


@pytest.mark.parametrize("bind", ["127.0.0.1", "100.101.102.103", None])
def test_r2_good_bind_no_error(tmp_path, bind):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"serve": {"bind": bind}}))
    assert config.load_config(str(p))["serve"]["bind"] == bind


@pytest.mark.parametrize("port", [0, 70000, "80", True])
def test_r2_bad_port_message(tmp_path, port):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"serve": {"port": port}}))
    with pytest.raises(ValueError) as e:
        config.load_config(str(p))
    assert "serve.port" in str(e.value)


def test_r2_cli_bad_port_message_mentions_flag(no_listen, monkeypatch, capsys):
    monkeypatch.setattr(serve, "tailscale_self", lambda: dict(TS))
    assert cli.main(["serve", "--bind", "127.0.0.1", "--port", "70000"]) == 2
    assert "--port" in capsys.readouterr().err


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run_cli_serve(monkeypatch, ts_fn, port):
    """Run `proj serve --bind 127.0.0.1 --port N` in a thread; return (rc_box, stop)."""
    holder = {}
    real = serve.make_server

    def wrap(*a, **k):
        holder["srv"] = real(*a, **k)
        return holder["srv"]
    monkeypatch.setattr(serve, "make_server", wrap)
    monkeypatch.setattr(serve, "tailscale_self", ts_fn)
    rc = {}
    t = threading.Thread(target=lambda: rc.setdefault("rc", cli.main(
        ["serve", "--bind", "127.0.0.1", "--port", str(port)])), daemon=True)
    t.start()
    import time
    for _ in range(100):
        if "srv" in holder or "rc" in rc:
            break
        time.sleep(0.05)
    assert "srv" in holder, rc

    def get(host):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            c.putrequest("GET", "/", skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", host)
            c.endheaders()
            r = c.getresponse()
            r.read()
            return r.status
        finally:
            c.close()

    def stop():
        holder["srv"].shutdown()
        holder["srv"].server_close()
        t.join(10)
    return get, stop


def test_r2_tailscale_self_called_once_per_serve(monkeypatch, capsys):
    calls = []

    def ts():
        calls.append(1)
        return dict(TS)
    port = free_port()
    get, stop = run_cli_serve(monkeypatch, ts, port)
    try:
        assert get("demo-vm") == 200
    finally:
        stop()
    assert len(calls) == 1


def test_r2_no_tailscale_info_fin_vm_403_and_hint(monkeypatch, capsys):
    port = free_port()
    get, stop = run_cli_serve(monkeypatch, lambda: None, port)
    try:
        assert get("demo-vm") == 403
        assert get("127.0.0.1:%d" % port) == 200
    finally:
        stop()
    err = capsys.readouterr().err
    assert err.strip() != ""
    assert "127.0.0.1" in err


def test_r2_full_tailscale_info_no_hint(monkeypatch, capsys):
    port = free_port()
    get, stop = run_cli_serve(monkeypatch, lambda: dict(TS), port)
    try:
        assert get("demo-vm") == 200
        assert get("demo-vm.tail0000.ts.net") == 200
    finally:
        stop()
    lines = [x for x in capsys.readouterr().err.splitlines() if x.strip()]
    # only the startup banner (the listening URL) is allowed; no whitelist hint
    assert len(lines) <= 1 and all("403" not in x and "whitelist" not in x.lower() for x in lines)


def test_r2_partial_tailscale_info_hints(monkeypatch, capsys):
    port = free_port()
    get, stop = run_cli_serve(monkeypatch, lambda: {"ip": "127.0.0.1", "dns_name": None, "host_name": "demo-vm"}, port)
    try:
        assert get("demo-vm") == 200
        assert get("demo-vm.tail0000.ts.net") == 403
    finally:
        stop()
    assert capsys.readouterr().err.strip() != ""
