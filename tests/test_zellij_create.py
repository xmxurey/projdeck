"""T31: Enter creates proj-<project> zellij sessions (D14, D15). Only a stand-in zellij runs."""
import os
import re
import subprocess

import pytest

from projdeck import agent, cli, opener, sync, ui

LIST = (
    "proj-quant [Created 2h ago] (EXITED - attach to resurrect)\n"
    "quant [Created 3h ago] (EXITED - attach to resurrect)\n"
    "dao [Created 1h ago]\n")

FAKE_ZELLIJ = """#!/bin/sh
echo "$*" >> "$ZJ_LOG"
case "$1" in
  list-sessions) cat "$ZJ_LIST"; exit 0;;
  delete-session) [ -n "$ZJ_FAIL_DELETE" ] && exit 1; exit 0;;
  --session) [ -n "$ZJ_FAIL_SESSION" ] && exit 1; exit 0;;
esac
exit 0
"""

FAKE_SHELL = """#!/bin/sh
echo "SHELL $*" >> "$ZJ_LOG"
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A stand-in zellij first on PATH (calls recorded in env.log), a recording $SHELL,
    an isolated cache dir and a project directory."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    z = bindir / "zellij"
    z.write_text(FAKE_ZELLIJ)
    z.chmod(0o755)
    sh = tmp_path / "fakeshell"
    sh.write_text(FAKE_SHELL)
    sh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("SHELL", str(sh))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("ZJ_LOG", str(tmp_path / "zj.log"))
    monkeypatch.setenv("ZJ_LIST", str(tmp_path / "zj.list"))
    for k in ("ZELLIJ", "ZELLIJ_SESSION_NAME", "ZJ_FAIL_DELETE", "ZJ_FAIL_SESSION"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "zj.list").write_text(LIST)
    proj = tmp_path / "quant"
    proj.mkdir()
    monkeypatch.setattr(agent, "zellij_by_project", lambda paths: {})

    class E:
        pass
    e = E()
    e.tmp, e.proj = tmp_path, str(proj)
    e.row = {"local": True, "ssh": None, "mux": "zellij", "host": "s-host", "path": e.proj,
             "home": str(tmp_path)}
    e.set_list = lambda text: (tmp_path / "zj.list").write_text(text)
    e.calls = lambda: [l for l in (open(str(tmp_path / "zj.log")).read().splitlines()
                                   if (tmp_path / "zj.log").exists() else [])]
    e.layout = lambda name="proj-quant": (tmp_path / "cache" / "zellij" / (name + ".kdl"))
    return e


@pytest.fixture
def opened(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.opener, "open_command",
                        lambda cmd, title, run=None, iterm=None: seen.append((cmd, iterm)) or "inline")
    return seen


def open_it(e):
    return cli.open_project([e.row], e.row)


# ---- naming

@pytest.mark.parametrize("given,want", [
    ("quant", "proj-quant"),
    ("dev/process-core", "proj-dev-process-core"),
    ("", "proj-project"),
    ("a.b_c-d", "proj-a.b_c-d"),
])
def test_names(given, want):
    assert opener.proj_session_name(given) == want


def test_name_is_sanitised_bounded_and_stable():
    for given in (".a b/中文", "-x" * 40, "a" * 100, 'q"\\ $(x)'):
        n = opener.proj_session_name(given)
        part = n[len("proj-"):]
        assert n.startswith("proj-") and part and len(part) <= 32
        assert set(part) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
        assert part[0] not in ".-"
        assert opener.proj_session_name(given) == n


def test_lossy_names_get_a_short_hash_so_different_projects_do_not_collide():
    pairs = [("dev/a b", "dev/a-b"), ("dev/中文", "dev/日本"),
             ("dev/projdeck/.worktrees/2026-11-18-proj-dashboard-T31",
              "dev/projdeck/.worktrees/2026-11-18-proj-dashboard-T30")]
    assert re.search(r"-[0-9a-f]{4}$", opener.proj_session_name(pairs[1][1]))
    for a, b in pairs:
        na, nb = opener.proj_session_name(a), opener.proj_session_name(b)
        assert na != nb
        assert len(na) - len("proj-") <= 32 and len(nb) - len("proj-") <= 32
        assert re.search(r"-[0-9a-f]{4}$", na)  # the first of each pair is lossy
    assert opener.proj_session_name("dev/a b") == opener.proj_session_name("dev/a b")
    assert opener.proj_session_name("dev/a-b") == "proj-dev-a-b"  # lossless: unchanged


# ---- create, replace an exited session

def test_creates_session_and_replaces_exited_one(env, opened):
    out = open_it(env)
    assert out == ("inline", None)
    assert len(opened) == 1 and opened[0][1] is False
    cmd = opened[0][0]
    assert cmd.index("zellij delete-session proj-quant") < cmd.index(
        "zellij --session proj-quant --new-session-with-layout")
    assert "--force" not in cmd
    layout = env.layout().read_text()
    assert 'cwd="%s"' % env.proj in layout and "claude --resume" in layout
    ev = [e for e in cli.read_events(0) if e["action"] == "open"]
    assert len(ev) == 1 and ev[0]["created"] == "proj-quant" and ev[0]["replaced_exited"] is True


def test_no_same_name_session_means_no_delete(env, opened):
    env.set_list("quant [Created 3h ago] (EXITED - attach to resurrect)\ndao [Created 1h ago]\n")
    open_it(env)
    assert "delete-session" not in opened[0][0]
    ev = [e for e in cli.read_events(0) if e["action"] == "open"][0]
    assert ev["created"] == "proj-quant" and "replaced_exited" not in ev


def test_empty_session_list_creates(env, opened):
    env.set_list("")
    open_it(env)
    assert "--new-session-with-layout" in opened[0][0] and "delete-session" not in opened[0][0]


def test_layout_escapes_quotes_backslashes_and_spaces(env, opened, tmp_path):
    weird = tmp_path / 'my "q" proj\\x'
    weird.mkdir()
    env.row["path"] = str(weird)
    env.set_list("")
    open_it(env)
    name = opener.proj_session_name(ui.project_name(env.row))
    text = env.layout(name).read_text()
    assert 'cwd="%s"' % str(weird).replace("\\", "\\\\").replace('"', '\\"') in text
    assert 'exec \\"$SHELL\\" -l' in text


def test_layout_file_name_is_fixed_and_no_temp_left(env, opened):
    open_it(env)
    open_it(env)
    assert sorted(os.listdir(str(env.tmp / "cache" / "zellij"))) == ["proj-quant.kdl"]


# ---- nothing of the user's is ever touched

def test_running_the_command_never_names_a_user_session_or_attaches(env):
    ran = []
    real = cli.opener.open_command
    cli.opener.open_command = lambda cmd, title, run=subprocess.run, iterm=None: (
        ran.append(subprocess.run(cmd, shell=True)), "inline")[1]
    try:
        open_it(env)
    finally:
        cli.opener.open_command = real
    calls = env.calls()
    assert calls[0] == "list-sessions -n"
    assert calls[1] == "delete-session proj-quant"
    assert calls[2].startswith("--session proj-quant --new-session-with-layout ")
    assert len(calls) == 3
    for c in calls:
        words = c.split()
        assert "quant" not in words and "dao" not in words and "attach" not in words


def test_user_named_exited_session_is_never_deleted_even_if_same_project(env, opened):
    env.set_list("quant [Created 3h ago] (EXITED - attach to resurrect)\n")
    open_it(env)
    assert "delete-session" not in opened[0][0]


def test_delete_guard_refuses_a_name_that_is_not_a_proj_name(env, tmp_path):
    for bad in ("quant", "dao", "proj-", "proj-a b", "../proj-x", ""):
        with pytest.raises(ValueError):
            opener.zellij_new_session(env.proj, bad, str(tmp_path / "l"), True)
    assert not (tmp_path / "l").exists()


def test_session_state_only_matches_the_exact_name():
    s = [{"name": "proj-quant2", "exited": True}, {"name": "quant", "exited": True},
         {"name": "proj-quant", "exited": False}]
    assert opener.session_state(s, "proj-quant") == "live"
    assert opener.session_state(s[:2], "proj-quant") is None
    assert opener.session_state(s[:1] + [{"name": "proj-quant", "exited": True}], "proj-quant") == "exited"


def test_live_proj_session_of_another_project_is_attached_not_recreated(env, opened):
    env.set_list("proj-quant [Created 1h ago]\nquant [Created 3h ago] (EXITED - attach to resurrect)\n")
    open_it(env)
    cmd = opened[0][0]
    assert "zellij attach proj-quant" in cmd and "delete-session" not in cmd
    assert "--new-session-with-layout" not in cmd and not env.layout().exists()
    assert cli.read_events(0)[0]["attach"] == "proj-quant"


# ---- fall back

@pytest.fixture
def launched(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "launch", lambda r, path, s=None: seen.append(path) or "inline")
    return seen


def test_without_zellij_falls_back_to_launch(env, opened, launched, monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert open_it(env) == ("inline", None)
    assert launched == [env.proj] and opened == []
    assert not (tmp_path / "cache" / "zellij").exists()


def test_zellij_timeout_falls_back_to_launch(env, opened, launched, monkeypatch, tmp_path):
    orig = agent.zellij_session_list

    def slow(run=None, timeout=5):
        def boom(*a, **k):
            raise subprocess.TimeoutExpired("zellij", 5)
        return orig(run=boom, timeout=timeout)
    monkeypatch.setattr(cli.agent, "zellij_session_list", slow)
    open_it(env)
    assert launched == [env.proj] and opened == []
    assert not (tmp_path / "cache" / "zellij").exists()


def test_unwritable_layout_dir_falls_back_and_says_why(env, opened, launched, monkeypatch, capsys):
    (env.tmp / "cache").mkdir()
    (env.tmp / "cache" / "zellij").write_text("a file, not a directory")
    open_it(env)
    assert launched == [env.proj] and opened == []
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1 and "layout" in err


def test_error_text_on_stderr_is_sanitised(env, launched, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("bad \x1b[31m path\n/x")
    monkeypatch.setattr(cli.opener, "zellij_new_session", boom)
    open_it(env)
    err = capsys.readouterr().err
    assert "\x1b" not in err and len(err.strip().splitlines()) == 1


@pytest.mark.parametrize("flag", ["ZJ_FAIL_DELETE", "ZJ_FAIL_SESSION"])
def test_failing_zellij_command_ends_in_the_picker_here(env, flag, monkeypatch):
    monkeypatch.setenv(flag, "1")
    cmds = []
    monkeypatch.setattr(cli.opener, "open_command",
                        lambda cmd, title, run=None, iterm=None: cmds.append(cmd) or "inline")
    open_it(env)
    subprocess.run(cmds[0], shell=True)
    calls = env.calls()
    assert calls[-1] == "SHELL -lic claude --resume" or calls[-2:] == ["SHELL -lic claude --resume", "SHELL -l"]
    if flag == "ZJ_FAIL_DELETE":
        assert not any(c.startswith("--session") for c in calls)


# ---- unchanged paths

def test_inside_zellij_without_live_session_still_opens_a_tab(env, monkeypatch):
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "poly")
    tabs = []
    monkeypatch.setattr(cli.opener, "zellij_new_tab", lambda path, title: tabs.append(path))
    assert open_it(env)[0] == "tab" and tabs == [env.proj] and env.calls() == []


@pytest.mark.parametrize("patch", [{"mux": "tmux"}, {"mux": False}, {"local": False, "ssh": "vm"}])
def test_other_hosts_do_not_create_sessions(env, launched, patch):
    env.row.update(patch)
    open_it(env)
    assert launched == [env.proj] and env.calls() == []


def test_live_session_of_the_project_is_still_joined(env, opened, monkeypatch):
    monkeypatch.setattr(agent, "zellij_by_project",
                        lambda paths: {env.proj: [{"name": "research", "claude": 1}]})
    open_it(env)
    assert "zellij attach research" in opened[0][0] and "--new-session" not in opened[0][0]


# ---- layout shape, atomic write, odd paths (review round 1)

def kdl_unescape(lit):
    """The inverse of a KDL v1 string literal (enough for what the layout writer emits)."""
    body, out, i = lit[1:-1], [], 0
    table = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "\\": "\\", '"': '"'}
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
        elif body[i + 1] == "u":
            j = body.index("}", i)
            out.append(chr(int(body[i + 3:j], 16)))
            i = j + 1
        else:
            out.append(table[body[i + 1]])
            i += 2
    return "".join(out)


def test_layout_has_tab_bar_command_pane_that_closes_and_status_bar(env, opened):
    env.set_list("")
    open_it(env)
    lines = [l.strip() for l in env.layout().read_text().splitlines()]
    assert lines.index('pane size=1 borderless=true {') < lines.index('plugin location="zellij:tab-bar"')
    cmd = next(i for i, l in enumerate(lines) if l.startswith("pane cwd="))
    assert lines[cmd].endswith("close_on_exit=true {")
    assert lines.index('plugin location="zellij:tab-bar"') < cmd < lines.index(
        'plugin location="zellij:status-bar"')
    assert lines.index('pane size=2 borderless=true {') < lines.index('plugin location="zellij:status-bar"')


@pytest.mark.parametrize("dirname", ["line\nbreak", "tab\there", "bell\x07x", "中文 路径", "del\x7fx"])
def test_layout_cwd_round_trips_odd_characters(env, opened, tmp_path, dirname):
    d = tmp_path / dirname
    d.mkdir()
    env.row["path"] = str(d)
    env.set_list("")
    open_it(env)
    text = env.layout(opener.proj_session_name(ui.project_name(env.row))).read_text()
    lit = re.search(r'cwd=("(?:[^"\\]|\\.)*")', text).group(1)
    assert kdl_unescape(lit) == str(d)


def test_non_utf8_path_writes_layout_instead_of_crashing(env, tmp_path):
    raw = os.fsencode(str(tmp_path)) + b"/odd\xff"  # the directory itself is not needed
    path = os.fsdecode(raw)
    cmd = opener.zellij_new_session(path, "proj-odd", str(tmp_path / "l"), False)
    assert "--new-session-with-layout" in cmd
    assert os.listdir(str(tmp_path / "l")) == ["proj-odd.kdl"]


def test_create_session_falls_back_on_value_error_too(env, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli, "launch", lambda r, path, s=None: seen.append(path) or "inline")

    def boom(*a, **k):
        raise ValueError("bad \x1b[31mname")
    monkeypatch.setattr(cli.opener, "zellij_new_session", boom)
    open_it(env)
    err = capsys.readouterr().err
    assert seen == [env.proj] and "\x1b" not in err and len(err.strip().splitlines()) == 1


def test_interrupted_layout_write_leaves_no_temp_file(env, monkeypatch, tmp_path):
    def interrupt(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        opener.zellij_new_session(env.proj, "proj-quant", str(tmp_path / "l"), False)
    assert os.listdir(str(tmp_path / "l")) == []


def test_layout_and_cache_share_one_atomic_writer():
    assert opener._write_atomic is sync._write_atomic


def test_conftest_guards_stand_in_for_real_zellij_and_ssh(monkeypatch):
    """Without a test's own stand-in, zellij reads as unusable and ssh fails, both recorded."""
    assert agent.zellij_session_list() is None
    r = subprocess.run(["ssh", "somehost", "true"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode != 0
    calls = open(os.environ["PROJ_STUB_CALLS"]).read().splitlines()
    assert calls == ["zellij list-sessions -n", "ssh somehost true"]
