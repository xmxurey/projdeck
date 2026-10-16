import json
import os
import sys
import threading
import time

import pytest

from projdeck import config, sync


def write_cfg(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def seed_cache(cache_dir, name, data):
    os.makedirs(cache_dir)
    with open(os.path.join(cache_dir, name + ".json"), "w") as f:
        json.dump(data, f)


def test_load_config_defaults_and_validation(tmp_path):
    cfg = config.load_config(str(tmp_path / "missing.json"))
    assert cfg["hosts"][0]["local"] is True and cfg["tiers_days"] == {"active": 14, "stale": 90}
    with pytest.raises(ValueError):
        config.load_config(write_cfg(tmp_path, {"hosts": [{"name": "x"}]}))
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [{"name": "sg", "ssh": "sg"}],
                                                  "ssh_timeout": 5}))
    assert cfg["ssh_timeout"] == 5 and cfg["scan_depth"] == 2
    assert config.host_by_name(cfg, "sg")["ssh"] == "sg"
    with pytest.raises(ValueError):
        config.host_by_name(cfg, "nope")


def test_load_config_merges_partial_tiers_days(tmp_path):
    cfg = config.load_config(write_cfg(tmp_path, {"tiers_days": {"active": 7}}))
    assert cfg["tiers_days"] == {"active": 7, "stale": 90}


def test_load_config_rejects_non_object_top_level(tmp_path):
    with pytest.raises(ValueError):
        config.load_config(write_cfg(tmp_path, [1, 2]))


@pytest.mark.parametrize("name", ["has space", "a/b", ".hidden", "semi;colon", "tab\tname", 7])
def test_load_config_rejects_bad_host_names(tmp_path, name):
    with pytest.raises(ValueError):
        config.load_config(write_cfg(tmp_path, {"hosts": [{"name": name, "local": True}]}))


def test_load_config_accepts_safe_host_names_and_rejects_duplicates(tmp_path):
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "my-host.lan_1", "local": True}, {"name": "Mac9", "ssh": "mac9"}]}))
    assert [h["name"] for h in cfg["hosts"]] == ["my-host.lan_1", "Mac9"]
    with pytest.raises(ValueError):
        config.load_config(write_cfg(tmp_path, {"hosts": [
            {"name": "dup", "local": True}, {"name": "dup", "ssh": "x"}]}))


def test_collector_request():
    cfg = config.load_config("/nonexistent")
    req = config.collector_request(cfg, {"name": "m", "local": True, "roots": ["~/dev"]})
    assert req == {"action": "collect", "host": "m", "roots": ["~/dev"], "scan_depth": 2,
                   "include_plain": True, "patterns": None}


def test_sync_local_and_remote_write_cache(tmp_path, make_repo, fake_ssh):
    make_repo(tmp_path / "root" / "p1")
    root = str(tmp_path / "root")
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "mac", "local": True, "roots": [root]},
        {"name": "vm", "ssh": "vm", "roots": [root]}]}))
    status = sync.sync_all(cfg)
    assert sorted((s["host"], s["ok"]) for s in status) == [("mac", True), ("vm", True)]
    vm = sync.read_cache("vm")
    assert vm["host"] == "vm" and vm["stale_since"] is None
    assert [os.path.basename(p["path"]) for p in vm["projects"]] == ["p1"]
    assert sync.oldest_sync(cfg) == min(c["synced_at"] for c in sync.load_caches(cfg))


def test_sync_failure_keeps_old_cache_and_marks_stale(tmp_path, fake_ssh):
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "vm", "ssh": "unreachable", "roots": [str(tmp_path)]}]}))
    os.makedirs(fake_ssh)
    with open(os.path.join(fake_ssh, "vm.json"), "w") as f:
        json.dump({"schema": 1, "host": "vm", "synced_at": 111, "projects": []}, f)
    [st] = sync.sync_all(cfg)
    assert st["ok"] is False and "connect failed" in st["error"]
    c = sync.read_cache("vm")
    assert c["stale_since"] == 111 and "connect failed" in c["last_error"]


def test_oldest_sync_none_when_a_host_never_synced(tmp_path, fake_ssh):
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [{"name": "a", "local": True}]}))
    assert sync.oldest_sync(cfg) is None


def test_run_agent_raises_agent_error(tmp_path, fake_ssh):
    with pytest.raises(sync.AgentError):
        sync.run_agent({"name": "m", "local": True}, {"action": "nope"})


def test_run_agent_non_dict_output_raises(monkeypatch):
    monkeypatch.setattr(sync, "_argv",
                        lambda host, req: ([sys.executable, "-c", "print('[1, 2]')"], None))
    with pytest.raises(sync.AgentError, match="unexpected agent output: list"):
        sync.run_agent({"name": "m", "local": True}, {"action": "collect"})


def test_sync_reports_cache_write_oserror(tmp_path, make_repo, fake_ssh, monkeypatch):
    make_repo(tmp_path / "root" / "p1")
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "mac", "local": True, "roots": [str(tmp_path / "root")]}]}))

    def boom(name, data):
        raise OSError("disk full")

    monkeypatch.setattr(sync, "_write_cache", boom)
    status = sync.sync_all(cfg)
    assert status[0]["host"] == "mac" and status[0]["ok"] is False
    assert "disk full" in status[0]["error"]


def test_failed_sync_survives_stale_mark_write_error(tmp_path, fake_ssh, monkeypatch):
    seed_cache(fake_ssh, "vm", {"schema": 1, "host": "vm", "synced_at": 111, "projects": []})
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "vm", "ssh": "unreachable", "roots": [str(tmp_path)]}]}))

    def boom(name, data):
        raise OSError("disk full")

    monkeypatch.setattr(sync, "_write_cache", boom)
    st = sync.sync_host(cfg["hosts"][0], cfg)
    assert st["ok"] is False and "connect failed" in st["error"]


def test_write_cache_tmp_name_is_unique_per_process_and_thread(fake_ssh, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(src)
        real_replace(src, dst)

    monkeypatch.setattr(sync.os, "replace", spy)
    sync._write_cache("vm", {"a": 1})
    final = sync._cache_path("vm")
    assert seen == ["%s.%d.%d.tmp" % (final, os.getpid(), threading.get_ident())]
    assert sync.read_cache("vm") == {"a": 1}


def test_sync_host_now_is_int(tmp_path, make_repo, fake_ssh):
    make_repo(tmp_path / "root" / "p1")
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "mac", "local": True, "roots": [str(tmp_path / "root")]}]}))
    sync.sync_host(cfg["hosts"][0], cfg, now=0)
    assert sync.read_cache("mac")["synced_at"] == 0


def test_sync_host_default_now_is_current_time(tmp_path, make_repo, fake_ssh):
    make_repo(tmp_path / "root" / "p1")
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "mac", "local": True, "roots": [str(tmp_path / "root")]}]}))
    before = int(time.time())
    sync.sync_host(cfg["hosts"][0], cfg)
    after = int(time.time())
    assert before <= sync.read_cache("mac")["synced_at"] <= after


def test_repeated_failure_keeps_original_stale_since(tmp_path, fake_ssh):
    seed_cache(fake_ssh, "vm", {"schema": 1, "host": "vm", "synced_at": 111, "projects": []})
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "vm", "ssh": "unreachable", "roots": [str(tmp_path)]}]}))
    sync.sync_host(cfg["hosts"][0], cfg, now=200)
    sync.sync_host(cfg["hosts"][0], cfg, now=300)
    c = sync.read_cache("vm")
    assert c["stale_since"] == 111 and "connect failed" in c["last_error"]


def test_successful_sync_after_failure_clears_stale_marks(tmp_path, make_repo, fake_ssh):
    root = tmp_path / "root"
    make_repo(root / "p1")
    seed_cache(fake_ssh, "vm", {"schema": 1, "host": "vm", "synced_at": 111, "projects": []})
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "vm", "ssh": "unreachable", "roots": [str(root)]}]}))
    [failed] = sync.sync_all(cfg)
    assert failed["ok"] is False
    c = sync.read_cache("vm")
    assert c["stale_since"] == 111 and c["last_error"]

    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "vm", "ssh": "vm", "roots": [str(root)]}]}))
    [ok] = sync.sync_all(cfg)
    assert ok["ok"] is True
    c = sync.read_cache("vm")
    assert c["stale_since"] is None and c["last_error"] is None
    assert [os.path.basename(p["path"]) for p in c["projects"]] == ["p1"]


def test_payload_with_quote_and_non_ascii_round_trips(tmp_path, make_repo, fake_ssh):
    root = tmp_path / "it's 测试"
    make_repo(root / "proj")
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "vm", "ssh": "vm", "roots": [str(root)]}]}))
    [st] = sync.sync_all(cfg)
    assert st["ok"] is True, st
    vm = sync.read_cache("vm")
    assert [os.path.basename(p["path"]) for p in vm["projects"]] == ["proj"]


def test_first_failed_sync_writes_stub_cache(tmp_path, fake_ssh):
    from projdeck import report
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "vm", "ssh": "unreachable", "roots": [str(tmp_path)]}]}))
    assert sync.oldest_sync(cfg) is None
    sync.sync_host(cfg["hosts"][0], cfg, now=500)
    c = sync.read_cache("vm")
    assert c["projects"] == [] and c["synced_at"] == 0 and c["stale_since"] == 500
    assert "connect failed" in c["last_error"] and c["schema"] == 1
    assert sync.oldest_sync(cfg) == 0
    out = report.render([], sync.load_caches(cfg), 1000)
    assert "vm" in out and "last sync failed" in out


def test_load_config_rejects_dash_ssh_alias(tmp_path):
    with pytest.raises(ValueError):
        config.load_config(write_cfg(tmp_path, {"hosts": [{"name": "x", "ssh": "-oProxyCommand=x"}]}))
