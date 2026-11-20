"""Independent spec tests for T10: attach judges "exited" like the agent (only a trailing
`(EXITED ...)` marker counts) and a session name starting with `-` never reaches zellij.
A stub `zellij` (script in a tmp dir, first on PATH) records every argument; the shell
commands that opener builds are really run with bash against the stubs.  No real zellij."""
import os
import subprocess

import pytest

from projdeck import cli, opener, ui

SEP = "\x1f"
EXITED = "%s [Created 2h ago] (EXITED - attach to resurrect)"
LIVE = "%s [Created 1h ago]"

ZELLIJ_STUB = """#!/bin/sh
{ for a in "$@"; do printf '%s\\037' "$a"; done; printf '\\n'; } >> "$ZLOG"
case "$1" in
  list-sessions) cat "$ZLIST"; exit 0;;
esac
exit 0
"""
CLAUDE_STUB = """#!/bin/sh
echo "$PWD|$*" >> "$CLOG"
exit 0
"""
SHELL_STUB = """#!/bin/sh
if [ "$1" = -lic ]; then sh -c "$2"; fi
exit 0
"""


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in (("zellij", ZELLIJ_STUB), ("claude", CLAUDE_STUB), ("fakeshell", SHELL_STUB)):
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        self.zlog, self.clog, self.zlist = tmp_path / "zlog", tmp_path / "clog", tmp_path / "zlist"
        for p in (self.zlog, self.clog, self.zlist):
            p.write_text("")
        self.home = tmp_path / "hhome"
        self.home.mkdir()
        monkeypatch.delenv("ZELLIJ", raising=False)
        monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
        monkeypatch.setenv("PATH", os.pathsep.join(
            [str(self.bin), os.environ["PROJ_STUB_DIRS"], "/usr/bin", "/bin"]))
        monkeypatch.setenv("SHELL", str(self.bin / "fakeshell"))
        monkeypatch.setenv("ZLOG", str(self.zlog))
        monkeypatch.setenv("CLOG", str(self.clog))
        monkeypatch.setenv("ZLIST", str(self.zlist))
        self.opened, self.launched, self.tabs = [], [], []
        monkeypatch.setattr(opener, "open_command", lambda *a, **k: self.opened.append((a, k)) or "inline")
        monkeypatch.setattr(cli, "launch", lambda r, path, s=None: self.launched.append(path) or "inline")
        monkeypatch.setattr(cli, "log_event", lambda *a, **k: None)
        monkeypatch.setattr(opener, "zellij_new_tab", lambda path, name, *a, **k: self.tabs.append(path))
        monkeypatch.setattr(ui, "pick_live", lambda live, name: live[0])
        self.owned = {}
        monkeypatch.setattr(cli.agent, "zellij_by_project", lambda paths: dict(self.owned))

    def sessions(self, *lines):
        self.zlist.write_text("".join(l + "\n" for l in lines))

    def project(self, name="quant"):
        path = str(self.home / name)
        os.makedirs(path, exist_ok=True)
        return {"local": True, "ssh": None, "mux": "zellij", "host": "s-host", "path": path,
                "home": str(self.home)}

    def own(self, r, *names):
        self.owned = {r["path"]: [{"name": n, "claude": 1} for n in names]} if names else {}

    def zcalls(self):
        return [[a for a in l.split(SEP) if a != ""] for l in self.zlog.read_text().splitlines()]

    def mutating_calls(self):
        return [c for c in self.zcalls() if c[0] != "list-sessions"]

    def run(self, cmd):
        return subprocess.run(["bash", "-c", cmd], env=dict(os.environ), stdin=subprocess.DEVNULL,
                              timeout=30, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def claude_calls(self):
        return self.clog.read_text().splitlines()


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def attach_and_run(env, name, *lines):
    """Run the command zellij_attach(name, project path) builds; return the project path."""
    r = env.project("p")
    env.sessions(*lines)
    env.run(opener.zellij_attach(name, r["path"]))
    return r["path"]


# ------------------------------------------------------------------ AC1: exited judgement
def test_live_session_with_EXITED_in_its_name_is_attached(env):
    attach_and_run(env, "proj-EXITED", LIVE % "proj-EXITED")
    assert env.mutating_calls() == [["attach", "proj-EXITED"]]
    assert env.claude_calls() == []


def test_name_part_EXITED_with_parens_in_name_still_live(env):
    attach_and_run(env, "proj-(EXITED)", LIVE % "proj-(EXITED)")
    assert env.mutating_calls() == [["attach", "proj-(EXITED)"]]


def test_exited_marker_at_line_end_is_not_attached_and_picker_runs_in_cwd(env):
    path = attach_and_run(env, "proj-EXITED", EXITED % "proj-EXITED")
    assert env.mutating_calls() == []
    assert env.claude_calls() == ["%s|--resume" % path]


def test_exited_marker_with_trailing_whitespace_is_exited(env):
    path = attach_and_run(env, "proj-a", (EXITED % "proj-a") + "  ")
    assert env.mutating_calls() == []
    assert env.claude_calls() == ["%s|--resume" % path]


def test_marker_not_at_line_end_does_not_make_session_exited(env):
    attach_and_run(env, "proj-a", (EXITED % "proj-a") + " tail")
    assert env.mutating_calls() == [["attach", "proj-a"]]


def test_plain_live_session_is_attached_with_exact_args(env):
    attach_and_run(env, "research", LIVE % "research")
    assert env.mutating_calls() == [["attach", "research"]]


def test_vanished_session_runs_picker_not_attach(env):
    path = attach_and_run(env, "proj-gone", LIVE % "other")
    assert env.mutating_calls() == []
    assert env.claude_calls() == ["%s|--resume" % path]


def test_empty_session_list_runs_picker(env):
    path = attach_and_run(env, "proj-gone")
    assert env.mutating_calls() == []
    assert env.claude_calls() == ["%s|--resume" % path]


def test_name_match_is_exact_not_prefix_or_regex(env):
    path = attach_and_run(env, "proj-a", LIVE % "proj-ab", LIVE % "xproj-a", LIVE % "proj-a.b")
    assert env.mutating_calls() == []
    assert env.claude_calls() == ["%s|--resume" % path]
    env.zlog.write_text("")
    attach_and_run(env, "proj-a.b", LIVE % "proj-axb")
    assert env.mutating_calls() == []


def test_exited_twin_does_not_block_a_live_session_of_another_name(env):
    attach_and_run(env, "proj-b", EXITED % "proj-a", LIVE % "proj-b")
    assert env.mutating_calls() == [["attach", "proj-b"]]


def test_attach_command_never_deletes_anything(env):
    attach_and_run(env, "proj-a", EXITED % "proj-a")
    attach_and_run(env, "proj-b", LIVE % "proj-b")
    assert not any(c[0] == "delete-session" for c in env.zcalls())


# ------------------------------------------------------------------ AC2: names starting with -
@pytest.mark.parametrize("name", ["-x", "-", "--", "--help", "-proj-a", "-EXITED"])
def test_zellij_attach_rejects_option_like_names(name):
    with pytest.raises(ValueError):
        opener.zellij_attach(name, "/home/u/p")


@pytest.mark.parametrize("name", ["proj-a", "a-", "x-y-z", "proj--a", "research"])
def test_zellij_attach_accepts_names_with_inner_or_trailing_dash(name):
    cmd = opener.zellij_attach(name, "/home/u/p")
    assert isinstance(cmd, str) and name in cmd


def test_open_project_with_only_dash_session_returns_hint_and_runs_nothing(env):
    r = env.project()
    env.sessions(LIVE % "-x")
    env.own(r, "-x")
    out = cli.open_project([r], r)
    assert out[0] is None
    assert isinstance(out[1], str) and out[1].strip()
    assert "-x" in out[1] or "-" in out[1]
    assert env.opened == [] and env.launched == [] and env.tabs == []
    assert env.mutating_calls() == []
    for call in env.zcalls():
        assert "-x" not in call


def test_dash_session_name_in_no_zellij_argument_even_when_listed(env):
    r = env.project()
    env.sessions(LIVE % "-x", LIVE % "--help")
    env.own(r, "--help")
    out = cli.open_project([r], r)
    assert out[0] is None
    assert env.opened == [] and env.launched == []
    for call in env.zcalls():
        assert not any(a in ("-x", "--help") for a in call)


def test_open_project_ordinary_live_session_still_attaches(env):
    r = env.project()
    env.sessions(LIVE % "research")
    env.own(r, "research")
    out = cli.open_project([r], r)
    assert out[0] == "inline"
    assert len(env.opened) == 1
    cmd = env.opened[0][0][0]
    env.run(cmd)
    assert env.mutating_calls() == [["attach", "research"]]


def test_open_project_live_proj_EXITED_is_attached_via_enter(env):
    r = env.project("EXITED")
    env.sessions(LIVE % "proj-EXITED")
    env.own(r)
    out = cli.open_project([r], r)
    assert out[0] == "inline"
    env.run(env.opened[0][0][0])
    assert env.mutating_calls() == [["attach", "proj-EXITED"]]


# ------------------------------------------------------------------ deletion boundary unchanged
def deletes(env):
    return [c for c in env.zcalls() if c[0] == "delete-session"]


def test_only_exact_exited_proj_name_is_deleted(env):
    r = env.project("quant")
    env.sessions(EXITED % "proj-quant", EXITED % "quant", EXITED % "proj-quant2",
                 EXITED % "xproj-quant", EXITED % "proj-other")
    env.own(r)
    out = cli.open_project([r], r)
    assert out[0] == "inline"
    env.run(env.opened[0][0][0])
    assert deletes(env) == [["delete-session", "proj-quant"]]


@pytest.mark.parametrize("lines", [
    [EXITED % "quant", EXITED % "proj-quant2", EXITED % "xproj-quant"],
    [LIVE % "proj-quant"],
    [],
])
def test_nothing_deleted_without_an_exited_exact_proj_session(env, lines):
    r = env.project("quant")
    env.sessions(*lines)
    env.own(r)
    cli.open_project([r], r)
    for o in env.opened:
        env.run(o[0][0])
    assert deletes(env) == []


def test_live_proj_session_is_never_deleted_even_with_exited_siblings(env):
    r = env.project("quant")
    env.sessions(LIVE % "proj-quant", EXITED % "quant", EXITED % "proj-quant2")
    env.own(r)
    cli.open_project([r], r)
    for o in env.opened:
        env.run(o[0][0])
    assert deletes(env) == []
