"""T19 independent spec tests: cli no longer reaches into ui privates; ui.preview_target
behaves as specified; `_preview` end to end is unchanged.  Written from the brief only."""
import ast
import copy
import json
import os
import subprocess
import sys
import site
import time

import pytest

from projdeck import board, config, model, ui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 1_800_000_000
HOST = "s-host"


def _project(path, **kw):
    p = {"path": path, "exists": True, "git": None, "suspicious": [], "progress": "", "errors": [],
         "sessions": [], "last_mtime": NOW - 100}
    p.update(kw)
    return p


def _session(project, **kw):
    d = {"id": "s1", "project": project, "cwd": project, "latest_cwd": project,
         "kind": "interactive", "live": True, "name": "n-s1", "title": "t", "host": HOST,
         "started_at": NOW - 3000, "status": "busy", "state": "busy", "idle_since": None,
         "question": "", "last_reply": "hello there", "last_human": {"ts": 0, "text": ""},
         "last_input": "human", "subagents_running": 0, "segments": [["busy", NOW - 3000, NOW - 60]],
         "attach": "", "tasks": None, "tasks_owner": None, "errors": []}
    d.update(kw)
    return d


def _cache(projects, collected=NOW):
    return {"schema": 1, "host": HOST, "collected_at": collected, "synced_at": collected,
            "stale_since": None, "last_error": None, "projects": projects}


def _live(sessions, collected=NOW):
    return {"schema": 1, "host": HOST, "collected_at": collected, "synced_at": collected,
            "stale_since": None, "last_error": None, "sessions": list(sessions), "projects": {},
            "zellij_exited": None, "errors": []}


def _cfg():
    c = copy.deepcopy(config.DEFAULTS)
    c["hosts"] = [{"name": HOST, "ssh": HOST}]
    return c


def _world(projects, sessions):
    cfg = _cfg()
    caches = [_cache(projects)]
    lives = [_live(sessions)]
    b = board.build(caches, lives, cfg, NOW)
    rows = model.rows(caches, cfg, NOW)
    return b, rows


# ---------------------------------------------------------------- AC3: no ui._ in cli
def test_cli_has_no_private_ui_attribute_access():
    with open(os.path.join(ROOT, "projdeck", "cli.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    bad = [n.attr for n in ast.walk(tree)
           if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
           and n.value.id == "ui" and n.attr.startswith("_")]
    assert bad == []


def test_preview_target_is_public_and_callable():
    assert callable(ui.preview_target)
    assert not ui.preview_target.__name__.startswith("_")


# ---------------------------------------------------------------- preview_target behaviour
def test_cached_project_with_live_session_returns_its_row_and_live():
    b, rows = _world([_project("/home/u/a"), _project("/home/u/b")],
                     [_session("/home/u/a")])
    row, live = ui.preview_target(b, rows, HOST, "/home/u/a", NOW)
    assert row is not None and row["host"] == HOST and row["path"] == "/home/u/a"
    assert row == model.find_row(rows, HOST, "/home/u/a")
    assert live is not None
    assert live["project"] == "/home/u/a"


def test_cached_project_without_live_session_has_no_live():
    b, rows = _world([_project("/home/u/a"), _project("/home/u/b")],
                     [_session("/home/u/a")])
    row, live = ui.preview_target(b, rows, HOST, "/home/u/b", NOW)
    assert row is not None and row["path"] == "/home/u/b"
    assert live is None


def test_live_only_project_gets_minimal_row_and_live():
    b, rows = _world([_project("/home/u/a")], [_session("/home/u/new")])
    row, live = ui.preview_target(b, rows, HOST, "/home/u/new", NOW)
    assert row is not None
    assert row["host"] == HOST and row["path"] == "/home/u/new"
    assert live is not None and live["project"] == "/home/u/new"
    assert model.find_row(rows, HOST, "/home/u/new") is None  # the input rows were not extended


def test_unknown_host_and_path_return_none_none():
    b, rows = _world([_project("/home/u/a")], [_session("/home/u/a")])
    assert ui.preview_target(b, rows, "no-such-host", "/nowhere", NOW) == (None, None)


def test_known_host_unknown_path_returns_none_none():
    b, rows = _world([_project("/home/u/a")], [_session("/home/u/a")])
    assert ui.preview_target(b, rows, HOST, "/nowhere", NOW) == (None, None)


def test_known_path_on_other_host_returns_none_none():
    b, rows = _world([_project("/home/u/a")], [_session("/home/u/a")])
    assert ui.preview_target(b, rows, "em-host", "/home/u/a", NOW) == (None, None)


def test_empty_inputs_return_none_none():
    b = board.build([], [], _cfg(), NOW)
    assert ui.preview_target(b, [], HOST, "/home/u/a", NOW) == (None, None)
    assert ui.preview_target(b, [], "", "", NOW) == (None, None)


# ---------------------------------------------------------------- end to end
@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cache = tmp_path / "cache"
    (cache / "live").mkdir(parents=True)
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"hosts": [{"name": HOST, "ssh": HOST}]}))
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for prog, code in (("ssh", 255), ("zellij", 1), ("tmux", 1), ("tailscale", 1),
                       ("systemctl", 1), ("fzf", 1), ("claude", 0)):
        s = stubs / prog
        s.write_text("#!/bin/sh\n" + ("echo '[]'\n" if prog == "claude" else "") + "exit %d\n" % code)
        s.chmod(0o755)
    e = dict(os.environ)
    e.update(HOME=str(home), PROJ_CACHE=str(cache), PROJ_CONFIG=str(cfgp),
             PYTHONUSERBASE=site.USER_BASE, PATH=str(stubs) + os.pathsep + os.environ["PATH"])
    e.pop("PROJ_LIVE_TESTS", None)
    return {"env": e, "cache": cache}


def _put(env, projects, sessions):
    now = time.time()
    (env["cache"] / (HOST + ".json")).write_text(json.dumps(_cache(projects, now)))
    (env["cache"] / "live" / (HOST + ".json")).write_text(json.dumps(_live(sessions, now)))


def _preview(env, path):
    r = subprocess.run([sys.executable, "-m", "projdeck", "_preview", HOST, path],
                       cwd=ROOT, env=env["env"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=120)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def _now_session(project, **kw):
    t = int(time.time())
    return _session(project, started_at=t - 3000, segments=[["busy", t - 3000, t - 60]], **kw)


def test_e2e_live_only_project_says_not_collected_and_shows_last_6h(env):
    _put(env, [_project("/home/u/a")], [_now_session("/home/u/new")])
    code, out, err = _preview(env, "/home/u/new")
    assert code == 0, err
    out = ui.strip_ansi(out)
    assert "该项目还没采集" in out
    assert "最近 6 小时" in out
    assert "(not in cache)" not in out


def test_e2e_cached_project_previews_with_exit_zero(env):
    _put(env, [_project("/home/u/a", progress="PROGRESS-MARK")], [])
    code, out, err = _preview(env, "/home/u/a")
    assert code == 0, err
    out = ui.strip_ansi(out)
    assert HOST + ":/home/u/a" in out
    assert "最近 6 小时" not in out
    assert "(not in cache)" not in out
    assert "该项目还没采集" not in out


def test_e2e_cached_project_with_live_shows_last_6h(env):
    _put(env, [_project("/home/u/a")], [_now_session("/home/u/a", last_reply="UNIQUE-WORDS")])
    code, out, err = _preview(env, "/home/u/a")
    assert code == 0, err
    out = ui.strip_ansi(out)
    assert "最近 6 小时" in out and "UNIQUE-WORDS" in out
    assert "该项目还没采集" not in out


def test_e2e_unknown_project_says_not_in_cache(env):
    _put(env, [_project("/home/u/a")], [_now_session("/home/u/a")])
    code, out, err = _preview(env, "/nowhere")
    assert code == 0, err
    assert "(not in cache)" in out


def test_e2e_escape_sequences_never_reach_output(env):
    evil = "\x1b[31mRED\x1b]0;title\x07 tail"
    _put(env, [_project("/home/u/a", progress=evil)],
         [_now_session("/home/u/a", last_reply=evil, title=evil, name=evil)])
    code, out, err = _preview(env, "/home/u/a")
    assert code == 0, err
    # the preview may colour its own text, but no ESC byte may come from the data: every ESC
    # must start a plain SGR sequence ("ESC [ digits/; m")
    import re
    stray = re.sub(r"\x1b\[[0-9;]*m", "", out)
    assert "\x1b" not in stray
    assert "]0;title" not in out and "\x07" not in out


def test_e2e_escape_in_live_only_project_never_reaches_output(env):
    evil = "\x1b[2Jwipe\x1b]0;t\x07"
    _put(env, [], [_now_session("/home/u/new", last_reply=evil)])
    code, out, err = _preview(env, "/home/u/new")
    assert code == 0, err
    import re
    assert "\x1b" not in re.sub(r"\x1b\[[0-9;]*m", "", out)
    assert "\x1b[2J" not in out and "\x07" not in out
