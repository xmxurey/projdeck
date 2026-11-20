"""Spec tests for T01: the test session never touches the real environment.

Each scenario starts a child pytest with a throw-away HOME, recording stand-ins
for every external program on PATH, and sentinel values for PROJ_CACHE /
PROJ_CONFIG.  Nothing here reads or writes the real cache or config.
"""
import json
import os
import site
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RECORDED = ["tmux", "tailscale", "systemctl", "systemd-analyze", "claude",
            "zellij", "ssh"]
MUST_BE_ZERO = ["tmux", "tailscale", "systemctl", "systemd-analyze", "claude"]
SENTINEL_HOST = "sentinel-host"


def _make_recorders(bindir, logdir):
    bindir.mkdir()
    logdir.mkdir()
    for name in RECORDED:
        body = {"claude": "echo '[]'", "ssh": "exit 255"}.get(name, "exit 1")
        p = bindir / name
        p.write_text('#!/bin/sh\necho "%s $*" >> "%s/%s.log"\n%s\n'
                     % (name, logdir, name, body))
        p.chmod(p.stat().st_mode | stat.S_IXUSR)


def _child_env(tmp, bindir, cache, config):
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.pop("PROJ_LIVE_TESTS", None)
    env.update({
        "HOME": str(home),
        "PYTHONUSERBASE": site.USER_BASE,
        "PROJ_CACHE": str(cache),
        "PROJ_CONFIG": str(config),
        "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def _run(args, env):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + args,
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=900)


def _calls(logdir, name):
    f = logdir / (name + ".log")
    return f.read_text().splitlines() if f.exists() else []


@pytest.fixture
def sandbox(tmp_path):
    bindir, logdir = tmp_path / "bin", tmp_path / "calls"
    _make_recorders(bindir, logdir)
    return tmp_path, bindir, logdir


def test_real_program_stubs_cover_required_programs():
    code = ("from tests.conftest import REAL_PROGRAM_STUBS as S;"
            "import json;print(json.dumps(sorted(S)))")
    env = dict(os.environ, PYTHONUSERBASE=site.USER_BASE,
               PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    names = set(json.loads(r.stdout.strip().splitlines()[-1]))
    assert {"claude", "ssh", "zellij", "tmux", "tailscale", "systemctl",
            "systemd-analyze"} <= names


def test_autouse_points_cache_and_config_into_tmp(tmp_path_factory):
    base = tmp_path_factory.getbasetemp().resolve()
    cache = os.environ.get("PROJ_CACHE")
    config = os.environ.get("PROJ_CONFIG")
    assert cache, "PROJ_CACHE must be set by the session"
    assert config, "PROJ_CONFIG must be set by the session"
    for v in (cache, config):
        assert base in Path(v).resolve().parents, v
    assert not Path(config).exists()


def test_programs_not_called_by_four_files(sandbox):
    tmp, bindir, logdir = sandbox
    env = _child_env(tmp, bindir, tmp / "cache", tmp / "cfg.json")
    r = _run(["tests/test_agent_sessions.py", "tests/test_agent_live_spec.py",
              "tests/test_contrib_docs.py", "tests/test_agent_zellij.py"], env)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    for name in MUST_BE_ZERO:
        assert _calls(logdir, name) == [], name


def test_live_tests_skipped_without_opt_in(sandbox):
    tmp, bindir, logdir = sandbox
    env = _child_env(tmp, bindir, tmp / "cache", tmp / "cfg.json")
    r = _run(["-rs",
              "tests/test_agent_zellij.py::test_matches_zellij_dump_layout_on_this_machine",
              "tests/test_contrib_docs.py::test_unit_systemd_verify_clean"], env)
    out = r.stdout
    assert r.returncode == 0, out[-2000:]
    assert "2 skipped" in out, out
    skips = [l for l in out.splitlines() if l.startswith("SKIPPED")]
    assert len(skips) == 2, out
    assert all("PROJ_LIVE_TESTS" in l for l in skips), skips
    for name in MUST_BE_ZERO:
        assert _calls(logdir, name) == [], name


PROBE = "tests/test_isolation_spec.py::test_probe_default_config_and_cache"


def test_probe_default_config_and_cache():
    """Probe: uses the default config/cache paths, as real code would.

    It sets neither PROJ_CONFIG nor PROJ_CACHE; the session isolation decides
    where they point.  It loads the default config and runs a sync, which
    contacts configured hosts via ssh and writes cache files.
    """
    from projdeck import cli, config

    cfg = config.load_config()
    print("hosts:", [h["name"] for h in cfg["hosts"]])
    try:
        cli.main(["sync"])
    except SystemExit:
        pass


def _run_probe(sandbox, cache, cfg, capture=False):
    tmp, bindir, logdir = sandbox
    env = _child_env(tmp, bindir, cache, cfg)
    r = _run((["-s"] if capture else []) + [PROBE], env)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return r, logdir


def test_external_proj_cache_sentinel_stays_empty(sandbox):
    tmp = sandbox[0]
    sentinel = tmp / "sentinel-cache"
    sentinel.mkdir()
    _run_probe(sandbox, sentinel, tmp / "cfg.json")
    assert list(sentinel.rglob("*")) == []


def test_external_proj_config_sentinel_not_used(sandbox):
    tmp = sandbox[0]
    cfg = tmp / "sentinel-config.json"
    cfg.write_text(json.dumps(
        {"hosts": [{"name": SENTINEL_HOST, "ssh": SENTINEL_HOST}]}))
    r, logdir = _run_probe(sandbox, tmp / "cache", cfg, capture=True)
    assert SENTINEL_HOST not in r.stdout
    assert SENTINEL_HOST not in r.stderr
    assert all(SENTINEL_HOST not in c for c in _calls(logdir, "ssh"))


def test_mockup_verbatim_test_removed_rest_passes(sandbox):
    tmp, bindir, logdir = sandbox
    env = _child_env(tmp, bindir, tmp / "cache", tmp / "cfg.json")
    c = _run(["--collect-only", "tests/test_mockup_fixture.py"], env)
    assert c.returncode == 0, c.stdout[-1500:]
    assert "tests/test_mockup_fixture.py::" in c.stdout
    assert "test_matches_design_appendix_verbatim" not in c.stdout
    r = _run(["tests/test_mockup_fixture.py"], env)
    assert r.returncode == 0, r.stdout[-2000:]
    assert "passed" in r.stdout
