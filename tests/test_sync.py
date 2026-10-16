import json
import os

import pytest

from projdeck import config, sync


def write_cfg(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


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
