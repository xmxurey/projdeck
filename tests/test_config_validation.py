"""Config errors exit 2 and name the key; host-level errors never echo host values."""
import json

import pytest

from projdeck import cli, config


def run(tmp_path, monkeypatch, capsys, cfg, argv=("report",)):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    monkeypatch.setenv("PROJ_CONFIG", str(p))
    rc = cli.main(list(argv))
    return rc, capsys.readouterr().err


GOOD_HOST = {"name": "s-host", "local": True}

BAD = [
    ({"hosts": 5}, "hosts"),
    ({"hosts": ["mac"]}, "hosts[0]"),
    ({"hosts": [dict(GOOD_HOST, roots="~/dev")]}, "hosts[0].roots"),
    ({"hosts": [dict(GOOD_HOST, roots=[])]}, "hosts[0].roots"),
    ({"hosts": [dict(GOOD_HOST, roots=[1])]}, "hosts[0].roots"),
    ({"hosts": [dict(GOOD_HOST, tmux="nope")]}, "hosts[0].tmux"),
    ({"scan_depth": "x"}, "scan_depth"),
    ({"scan_depth": 0}, "scan_depth"),
    ({"scan_depth": 2.0}, "scan_depth"),
    ({"scan_depth": True}, "scan_depth"),
    ({"include_plain": "no"}, "include_plain"),
    ({"include_plain": 1}, "include_plain"),
    ({"include_plain": 0}, "include_plain"),
    ({"tiers_days": {"active": "x"}}, "tiers_days.active"),
    ({"tiers_days": {"active": True}}, "tiers_days.active"),
    ({"tiers_days": {"active": 90, "stale": 90}}, "tiers_days"),
    ({"tiers_days": {"active": 100}}, "tiers_days"),
]


@pytest.mark.parametrize("cfg,key", BAD)
def test_bad_config_exits_2_naming_the_key(tmp_path, monkeypatch, capsys, cfg, key):
    rc, err = run(tmp_path, monkeypatch, capsys, cfg)
    assert rc == 2
    assert key in err
    assert "Traceback" not in err


def test_host_errors_do_not_echo_values(tmp_path, monkeypatch, capsys):
    rc, err = run(tmp_path, monkeypatch, capsys,
                  {"hosts": [{"name": "s-host", "ssh": "secret-alias-x", "roots": "~/dev"}]})
    assert rc == 2 and "hosts[0].roots" in err and "secret-alias-x" not in err
    rc, err = run(tmp_path, monkeypatch, capsys, {"hosts": [{"ssh": "secret-alias-x"}]})
    assert rc == 2 and "hosts[0].name" in err and "secret-alias-x" not in err
    rc, err = run(tmp_path, monkeypatch, capsys,
                  {"hosts": [{"name": "s-host", "ssh": "-oSecretOpt"}]})
    assert rc == 2 and "hosts[0].ssh" in err and "SecretOpt" not in err and "s-host" not in err
    rc, err = run(tmp_path, monkeypatch, capsys, {"hosts": [{"name": "bad name!", "local": True}]})
    assert rc == 2 and "hosts[0].name" in err and "bad name" not in err


@pytest.mark.parametrize("cfg", [
    {}, {"hosts": [dict(GOOD_HOST, tmux="always")]}, {"hosts": [dict(GOOD_HOST, tmux=False)]},
    {"hosts": [dict(GOOD_HOST, roots=["~/dev"])]}, {"include_plain": False},
    {"scan_depth": 3, "tiers_days": {"active": 7.5, "stale": 30}},
])
def test_valid_configs_load(tmp_path, cfg):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    assert config.load_config(str(p))["hosts"]


def test_sync_unknown_host_exits_2(tmp_path, monkeypatch, capsys):
    rc, err = run(tmp_path, monkeypatch, capsys, {"hosts": [{"name": "loc", "local": True}]},
                  ["sync", "nosuch"])
    assert rc == 2 and "nosuch" in err


def test_older_abc_names_the_option(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}")
    monkeypatch.setenv("PROJ_CONFIG", str(p))
    with pytest.raises(SystemExit) as e:
        cli.main(["trash", "--purge", "--older", "abc"])
    assert "--older" in str(e.value)


@pytest.mark.parametrize("mux", [0, 1, "screen", None])
def test_mux_must_be_a_real_bool_or_name(tmp_path, monkeypatch, capsys, mux):
    rc, err = run(tmp_path, monkeypatch, capsys, {"hosts": [dict(GOOD_HOST, mux=mux)]})
    assert rc == 2 and "hosts[0].mux" in err and "s-host" not in err


@pytest.mark.parametrize("brief", [1, 0, "yes", None, []])
def test_brief_must_be_a_real_bool(tmp_path, monkeypatch, capsys, brief):
    cfg = {"hosts": [{"name": "s-host", "ssh": "secret-alias-x", "roots": ["/home/u/secret"],
                      "brief": brief}]}
    rc, err = run(tmp_path, monkeypatch, capsys, cfg)
    assert rc == 2 and "hosts[0].brief" in err
    assert "secret-alias-x" not in err and "/home/u/secret" not in err and "s-host" not in err


@pytest.mark.parametrize("host", [
    dict(GOOD_HOST, brief=True), dict(GOOD_HOST, brief=False), dict(GOOD_HOST)])
def test_brief_bool_or_absent_loads_and_is_kept(tmp_path, host):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"hosts": [host]}))
    loaded = config.load_config(str(p))["hosts"][0]
    assert loaded.get("brief") == host.get("brief")
