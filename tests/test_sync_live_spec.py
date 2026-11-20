"""Independent tests for T11: the live layer of sync (spec: design 3.2, brief T11, D15)."""
import json
import os
import threading

import pytest

from projdeck import agent, cli, config, sync

NOW = 1_800_000_000


def make_cfg(tmp_path, hosts):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"hosts": hosts}))
    return config.load_config(str(p)), str(p)


def live_path(name):
    return os.path.join(sync.cache_dir(), "live", name + ".json")


def collect_doc(host, paths):
    return {"schema": 1, "host": host, "collected_at": NOW, "home": "/h", "tags": [],
            "projects": [{"path": p} for p in paths]}


def live_doc(host, marker="m"):
    return {"schema": 1, "host": host, "collected_at": NOW, "source": "none", "since": NOW - 86400,
            "sessions": [{"id": marker}], "projects": {}, "zellij_exited": None, "errors": []}


class FakeRemote:
    """Replaces sync.run_agent for remote hosts; records every request."""
    def __init__(self, monkeypatch, paths=("/r/a", "/r/b"), fail_live=False, fail_collect=False,
                 live_result=None):
        self.calls = []
        self.paths, self.fail_live, self.fail_collect = list(paths), fail_live, fail_collect
        self.live_result = live_result
        monkeypatch.setattr(sync, "run_agent", self)

    def __call__(self, host, req, *a, **k):
        self.calls.append((host["name"], req))
        if req["action"] == "collect":
            if self.fail_collect:
                raise sync.AgentError("collect boom")
            return collect_doc(host["name"], self.paths)
        if req["action"] == "live":
            if self.fail_live:
                raise sync.AgentError("ssh: connect failed")
            return self.live_result if self.live_result is not None else live_doc(host["name"])
        raise AssertionError("unexpected action %r" % req["action"])

    def actions(self, host=None):
        return [r["action"] for h, r in self.calls if host in (None, h)]


class ActSpy:
    def __init__(self, monkeypatch, result=None):
        self.calls = []
        self.result = result
        monkeypatch.setattr(agent, "act_live", self)

    def __call__(self, req, memo=None):
        self.calls.append((req, memo))
        return self.result if self.result is not None else live_doc(req.get("host", "mac"))


# ---- 1. local host: in-process, no subprocess, written under live/ ----

def test_local_sync_live_calls_act_live_in_process(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "mac", "local": True}])
    spy = ActSpy(monkeypatch, live_doc("mac", "L"))

    def no_proc(*a, **k):
        raise AssertionError("subprocess/ssh used for a local host")
    monkeypatch.setattr(sync, "run_agent", no_proc)
    monkeypatch.setattr("subprocess.run", no_proc)
    monkeypatch.setattr("subprocess.Popen", no_proc)
    memo = {}
    st = sync.sync_live(cfg["hosts"][0], cfg, now=NOW, memo=memo)
    assert st["host"] == "mac" and st["ok"] is True and not st["error"]
    assert len(spy.calls) == 1
    req, got_memo = spy.calls[0]
    assert req["action"] == "live" and req["host"] == "mac"
    assert got_memo is memo
    with open(live_path("mac")) as f:
        assert json.load(f)["sessions"] == [{"id": "L"}]
    assert sync.read_live("mac")["sessions"] == [{"id": "L"}]
    assert not os.path.exists(os.path.join(sync.cache_dir(), "mac.json"))


def test_read_live_missing_is_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    assert sync.read_live("nobody") is None


@pytest.mark.parametrize("host,expect", [
    ({"name": "m", "local": True, "mux": "zellij"}, True),
    ({"name": "m", "local": True, "mux": "tmux"}, False),
    ({"name": "m", "local": True, "mux": False}, False),
    ({"name": "m", "local": True, "tmux": False}, False),
    ({"name": "m", "ssh": "m", "mux": "zellij"}, False),
    ({"name": "m", "ssh": "m", "mux": "tmux"}, False),
    ({"name": "m", "ssh": "m"}, False),
])
def test_live_request_zellij_flag_only_local_zellij(tmp_path, host, expect):
    cfg, _ = make_cfg(tmp_path, [{"name": "x", "local": True}])
    req = sync.live_request(cfg, host, ["/p1", "/p2"])
    assert req["action"] == "live" and req["host"] == "m" and req["paths"] == ["/p1", "/p2"]
    if expect:
        if "zellij" not in req or req["zellij"] is not True:
            pytest.fail("zellij flag missing: %r" % (req,))
    else:
        assert "zellij" not in req


def test_local_zellij_flag_reaches_act_live(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "mac", "local": True, "mux": "zellij"}])
    spy = ActSpy(monkeypatch)
    sync.sync_live(cfg["hosts"][0], cfg, now=NOW)
    assert spy.calls[0][0].get("zellij") is True


# ---- 2. failures keep old data / write stub ----

def test_remote_failure_keeps_old_live_and_marks_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    FakeRemote(monkeypatch)
    assert sync.sync_live(cfg["hosts"][0], cfg, now=NOW)["ok"] is True
    FakeRemote(monkeypatch, fail_live=True)
    st = sync.sync_live(cfg["hosts"][0], cfg, now=NOW + 500)
    assert st["host"] == "vm" and st["ok"] is False and "connect failed" in st["error"]
    d = sync.read_live("vm")
    assert d["sessions"] == [{"id": "m"}]
    assert d["stale_since"] and "connect failed" in d["last_error"]


def test_remote_never_succeeded_writes_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    FakeRemote(monkeypatch, fail_live=True)
    st = sync.sync_live(cfg["hosts"][0], cfg, now=NOW)
    assert st["ok"] is False
    d = sync.read_live("vm")
    assert d is not None and not d.get("sessions")
    assert d["stale_since"] and d["last_error"]


def test_real_ssh_failure_via_unreachable_host(tmp_path, fake_ssh):
    cfg, _ = make_cfg(tmp_path, [{"name": "vm", "ssh": "unreachable"}])
    st = sync.sync_live(cfg["hosts"][0], cfg, now=NOW)
    assert st["ok"] is False and "connect failed" in st["error"]
    assert sync.read_live("vm")["last_error"]


def test_local_act_live_exception_is_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "mac", "local": True}])
    ActSpy(monkeypatch, live_doc("mac", "old"))
    sync.sync_live(cfg["hosts"][0], cfg, now=NOW)

    def boom(req, memo=None):
        raise RuntimeError("kaput")
    monkeypatch.setattr(agent, "act_live", boom)
    st = sync.sync_live(cfg["hosts"][0], cfg, now=NOW + 5)
    assert st["ok"] is False and "kaput" in st["error"]
    d = sync.read_live("mac")
    assert d["sessions"] == [{"id": "old"}] and d["stale_since"] and "kaput" in d["last_error"]


def test_live_output_without_schema_is_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    FakeRemote(monkeypatch)
    sync.sync_live(cfg["hosts"][0], cfg, now=NOW)
    FakeRemote(monkeypatch, live_result={"host": "vm", "sessions": [{"id": "bad"}]})
    st = sync.sync_live(cfg["hosts"][0], cfg, now=NOW + 1)
    assert st["ok"] is False
    assert sync.read_live("vm")["sessions"] == [{"id": "m"}]


def test_success_after_failure_clears_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    FakeRemote(monkeypatch, fail_live=True)
    sync.sync_live(cfg["hosts"][0], cfg, now=NOW)
    FakeRemote(monkeypatch)
    assert sync.sync_live(cfg["hosts"][0], cfg, now=NOW + 9)["ok"] is True
    d = sync.read_live("vm")
    assert not d.get("stale_since") and not d.get("last_error")


# ---- 3. no collisions between collect and live caches ----

def test_collect_and_live_caches_never_collide(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    names = ["statusline", "live", "s-host.live"]  # deliberately not alphabetical
    cfg, _ = make_cfg(tmp_path, [{"name": n, "ssh": n} for n in names])
    FakeRemote(monkeypatch)
    status = sync.sync_all(cfg)
    assert sorted(s["host"] for s in status) == sorted(names)
    assert all(s["ok"] for s in status)
    for n in names:
        c, l = sync.read_cache(n), sync.read_live(n)
        assert c["host"] == n and "projects" in c and "sessions" not in c
        assert l["host"] == n and "sessions" in l and "projects" in l and "home" not in l
    # load_caches follows the configured order, it does not sort
    assert [c["host"] for c in sync.load_caches(cfg)] == names
    assert sorted(l["host"] for l in sync.load_lives(cfg) if l) == sorted(names)


def test_load_lives_skips_hosts_without_live_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "a", "ssh": "a"}, {"name": "b", "ssh": "b"}])
    FakeRemote(monkeypatch)
    sync.sync_live(cfg["hosts"][0], cfg, now=NOW)
    assert [l["host"] for l in sync.load_lives(cfg) if l] == ["a"]


# ---- 4. _write_json atomicity ----

def test_write_json_roundtrip_and_no_tmp_left(tmp_path):
    p = str(tmp_path / "d" / "x.json")
    os.makedirs(os.path.dirname(p))
    sync._write_json(p, {"a": [1, 2], "b": "é"})
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == {"a": [1, 2], "b": "é"}
    sync._write_json(p, {"a": 2})
    with open(p, encoding="utf-8") as f:
        assert json.load(f) == {"a": 2}
    assert os.listdir(os.path.dirname(p)) == ["x.json"]


def test_write_json_concurrent_writers_and_reader(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    p = str(d / "x.json")
    A = {"who": "A", "pad": "a" * 200000}
    B = {"who": "B", "pad": "b" * 150000}
    sync._write_json(p, A)
    stop = threading.Event()
    errors, reads = [], [0]

    def writer(doc):
        try:
            for _ in range(150):
                sync._write_json(p, doc)
        except Exception as e:  # noqa
            errors.append(("w", repr(e)))

    def reader():
        while not stop.is_set():
            try:
                with open(p, encoding="utf-8") as f:
                    got = json.load(f)
                assert got in (A, B)
                reads[0] += 1
            except Exception as e:  # noqa
                errors.append(("r", repr(e)))
                return

    rt = threading.Thread(target=reader)
    ws = [threading.Thread(target=writer, args=(A,)), threading.Thread(target=writer, args=(B,))]
    rt.start()
    for w in ws:
        w.start()
    for w in ws:
        w.join(60)
    stop.set()
    rt.join(60)
    assert errors == []
    assert reads[0] > 0
    assert os.listdir(str(d)) == ["x.json"]


# ---- 5. sync_all layers and `proj sync --live` ----

def test_sync_all_both_layers_uses_fresh_collect_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    fr = FakeRemote(monkeypatch, paths=["/r/x", "/r/y"])
    st = sync.sync_all(cfg)
    assert [(s["host"], s["ok"]) for s in st] == [("vm", True)]
    assert fr.actions() == ["collect", "live"]
    assert fr.calls[1][1]["paths"] == ["/r/x", "/r/y"]
    assert sync.read_cache("vm") and sync.read_live("vm")


def test_sync_all_live_only_skips_collect_and_uses_cached_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    FakeRemote(monkeypatch, paths=["/old/1"])
    sync.sync_all(cfg)
    before = open(os.path.join(sync.cache_dir(), "vm.json"), "rb").read()
    fr = FakeRemote(monkeypatch, paths=["/new/never"])
    st = sync.sync_all(cfg, live_only=True)
    assert st[0]["ok"] is True
    assert fr.actions() == ["live"]
    assert fr.calls[0][1]["paths"] == ["/old/1"]
    assert open(os.path.join(sync.cache_dir(), "vm.json"), "rb").read() == before


def test_sync_all_names_filters_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "a", "ssh": "a"}, {"name": "b", "ssh": "b"}])
    fr = FakeRemote(monkeypatch)
    st = sync.sync_all(cfg, ["b"])
    assert [s["host"] for s in st] == ["b"]
    assert {h for h, _ in fr.calls} == {"b"}


def test_sync_all_live_only_without_collect_cache_still_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    fr = FakeRemote(monkeypatch)
    st = sync.sync_all(cfg, live_only=True)
    assert fr.actions() == ["live"] and fr.calls[0][1]["paths"] == []
    assert st[0]["ok"] is True


def test_sync_all_local_both_layers_real_collect(tmp_path, make_repo, fake_ssh, monkeypatch):
    make_repo(tmp_path / "root" / "p1")
    cfg, _ = make_cfg(tmp_path, [{"name": "mac", "local": True, "roots": [str(tmp_path / "root")]}])
    spy = ActSpy(monkeypatch)
    st = sync.sync_all(cfg)
    assert st[0]["ok"] is True
    assert len(spy.calls) == 1
    assert [os.path.basename(p) for p in spy.calls[0][0]["paths"]] == ["p1"]
    assert sync.read_cache("mac")["projects"] and sync.read_live("mac")


def test_sync_all_one_layer_failing_reports_not_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    FakeRemote(monkeypatch, fail_live=True)
    st = sync.sync_all(cfg)
    assert len(st) == 1 and st[0]["ok"] is False and st[0]["error"]
    assert sync.read_cache("vm") is not None  # collect layer still written


def test_sync_all_collect_failure_live_uses_old_collect_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, _ = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    FakeRemote(monkeypatch, paths=["/old/1"])
    sync.sync_all(cfg)
    fr = FakeRemote(monkeypatch, paths=["/x"], fail_collect=True)
    st = sync.sync_all(cfg)
    assert st[0]["ok"] is False and "collect boom" in st[0]["error"]
    assert fr.calls[-1][1]["action"] == "live" and fr.calls[-1][1]["paths"] == ["/old/1"]


# ---- CLI ----

def test_cli_sync_parser_accepts_live_quiet_hosts():
    a = cli.parser().parse_args(["sync", "--live", "--quiet", "h1", "h2"])
    assert a.live is True and a.quiet is True and a.hosts == ["h1", "h2"]
    a = cli.parser().parse_args(["sync"])
    assert a.live is False and a.quiet is False and a.hosts == []


def test_cli_sync_live_runs_no_collect_and_removes_live_marker(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, cfgp = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    monkeypatch.setenv("PROJ_CONFIG", cfgp)
    FakeRemote(monkeypatch, paths=["/old/1"])
    sync.sync_all(cfg)
    os.makedirs(sync.cache_dir(), exist_ok=True)
    for m in (".syncing-live", ".syncing"):
        open(os.path.join(sync.cache_dir(), m), "w").close()
    fr = FakeRemote(monkeypatch)
    assert cli.main(["sync", "--live", "--quiet"]) == 0
    assert fr.actions() == ["live"] and fr.calls[0][1]["paths"] == ["/old/1"]
    assert not os.path.exists(os.path.join(sync.cache_dir(), ".syncing-live"))
    assert os.path.exists(os.path.join(sync.cache_dir(), ".syncing"))
    assert capsys.readouterr().out == ""


def test_cli_sync_default_refreshes_both_layers_and_fails_with_1(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, cfgp = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    monkeypatch.setenv("PROJ_CONFIG", cfgp)
    fr = FakeRemote(monkeypatch)
    assert cli.main(["sync", "--quiet"]) == 0
    assert fr.actions() == ["collect", "live"]
    FakeRemote(monkeypatch, fail_live=True)
    assert cli.main(["sync", "--live", "--quiet"]) == 1


# ---- round 2: layer-tagged errors and stale_since consistency ----

def _one_host(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    cfg, cfgp = make_cfg(tmp_path, [{"name": "vm", "ssh": "vm"}])
    return cfg, cfgp


def test_error_none_when_both_layers_ok(tmp_path, monkeypatch):
    cfg, _ = _one_host(tmp_path, monkeypatch)
    FakeRemote(monkeypatch)
    st = sync.sync_all(cfg)
    assert st[0]["ok"] is True and not st[0]["error"]


def test_error_prefix_live_only_failure(tmp_path, monkeypatch):
    cfg, _ = _one_host(tmp_path, monkeypatch)
    FakeRemote(monkeypatch, fail_live=True)
    st = sync.sync_all(cfg)
    assert st[0]["ok"] is False and st[0]["error"].startswith("live:")
    assert "connect failed" in st[0]["error"] and "collect" not in st[0]["error"]


def test_error_prefix_collect_only_failure(tmp_path, monkeypatch):
    cfg, _ = _one_host(tmp_path, monkeypatch)
    FakeRemote(monkeypatch, fail_collect=True)
    st = sync.sync_all(cfg)
    assert st[0]["ok"] is False and st[0]["error"].startswith("collect:")
    assert "collect boom" in st[0]["error"] and "live:" not in st[0]["error"]


def test_error_both_layers_failing_lists_both(tmp_path, monkeypatch):
    cfg, _ = _one_host(tmp_path, monkeypatch)
    FakeRemote(monkeypatch, fail_collect=True, fail_live=True)
    st = sync.sync_all(cfg)
    e = st[0]["error"]
    assert st[0]["ok"] is False
    assert e.startswith("collect:") and "collect boom" in e
    assert "; live:" in e and "connect failed" in e
    assert e.index("collect boom") < e.index("connect failed")


def test_error_live_only_mode_has_live_prefix(tmp_path, monkeypatch):
    cfg, _ = _one_host(tmp_path, monkeypatch)
    FakeRemote(monkeypatch, fail_live=True)
    st = sync.sync_all(cfg, live_only=True)
    assert st[0]["ok"] is False and st[0]["error"].startswith("live:")


def _set_stale(path, value):
    with open(path) as f:
        d = json.load(f)
    d["stale_since"] = value
    with open(path, "w") as f:
        json.dump(d, f)


@pytest.mark.parametrize("failing", ["collect", "live"])
def test_stale_since_keeps_first_failure_time_per_layer(tmp_path, monkeypatch, failing):
    cfg, _ = _one_host(tmp_path, monkeypatch)
    FakeRemote(monkeypatch)
    sync.sync_all(cfg)
    kw = {"fail_collect": True} if failing == "collect" else {"fail_live": True}
    FakeRemote(monkeypatch, **kw)
    sync.sync_all(cfg)
    path = os.path.join(sync.cache_dir(), "vm.json") if failing == "collect" else live_path("vm")
    with open(path) as f:
        first = json.load(f)
    assert first["stale_since"] and first["last_error"]
    _set_stale(path, 12345)          # pretend the first failure happened long ago
    sync.sync_all(cfg)               # fails again
    with open(path) as f:
        second = json.load(f)
    assert second["stale_since"] == 12345 and second["last_error"]
    # the layer that did not fail stays clean
    other = live_path("vm") if failing == "collect" else os.path.join(sync.cache_dir(), "vm.json")
    with open(other) as f:
        assert not json.load(f).get("stale_since")
    # recovery clears it
    FakeRemote(monkeypatch)
    sync.sync_all(cfg)
    with open(path) as f:
        assert not json.load(f).get("stale_since")


def test_stale_since_both_layers_same_semantics_when_both_fail(tmp_path, monkeypatch):
    cfg, _ = _one_host(tmp_path, monkeypatch)
    FakeRemote(monkeypatch)
    sync.sync_all(cfg)
    FakeRemote(monkeypatch, fail_collect=True, fail_live=True)
    sync.sync_all(cfg)
    cpath = os.path.join(sync.cache_dir(), "vm.json")
    _set_stale(cpath, 111)
    _set_stale(live_path("vm"), 222)
    sync.sync_all(cfg)
    assert sync.read_cache("vm")["stale_since"] == 111
    assert sync.read_live("vm")["stale_since"] == 222
