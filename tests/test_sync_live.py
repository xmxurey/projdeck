import json
import os
import threading

from projdeck import agent, cli, config, sync

NOW = 1_700_000_000


def make_cfg(hosts):
    return {"hosts": hosts, "ssh_timeout": 5, "scan_depth": 2, "include_plain": True,
            "suspicious_patterns": None, "auto_sync_minutes": 60}


def live_path(cache, name):
    return os.path.join(cache, "live", name + ".json")


def test_live_request_zellij_flag_only_for_local_zellij_host():
    cfg = make_cfg([])
    local_z = {"name": "a", "local": True, "mux": "zellij"}
    local_t = {"name": "b", "local": True, "mux": "tmux"}
    remote = {"name": "c", "ssh": "c", "mux": "zellij"}  # as if unresolved: still never flagged
    assert sync.live_request(cfg, local_z, ["/p"])["zellij"] is True
    for h in (local_t, remote, {"name": "d", "local": True}):
        assert "zellij" not in sync.live_request(cfg, h, ["/p"])
    req = sync.live_request(cfg, local_z, ["/p"])
    assert req["action"] == "live" and req["paths"] == ["/p"] and req["host"] == "a"


def test_sync_live_local_runs_in_process_and_writes_live_cache(fake_ssh, monkeypatch):
    calls = []

    def fake_act_live(req, memo=None):
        calls.append((req, memo))
        return {"schema": agent.SCHEMA, "host": "mac", "collected_at": NOW, "sessions": [],
                "errors": ["x"]}

    monkeypatch.setattr(sync.agent, "act_live", fake_act_live)

    def no_subprocess(*a, **k):
        raise AssertionError("subprocess used")

    monkeypatch.setattr(sync.subprocess, "run", no_subprocess)
    sync._write_json(sync._cache_path("mac"), {"schema": 1, "host": "mac", "synced_at": 5,
                                                "projects": [{"path": "/p1"}, {"path": "/p2"}]})
    memo = {}
    host = {"name": "mac", "local": True}
    r = sync.sync_live(host, make_cfg([host]), now=NOW, memo=memo)
    assert r == {"host": "mac", "ok": True, "error": None}
    assert calls[0][0]["paths"] == ["/p1", "/p2"] and calls[0][1] is memo
    c = json.load(open(live_path(fake_ssh, "mac")))
    assert c["errors"] == ["x"] and c["stale_since"] is None and c["last_error"] is None
    assert c["host"] == "mac" and c["synced_at"] == NOW
    assert sync.read_live("mac") == c and sync.load_lives(make_cfg([host])) == [c]


def test_sync_live_remote_failure_keeps_old_and_marks_stale(fake_ssh):
    host = {"name": "vm", "ssh": "unreachable"}
    cfg = make_cfg([host])
    sync._write_json(live_path(fake_ssh, "vm"), {"schema": 1, "host": "vm", "synced_at": 111,
                                                  "sessions": [{"id": "s"}]})
    r = sync.sync_live(host, cfg, now=NOW)
    assert r["ok"] is False and "connect failed" in r["error"]
    c = sync.read_live("vm")
    assert c["stale_since"] == 111 and "connect failed" in c["last_error"]
    assert c["sessions"] == [{"id": "s"}]


def test_sync_live_never_succeeded_writes_stub(fake_ssh):
    host = {"name": "vm", "ssh": "unreachable"}
    r = sync.sync_live(host, make_cfg([host]), now=NOW)
    assert r["ok"] is False
    c = sync.read_live("vm")
    assert c["sessions"] == [] and c["stale_since"] == NOW and "connect failed" in c["last_error"]
    assert c["collected_at"] == 0 and c["host"] == "vm"


def test_collect_and_live_caches_do_not_collide(fake_ssh, monkeypatch):
    monkeypatch.setattr(sync.agent, "act_live", lambda req, memo=None: {
        "schema": agent.SCHEMA, "host": req["host"], "collected_at": NOW, "sessions": []})
    hosts = [{"name": n, "local": True} for n in ("live", "s-host.live", "statusline")]
    for h in hosts:
        sync._write_json(sync._cache_path(h["name"]), {"schema": 1, "host": h["name"],
                                                        "synced_at": 1, "projects": []})
        assert sync.sync_live(h, make_cfg(hosts), now=NOW)["ok"]
    for h in hosts:
        assert "projects" in sync.read_cache(h["name"])
        assert "sessions" in sync.read_live(h["name"])
    assert sorted(os.listdir(os.path.join(fake_ssh, "live"))) == [
        "live.json", "s-host.live.json", "statusline.json"]


def test_write_json_concurrent_writers_and_reader(tmp_path):
    path = str(tmp_path / "d" / "x.json")
    a, b = {"v": "a" * 5000}, {"v": "b" * 7000}
    sync._write_json(path, a)
    stop, bad = threading.Event(), []

    def writer(d):
        for _ in range(300):
            sync._write_json(path, d)

    def reader():
        while not stop.is_set():
            try:
                with open(path) as f:
                    got = json.load(f)
                if got not in (a, b):
                    bad.append("content")
            except ValueError as e:
                bad.append(str(e))

    rt = threading.Thread(target=reader)
    ws = [threading.Thread(target=writer, args=(d,)) for d in (a, b)]
    rt.start()
    [w.start() for w in ws]
    [w.join() for w in ws]
    stop.set()
    rt.join()
    assert bad == []
    assert os.listdir(os.path.dirname(path)) == ["x.json"]


def test_sync_all_live_only_skips_collect_and_uses_cached_paths(fake_ssh, monkeypatch):
    seen = []
    monkeypatch.setattr(sync.agent, "act_live", lambda req, memo=None: (
        seen.append(req["paths"]) or {"schema": agent.SCHEMA, "sessions": []}))
    monkeypatch.setattr(sync, "run_agent", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("collect ran")))
    host = {"name": "mac", "local": True}
    sync._write_json(sync._cache_path("mac"), {"schema": 1, "host": "mac", "synced_at": 1,
                                                "projects": [{"path": "/p1"}]})
    st = sync.sync_all(make_cfg([host]), live_only=True)
    assert st == [{"host": "mac", "ok": True, "error": None}] and seen == [["/p1"]]
    assert sync.read_live("mac") is not None


def test_sync_all_default_refreshes_both_layers_with_fresh_paths(fake_ssh, monkeypatch):
    seen = []
    monkeypatch.setattr(sync.agent, "act_live", lambda req, memo=None: (
        seen.append(req["paths"]) or {"schema": agent.SCHEMA, "sessions": []}))
    monkeypatch.setattr(sync, "run_agent", lambda h, req, timeout=20: {
        "schema": agent.SCHEMA, "projects": [{"path": "/new"}]})
    host = {"name": "mac", "local": True}
    [st] = sync.sync_all(make_cfg([host]))
    assert st["ok"] and seen == [["/new"]]
    assert sync.read_cache("mac")["projects"] == [{"path": "/new"}]
    assert sync.read_live("mac")["sessions"] == []


def test_cmd_sync_live_flag(fake_ssh, monkeypatch):
    got = []
    monkeypatch.setattr(sync, "sync_all", lambda cfg, names=None, live_only=False: (
        got.append((names, live_only)) or [{"host": "h", "ok": True, "error": None}]))
    args = cli.parser().parse_args(["sync", "--live", "--quiet", "h"])
    assert cli.cmd_sync({}, args) == 0
    assert got == [(["h"], True)]
    cli.cmd_sync({}, cli.parser().parse_args(["sync", "--quiet"]))
    assert got[1] == (None, False)


def _seed_failing(monkeypatch, collect_ok, live_ok):
    def fake_run_agent(h, req, timeout=20):
        if req["action"] == "collect" and collect_ok:
            return {"schema": agent.SCHEMA, "projects": []}
        if req["action"] == "live" and live_ok:
            return {"schema": agent.SCHEMA, "sessions": []}
        raise sync.AgentError("%s boom" % req["action"])

    monkeypatch.setattr(sync, "run_agent", fake_run_agent)


def test_sync_all_error_names_the_failing_layer(fake_ssh, monkeypatch):
    host = {"name": "vm", "ssh": "vm"}
    _seed_failing(monkeypatch, collect_ok=True, live_ok=False)
    [st] = sync.sync_all(make_cfg([host]))
    assert st["ok"] is False and st["error"].startswith("live:") and "live boom" in st["error"]
    _seed_failing(monkeypatch, collect_ok=False, live_ok=True)
    [st] = sync.sync_all(make_cfg([host]))
    assert st["error"].startswith("collect:") and "live:" not in st["error"]
    _seed_failing(monkeypatch, collect_ok=False, live_ok=False)
    [st] = sync.sync_all(make_cfg([host]))
    assert "collect: collect boom" in st["error"] and "live: live boom" in st["error"]


def test_sync_all_live_only_error_not_prefixed_with_collect(fake_ssh, monkeypatch):
    _seed_failing(monkeypatch, collect_ok=True, live_ok=False)
    [st] = sync.sync_all(make_cfg([{"name": "vm", "ssh": "vm"}]), live_only=True)
    assert st["error"].startswith("live:")


def test_real_claude_is_never_started_by_default(fake_ssh):
    import shutil
    exe = shutil.which("claude")
    assert os.path.basename(os.path.dirname(exe)).startswith("fakeclaude")
