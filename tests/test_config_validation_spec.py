"""Independent spec tests for T12: config errors exit 2, name the key, never echo host values."""
import copy
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENT = "SENTINEL-9"


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "root").mkdir()
    e = dict(os.environ)
    e.update(HOME=str(tmp_path / "home"), PROJ_CACHE=str(tmp_path / "cache"),
             PROJ_CONFIG=str(tmp_path / "config.json"), PYTHONPATH=REPO)
    e.pop("PROJ_LIVE_TESTS", None)
    os.makedirs(e["HOME"], exist_ok=True)
    return {"env": e, "root": str(tmp_path / "root"), "cfg": tmp_path / "config.json"}


def base(root):
    return {"hosts": [{"name": "loc", "local": True, "roots": [root]}]}


def proj(env, cfg, *args):
    if cfg is not None:
        env["cfg"].write_text(json.dumps(cfg))
    p = subprocess.run([sys.executable, "-m", "projdeck"] + list(args), env=env["env"], cwd=REPO,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                       timeout=60)
    return p.returncode, p.stdout, p.stderr


def mutated(env, **top):
    cfg = base(env["root"])
    cfg.update(top)
    return cfg


def host_mutated(env, **kw):
    cfg = base(env["root"])
    cfg["hosts"][0].update(kw)
    return cfg


def assert_clean_error(rc, err, *keys):
    assert rc == 2, err
    assert "Traceback" not in err
    for k in keys:
        assert k in err, err


# subcommands that read config; all must refuse a bad config the same way
CMDS = [("report",), ("sync",), ("trash",), ("clean", "loc:/nonexistent"), ("restore",)]

# ---- top-level bad values: (id, config mutation, key that must be named)
TOP_BAD = [
    ("hosts-int", lambda e: mutated(e, hosts=5), "hosts"),
    ("hosts-dict", lambda e: mutated(e, hosts={"name": "x"}), "hosts"),
    ("hosts-str-item", lambda e: mutated(e, hosts=["mac"]), "hosts"),
    ("scan-str", lambda e: mutated(e, scan_depth="x"), "scan_depth"),
    ("scan-zero", lambda e: mutated(e, scan_depth=0), "scan_depth"),
    ("scan-neg", lambda e: mutated(e, scan_depth=-1), "scan_depth"),
    ("scan-float", lambda e: mutated(e, scan_depth=2.0), "scan_depth"),
    ("scan-bool", lambda e: mutated(e, scan_depth=True), "scan_depth"),
    ("plain-str", lambda e: mutated(e, include_plain="no"), "include_plain"),
    ("plain-1", lambda e: mutated(e, include_plain=1), "include_plain"),
    ("plain-0", lambda e: mutated(e, include_plain=0), "include_plain"),
    ("tiers-active-str", lambda e: mutated(e, tiers_days={"active": "x", "stale": 30}), "tiers_days"),
    ("tiers-stale-str", lambda e: mutated(e, tiers_days={"active": 3, "stale": "x"}), "tiers_days"),
    ("tiers-active-gt-stale", lambda e: mutated(e, tiers_days={"active": 30, "stale": 10}), "tiers_days"),
    ("tiers-equal", lambda e: mutated(e, tiers_days={"active": 10, "stale": 10}), "tiers_days"),
    ("tiers-zero", lambda e: mutated(e, tiers_days={"active": 0, "stale": 10}), "tiers_days"),
    ("tiers-neg", lambda e: mutated(e, tiers_days={"active": -1, "stale": 10}), "tiers_days"),
    ("tiers-bool", lambda e: mutated(e, tiers_days={"active": True, "stale": 10}), "tiers_days"),
    ("tiers-inf", lambda e: mutated(e, tiers_days={"active": 3, "stale": float("inf")}), "tiers_days"),
    ("tiers-nan", lambda e: mutated(e, tiers_days={"active": float("nan"), "stale": 10}), "tiers_days"),
]


@pytest.mark.parametrize("name,make,key", TOP_BAD, ids=[t[0] for t in TOP_BAD])
def test_bad_top_level_report_exits_2(env, name, make, key):
    rc, out, err = proj(env, make(env), "report")
    assert_clean_error(rc, err, key)


@pytest.mark.parametrize("cmd", CMDS, ids=[" ".join(c) for c in CMDS])
@pytest.mark.parametrize("make,key", [
    (lambda e: mutated(e, scan_depth=0), "scan_depth"),
    (lambda e: mutated(e, include_plain="no"), "include_plain"),
    (lambda e: mutated(e, tiers_days={"active": 30, "stale": 10}), "tiers_days"),
    (lambda e: mutated(e, hosts=5), "hosts"),
], ids=["scan", "plain", "tiers", "hosts"])
def test_every_subcommand_exits_2(env, cmd, make, key):
    rc, out, err = proj(env, make(env), *cmd)
    assert_clean_error(rc, err, key)


# ---- host-level bad values: key named as hosts[<i>].<key>, no value echoed
HOST_BAD = [
    ("roots-str", {"roots": "~/dev", "ssh": SENT + "-alias"}, "roots"),
    ("roots-empty", {"roots": [], "ssh": SENT + "-alias"}, "roots"),
    ("roots-nonstr", {"roots": [5, SENT + "-r"], "ssh": SENT + "-alias"}, "roots"),
    ("roots-dict", {"roots": {SENT: 1}, "ssh": SENT + "-alias"}, "roots"),
    ("tmux-str", {"tmux": SENT + "-nope", "ssh": SENT + "-alias"}, "tmux"),
    ("tmux-null", {"tmux": None, "ssh": SENT + "-alias"}, "tmux"),
    ("tmux-int", {"tmux": 1, "ssh": SENT + "-alias"}, "tmux"),
    ("local-str", {"local": SENT + "-yes", "ssh": SENT + "-alias"}, "local"),
    ("local-int", {"local": 1, "ssh": SENT + "-alias"}, "local"),
    ("ssh-empty", {"ssh": ""}, "ssh"),
    ("ssh-dash", {"ssh": "-o" + SENT}, "ssh"),
]


def two_hosts(env, bad):
    root = env["root"]
    good = {"name": "loc", "local": True, "roots": [root]}
    h = {"name": SENT + "-name", "roots": [root]}
    h.update(bad)
    return {"hosts": [good, h]}


@pytest.mark.parametrize("name,bad,key", HOST_BAD, ids=[t[0] for t in HOST_BAD])
@pytest.mark.parametrize("cmd", [("report",), ("sync",), ("trash",)], ids=lambda c: c[0])
def test_host_error_names_position_and_key_without_values(env, name, bad, key, cmd):
    rc, out, err = proj(env, two_hosts(env, bad), *cmd)
    assert_clean_error(rc, err, "hosts[1].%s" % key)
    assert "hosts[0]" not in err
    assert SENT not in err, err
    assert "~/dev" not in err


def test_host_missing_name_no_values_echoed(env):
    cfg = {"hosts": [{"ssh": SENT + "-alias", "roots": [SENT + "-root"], "extra": SENT}]}
    rc, out, err = proj(env, cfg, "report")
    assert_clean_error(rc, err, "hosts[0]")
    assert "name" in err
    assert SENT not in err, err


def test_host_name_wrong_type_or_empty_rejected(env):
    for bad in ("", 5, None):
        cfg = {"hosts": [{"name": bad, "ssh": SENT + "-alias"}]}
        rc, out, err = proj(env, cfg, "report")
        assert_clean_error(rc, err, "hosts[0]")
        assert SENT not in err


def test_host_ssh_leading_dash_no_values_echoed(env):
    cfg = {"hosts": [{"name": SENT + "-n", "ssh": "-oProxyCommand=" + SENT}]}
    rc, out, err = proj(env, cfg, "report")
    assert_clean_error(rc, err, "hosts[0].ssh")
    assert SENT not in err, err


def test_issue_example_roots_string_with_secret_alias(env):
    cfg = {"hosts": [{"name": "h", "ssh": "secret-alias-x", "roots": "~/dev"}]}
    rc, out, err = proj(env, cfg, "report")
    assert_clean_error(rc, err, "hosts[0].roots")
    assert "secret-alias-x" not in err


def test_error_names_config_file(env):
    rc, out, err = proj(env, mutated(env, scan_depth=0), "report")
    assert str(env["cfg"]) in err


def test_top_level_scalar_error_may_include_value(env):
    # spec: top-level scalar keys do not leak; error keeps "got <value>"
    rc, out, err = proj(env, mutated(env, scan_depth="weird-val"), "report")
    assert_clean_error(rc, err, "scan_depth")
    assert "weird-val" in err


# ---- good values stay accepted
GOOD = [
    ("default", lambda e: base(e["root"])),
    ("tmux-always", lambda e: host_mutated(e, tmux="always")),
    ("tmux-true", lambda e: host_mutated(e, tmux=True)),
    ("tmux-false", lambda e: host_mutated(e, tmux=False)),
    ("roots-tilde", lambda e: host_mutated(e, roots=["~/dev"])),
    ("plain-false", lambda e: mutated(e, include_plain=False)),
    ("plain-true", lambda e: mutated(e, include_plain=True)),
    ("scan-1", lambda e: mutated(e, scan_depth=1)),
    ("scan-3", lambda e: mutated(e, scan_depth=3)),
    ("tiers-ok", lambda e: mutated(e, tiers_days={"active": 3, "stale": 30})),
    ("tiers-float", lambda e: mutated(e, tiers_days={"active": 0.5, "stale": 2.5})),
    ("no-hosts-key-defaults-ok-local", lambda e: host_mutated(e, local=False, ssh="s-host")),
]


@pytest.mark.parametrize("name,make", GOOD, ids=[g[0] for g in GOOD])
def test_good_config_loads(env, name, make):
    sys.path.insert(0, REPO)
    from projdeck import config
    env["cfg"].write_text(json.dumps(make(env)))
    cfg = config.load_config(str(env["cfg"]))
    assert cfg["hosts"]


@pytest.mark.parametrize("name,make", GOOD[:5], ids=[g[0] for g in GOOD[:5]])
def test_good_config_report_not_a_config_error(env, name, make):
    rc, out, err = proj(env, make(env), "report")
    assert rc != 2, err
    assert "Traceback" not in err


@pytest.mark.parametrize("name,make,key", TOP_BAD[:6] + TOP_BAD[10:12], ids=lambda x: x if isinstance(x, str) else "")
def test_load_config_raises_value_error(env, name, make, key):
    sys.path.insert(0, REPO)
    from projdeck import config
    env["cfg"].write_text(json.dumps(make(env)))
    with pytest.raises(ValueError) as ei:
        config.load_config(str(env["cfg"]))
    assert key in str(ei.value)


def test_load_config_host_error_does_not_echo(env):
    sys.path.insert(0, REPO)
    from projdeck import config
    env["cfg"].write_text(json.dumps({"hosts": [{"name": "n", "ssh": SENT + "-alias", "roots": "~/dev"}]}))
    with pytest.raises(ValueError) as ei:
        config.load_config(str(env["cfg"]))
    assert "hosts[0].roots" in str(ei.value)
    assert SENT not in str(ei.value)


def test_good_config_does_not_mutate_between_loads(env):
    sys.path.insert(0, REPO)
    from projdeck import config
    env["cfg"].write_text(json.dumps(base(env["root"])))
    a = config.load_config(str(env["cfg"]))
    b = config.load_config(str(env["cfg"]))
    assert copy.deepcopy(a) == copy.deepcopy(b)


# ---- sync host names
def test_sync_unknown_host_exits_2_naming_it(env):
    rc, out, err = proj(env, base(env["root"]), "sync", "nosuch")
    assert_clean_error(rc, err, "nosuch")


def test_sync_unknown_host_among_known_exits_2(env):
    rc, out, err = proj(env, base(env["root"]), "sync", "loc", "nosuch")
    assert_clean_error(rc, err, "nosuch")


def test_sync_known_host_runs(env):
    rc, out, err = proj(env, base(env["root"]), "sync", "loc")
    assert rc == 0, err
    assert "Traceback" not in err
    assert "nosuch" not in err
    # a cache was written for the host (sync actually ran)
    cache = env["env"]["PROJ_CACHE"]
    assert os.path.isdir(cache) and os.listdir(cache)


def test_sync_known_host_quiet_runs(env):
    rc, out, err = proj(env, base(env["root"]), "sync", "--quiet", "loc")
    assert rc == 0, err


def test_sync_no_args_still_syncs_all(env):
    rc, out, err = proj(env, base(env["root"]), "sync")
    assert rc == 0, err


def test_sync_unknown_host_does_not_sync_known_ones(env):
    proj(env, base(env["root"]), "sync", "loc", "nosuch")
    cache = env["env"]["PROJ_CACHE"]
    assert not os.path.isdir(cache) or not any(
        f.endswith(".json") and "loc" in f for _, _, fs in os.walk(cache) for f in fs)


# ---- --older
@pytest.mark.parametrize("val", ["abc", "nan", "inf", "-inf", "NaN", "Infinity", "xd"])
def test_older_bad_value_names_option(env, val):
    rc, out, err = proj(env, base(env["root"]), "trash", "--purge", "--older", val)
    assert rc != 0
    assert "--older" in err, err
    assert "Traceback" not in err


def test_older_abc_message_mentions_value(env):
    rc, out, err = proj(env, base(env["root"]), "trash", "--purge", "--older", "abc")
    assert "--older" in err and "abc" in err


@pytest.mark.parametrize("val", ["30", "30d", "0.5d"])
def test_older_good_value_accepted(env, val):
    rc, out, err = proj(env, base(env["root"]), "trash", "--purge", "--older", val)
    assert "--older" not in err, err
    assert "Traceback" not in err
    assert rc != 2
