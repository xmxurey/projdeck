"""Independent spec tests for T31: Enter creates a `proj-<project>` zellij session when the
project has no live one (D14, D15), plus the four small fixes carried over from T28.
Written from brief.md, rulings.md and design 3.12 only.  A stub `zellij` (a script in a tmp dir,
first on PATH) records every call; the returned shell commands are really run with bash against
the stubs.  No real zellij, claude or ssh is ever started."""
import copy
import json
import os
import re
import shlex
import subprocess
import time

import pytest

from projdeck import agent, cli, config, opener, sync, ui

NOW = int(time.time())
SEP = "\x1f"

EXITED = "%s [Created 2h ago] (EXITED - attach to resurrect)"
LIVE = "%s [Created 1h ago]"


# ------------------------------------------------------------------ stubs
ZELLIJ_STUB = """#!/bin/sh
{ for a in "$@"; do printf '%s\\037' "$a"; done; printf '\\n'; } >> "$ZLOG"
case "$1" in
  list-sessions) cat "$ZLIST"; exit 0;;
  delete-session) [ -n "$ZFAIL_DELETE" ] && exit 1; exit 0;;
  --session) [ -n "$ZFAIL_SESSION" ] && exit 1; exit 0;;
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
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in (("zellij", ZELLIJ_STUB), ("claude", CLAUDE_STUB), ("fakeshell", SHELL_STUB)):
            p = self.bin / name
            p.write_text(body)
            p.chmod(0o755)
        self.zlog = tmp_path / "zlog"
        self.clog = tmp_path / "clog"
        self.zlist = tmp_path / "zlist"
        for p in (self.zlog, self.clog, self.zlist):
            p.write_text("")
        self.home = tmp_path / "hhome"
        self.home.mkdir()
        self.mp = monkeypatch
        monkeypatch.delenv("PROJ_CACHE", raising=False)
        monkeypatch.delenv("ZELLIJ", raising=False)
        monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
        monkeypatch.delenv("ZFAIL_DELETE", raising=False)
        monkeypatch.delenv("ZFAIL_SESSION", raising=False)
        # our stand-ins first, then conftest's stand-ins (tmux, ssh, ...), then the system
        monkeypatch.setenv("PATH", os.pathsep.join(
            [str(self.bin), os.environ["PROJ_STUB_DIRS"], "/usr/bin", "/bin"]))
        monkeypatch.setenv("SHELL", str(self.bin / "fakeshell"))
        monkeypatch.setenv("ZLOG", str(self.zlog))
        monkeypatch.setenv("CLOG", str(self.clog))
        monkeypatch.setenv("ZLIST", str(self.zlist))
        self.opened = []
        self.launched = []
        self.logged = []
        self.tabs = []
        self.picked = []

        def open_command(*a, **k):
            self.opened.append((a, k))
            return "inline"
        monkeypatch.setattr(opener, "open_command", open_command)
        monkeypatch.setattr(cli, "launch", lambda r, path, s=None: self.launched.append(path) or "inline")
        monkeypatch.setattr(cli, "log_event", lambda *a, **k: self.logged.append((a, k)))
        monkeypatch.setattr(opener, "zellij_new_tab", lambda path, name, *a, **k: self.tabs.append(path))
        monkeypatch.setattr(ui, "pick_live", lambda live, name: self.picked.append(name) or live[0])
        self.owned = {}
        monkeypatch.setattr(cli.agent, "zellij_by_project", lambda paths: dict(self.owned))

    def sessions(self, *lines):
        self.zlist.write_text("".join(l + "\n" for l in lines))

    def project(self, name="quant", mux="zellij", local=True, ssh=None, mkdir=True):
        path = str(self.home / name)
        if mkdir:
            os.makedirs(path, exist_ok=True)
        return {"local": local, "ssh": ssh, "mux": mux, "host": "s-host", "path": path,
                "home": str(self.home)}

    def own(self, r, *names):
        self.owned = {r["path"]: [{"name": n, "claude": 1} for n in names]} if names else {}

    def open(self, r):
        return cli.open_project([r], r)

    def zcalls(self):
        out = []
        for line in self.zlog.read_text().splitlines():
            out.append([a for a in line.split(SEP) if a != ""])
        return out

    def run_command(self, cmd, **env):
        e = dict(os.environ, **env)
        r = subprocess.run(["bash", "-c", cmd], env=e, stdin=subprocess.DEVNULL, timeout=30,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        return r

    def claude_calls(self):
        return [l for l in self.clog.read_text().splitlines()]

    def layout_dir(self):
        return os.path.join(sync.cache_dir(), "zellij")

    def kdl_files(self):
        out = []
        for root, _d, files in os.walk(str(self.home.parent)):
            out += [os.path.join(root, f) for f in files if f.endswith(".kdl")]
        return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def command_of(env_):
    assert len(env_.opened) == 1, "open_command must be called exactly once"
    (args, kwargs) = env_.opened[0]
    return args[0], kwargs, args


def layout_path_in(cmd):
    toks = [t.rstrip(";") for t in shlex.split(cmd) if ".kdl" in t]
    assert toks, "the command must reference the layout file: %r" % cmd
    return toks[0]


def kdl_strings(text):
    out = []
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', text):
        out.append(re.sub(r"\\(.)", lambda mm: mm.group(1), m.group(1)))
    return out


HOME = "/home/u"


def sname(given):
    """Session name of the project called `given` (relative to HOME) under HOME."""
    return opener.proj_session_name(given, HOME + "/" + given, HOME)


# ------------------------------------------------------------------ AC1: naming
@pytest.mark.parametrize("given,expected", [
    ("quant", "proj-quant"),
    ("dev-process-core", "proj-dev-process-core"),
])
def test_name_examples(given, expected):
    assert sname(given) == expected


@pytest.mark.parametrize("given", ["中文", "...", "---", "./-"])
def test_empty_after_normalising_gets_project_plus_4_hex(given):
    n = sname(given)
    assert re.fullmatch(r"proj-project-[0-9a-f]{4}", n), n
    assert n == sname(given)


def test_literally_empty_input_still_gives_a_valid_stable_name():
    n = sname("")
    assert re.fullmatch(r"proj-project(-[0-9a-f]{4})?", n), n
    assert n == sname("")


def test_lossless_names_get_no_suffix():
    assert sname("quant") == "proj-quant"
    assert sname("dev-process-core") == "proj-dev-process-core"
    assert sname("a.b_c-d") == "proj-a.b_c-d"


def test_colliding_lossy_inputs_get_different_names():
    a, b = sname("dev/a b"), sname("dev/a-b")
    assert a != b
    assert re.fullmatch(r"proj-dev-a-b-[0-9a-f]{4}", b), b
    assert re.fullmatch(r"proj-dev-a-b-[0-9a-f]{4}", a), a
    c, d = sname("dev/中文"), sname("dev/日本")
    assert c != d


def test_same_input_same_name_many_times():
    for given in ("dev/a b", "中文", "x" * 80, "quant"):
        assert len({sname(given) for _ in range(5)}) == 1


@pytest.mark.parametrize("given", [".a b/中文", "a b", "x/y/z", "--lead", "..hidden", "é", 'q"uote', "a\\b",
                                   "a" * 100, "x\ny", "\x1b[31mred", "..." + "b" * 50])
def test_name_charset_leading_and_length(given):
    n = sname(given)
    assert n.startswith("proj-")
    rest = n[len("proj-"):]
    assert rest and re.fullmatch(r"[A-Za-z0-9._-]+", rest), n
    assert rest[0] not in ".-", n
    assert len(rest) <= 32, n


def test_truncated_name_gets_a_suffix_and_stays_within_32():
    for given in ("a" * 40, "---" + "b" * 40):
        n = sname(given)
        rest = n[len("proj-"):]
        assert len(rest) <= 32, n
        assert re.fullmatch(r"[ab]+-[0-9a-f]{4}", rest), n
    assert sname("a" * 40) != sname("a" * 41)


def test_name_is_deterministic_and_distinguishes_nested_projects():
    for given in ("dev/process-core", ".a b/中文", "quant"):
        assert sname(given) == sname(given)
    assert sname("a/b") != sname("b")


# ------------------------------------------------------------------ AC2: create / replace
def test_create_without_any_same_name_session(env):
    r = env.project()
    env.sessions(EXITED % "quant", EXITED % "dao", LIVE % "research")
    out = env.open(r)
    assert out[0] == "inline"
    cmd, kwargs, args = command_of(env)
    assert kwargs.get("iterm") is False
    assert "zellij --session proj-quant --new-session-with-layout" in cmd
    assert "delete-session" not in cmd
    assert env.launched == []
    assert len(env.logged) == 1
    a, k = env.logged[0]
    assert a[0] == "open" and k.get("created") == "proj-quant"
    assert "replaced_exited" not in k and "attach" not in k


def test_replace_exited_proj_session_deletes_first_then_creates(env):
    r = env.project()
    env.sessions(EXITED % "proj-quant", EXITED % "quant", EXITED % "dao")
    out = env.open(r)
    assert out[0] == "inline"
    cmd, kwargs, args = command_of(env)
    assert kwargs.get("iterm") is False
    d = cmd.index("zellij delete-session proj-quant")
    c = cmd.index("zellij --session proj-quant --new-session-with-layout")
    assert d < c
    assert "--force" not in cmd
    assert cmd.count("delete-session") == 1
    a, k = env.logged[0]
    assert a[0] == "open"
    assert k.get("created") == "proj-quant" and k.get("replaced_exited") is True


def test_live_proj_session_of_this_project_is_attached_not_recreated(env):
    r = env.project()
    env.sessions(LIVE % "proj-quant")
    env.own(r, "proj-quant")
    env.open(r)
    cmd, kwargs, args = command_of(env)
    assert "attach proj-quant" in cmd
    assert "delete-session" not in cmd and "--new-session-with-layout" not in cmd


def test_layout_file_has_project_cwd_and_claude_resume(env):
    r = env.project()
    env.sessions(EXITED % "proj-quant")
    env.open(r)
    cmd, _k, _a = command_of(env)
    lp = layout_path_in(cmd)
    assert os.path.basename(lp) == "proj-quant.kdl"
    assert os.path.abspath(lp).startswith(os.path.abspath(env.layout_dir()) + os.sep)
    text = open(lp).read()
    cwd = re.search(r'cwd\s*=?\s*"((?:[^"\\]|\\.)*)"', text)
    assert cwd, text
    assert re.sub(r"\\(.)", r"\1", cwd.group(1)) == r["path"]
    assert any("claude --resume" in s for s in kdl_strings(text))


@pytest.mark.parametrize("dirname", ['a b', 'q"uote', 'back\\slash', 'mix "a\\b" c'])
def test_layout_escapes_kdl_strings(env, dirname):
    r = env.project(dirname)
    env.sessions()
    env.open(r)
    cmd, _k, _a = command_of(env)
    text = open(layout_path_in(cmd)).read()
    cwd = re.search(r'cwd\s*=?\s*"((?:[^"\\]|\\.)*)"', text)
    assert cwd, text
    assert re.sub(r"\\(.)", r"\1", cwd.group(1)) == r["path"]
    # no unescaped quote may terminate a string early: every quote pairs up
    stripped = re.sub(r'\\.', "", text)
    assert stripped.count('"') % 2 == 0


def test_layout_filename_is_determined_by_session_name_and_replaced_in_place(env):
    r = env.project()
    env.sessions()
    env.open(r)
    p1 = layout_path_in(command_of(env)[0])
    env.opened.clear()
    env.open(r)
    p2 = layout_path_in(command_of(env)[0])
    assert p1 == p2 and os.path.basename(p1) == "proj-quant.kdl"
    leftovers = [f for f in os.listdir(os.path.dirname(p1)) if f != "proj-quant.kdl"]
    assert leftovers == []


def test_layout_is_written_with_os_replace(env, monkeypatch):
    real = os.replace
    seen = []

    def rec(src, dst, *a, **k):
        seen.append((str(src), str(dst)))
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", rec)
    r = env.project()
    env.sessions()
    env.open(r)
    lp = layout_path_in(command_of(env)[0])
    hits = [s for s in seen if s[1] == lp]
    assert hits, "layout must be put in place with os.replace (atomic write)"
    assert hits[0][0] != lp
    assert os.path.isfile(lp)


def test_layout_failing_midway_falls_back_and_leaves_no_final_file(env, monkeypatch, capsys):
    def boom(src, dst, *a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", boom)
    r = env.project()
    env.sessions()
    out = env.open(r)
    assert out[0] == "inline"
    assert env.launched == [r["path"]] and env.opened == []
    assert not any(f.endswith("proj-quant.kdl") for f in env.kdl_files())
    err_lines = [l for l in capsys.readouterr().err.splitlines() if l.strip()]
    assert len(err_lines) == 1, err_lines
    assert "No space left on device" in err_lines[0]
    assert not any("created" in k for _a, k in env.logged)


# ------------------------------------------------------------------ AC3: never touch the user's sessions
SCENARIOS = {
    "exited_proj_and_user": dict(lines=[EXITED % "proj-quant", EXITED % "quant", EXITED % "dao"], own=()),
    "no_proj_only_user_exited": dict(lines=[EXITED % "quant", EXITED % "dao"], own=()),
    "user_live_and_exited": dict(lines=[LIVE % "research", EXITED % "quant", EXITED % "dao"], own=()),
    "empty": dict(lines=[], own=()),
    "proj_live_unowned": dict(lines=[LIVE % "proj-quant", EXITED % "quant", EXITED % "dao"], own=()),
    "proj_exited_other_owned_live": dict(lines=[EXITED % "proj-quant", LIVE % "research", EXITED % "quant",
                                                EXITED % "dao"], own=("research",)),
    "two_owned_live": dict(lines=[LIVE % "research", LIVE % "poly", EXITED % "quant", EXITED % "dao",
                                  EXITED % "proj-quant"], own=("research", "poly")),
    "proj_live_owned": dict(lines=[LIVE % "proj-quant", EXITED % "quant", EXITED % "dao"], own=("proj-quant",)),
    "inside_zellij_no_live": dict(lines=[EXITED % "proj-quant", EXITED % "quant", EXITED % "dao"], own=(),
                                  inside=True),
    "inside_zellij_live": dict(lines=[LIVE % "research", EXITED % "quant", EXITED % "dao"], own=("research",),
                               inside=True),
}


def exited_names(lines):
    return {l.split()[0] for l in lines if "EXITED" in l}


@pytest.mark.parametrize("fail", [None, "ZFAIL_DELETE", "ZFAIL_SESSION"])
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_no_zellij_call_names_a_foreign_or_exited_session(env, monkeypatch, scenario, fail):
    sc = SCENARIOS[scenario]
    r = env.project()
    env.sessions(*sc["lines"])
    env.own(r, *sc["own"])
    if sc.get("inside"):
        monkeypatch.setenv("ZELLIJ", "0")
        monkeypatch.setenv("ZELLIJ_SESSION_NAME", "poly")
    if fail:
        monkeypatch.setenv(fail, "1")
    env.open(r)
    for (args, kwargs) in env.opened:
        res = env.run_command(args[0])
        assert res.returncode == 0, res.stderr
    forbidden_names = {"quant", "dao"}
    ex = exited_names(sc["lines"])
    for call in env.zcalls():
        if call and call[0] == "list-sessions":
            continue
        assert not (set(call) & forbidden_names), call
        if call and call[0] == "attach":
            assert call[1] not in ex, "attached an exited session: %r" % (call,)
            assert call[1] in {n for n in (l.split()[0] for l in sc["lines"]) if n not in ex}
        if call and call[0] == "delete-session":
            assert call == ["delete-session", "proj-quant"], call
            assert "proj-quant" in ex
        if call and call[0] == "--session":
            assert call[1] == "proj-quant"


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_user_exited_names_never_in_any_returned_command(env, monkeypatch, scenario):
    sc = SCENARIOS[scenario]
    r = env.project()
    env.sessions(*sc["lines"])
    env.own(r, *sc["own"])
    if sc.get("inside"):
        monkeypatch.setenv("ZELLIJ", "0")
        monkeypatch.setenv("ZELLIJ_SESSION_NAME", "poly")
    env.open(r)
    for (args, _k) in env.opened:
        toks = shlex.split(args[0])
        for bad in ("quant", "dao"):
            assert bad not in toks, (bad, args[0])
        assert not re.search(r"(delete-session|attach|--session)\s+(quant|dao)\b", args[0])
        # the only delete-session ever emitted is for the exact proj- name
        for m in re.finditer(r"delete-session\s+(\S+)", args[0]):
            assert m.group(1) == "proj-quant"


def test_live_proj_session_not_owned_goes_through_zellij_attach_only(env):
    r = env.project()
    env.sessions(LIVE % "proj-quant", EXITED % "quant")
    env.own(r)
    out = env.open(r)
    assert out[0] == "inline"
    cmd, kwargs, _a = command_of(env)
    # the exact text, written out here rather than computed by the function under test
    assert cmd == (
        "if zellij list-sessions -n 2>/dev/null | "
        "awk -v n=proj-quant '$1 == n {r = substr($0, length($1) + 1); "
        "if (r !~ /\\(EXITED([^A-Za-z0-9_()][^()]*)?\\)[ \t\\r\\f\\v]*$/) f = 1} END {exit !f}'; "
        "then exec zellij attach proj-quant; "
        "else cd %s && \"$SHELL\" -lic 'claude --resume'; exec \"$SHELL\" -l; fi" % shlex.quote(r["path"]))
    assert "delete-session" not in cmd and "--new-session-with-layout" not in cmd
    res = env.run_command(cmd)
    calls = [c for c in env.zcalls() if c[0] != "list-sessions"]
    assert calls == [["attach", "proj-quant"]]
    assert env.launched == []


def test_exited_proj_session_is_never_attached_even_if_other_session_live(env):
    r = env.project()
    env.sessions(EXITED % "proj-quant", LIVE % "dao")
    env.own(r)
    env.open(r)
    cmd, _k, _a = command_of(env)
    env.run_command(cmd)
    assert not any(c[0] == "attach" for c in env.zcalls())


def test_opener_never_emits_delete_for_a_name_that_is_not_the_proj_name(env, tmp_path):
    """Safety by structure (ruling 5): even a direct call with a user-style name must not produce
    `delete-session quant` (it may refuse or ignore the flag, but never emit it)."""
    try:
        cmd = opener.zellij_new_session(str(env.home), "quant", str(tmp_path / "lay"), True)
    except (ValueError, AssertionError):
        return
    assert not re.search(r"delete-session\s+quant\b", cmd)


def test_zellij_new_session_returns_command_and_writes_layout(env, tmp_path):
    lay = tmp_path / "lay"
    cmd = opener.zellij_new_session(str(env.home), "proj-x", str(lay), True)
    assert "zellij delete-session proj-x" in cmd and "zellij --session proj-x --new-session-with-layout" in cmd
    assert cmd.index("delete-session") < cmd.index("--new-session-with-layout")
    assert (lay / "proj-x.kdl").is_file()
    cmd2 = opener.zellij_new_session(str(env.home), "proj-x", str(lay), False)
    assert "delete-session" not in cmd2


# ------------------------------------------------------------------ AC4: fallbacks
def test_zellij_missing_from_path_runs_claude_inline_and_writes_no_layout(env, monkeypatch, tmp_path):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    r = env.project()
    out = env.open(r)
    assert out[0] == "inline"
    assert env.launched == [r["path"]]
    assert env.opened == []
    assert env.kdl_files() == []
    assert env.zcalls() == []
    assert not any("created" in k for _a, k in env.logged)


def test_zellij_timeout_runs_claude_inline(env, monkeypatch):
    # the timeout is a default argument of agent.zellij_session_list (run, timeout)
    run_default, _timeout = agent.zellij_session_list.__defaults__
    monkeypatch.setattr(agent.zellij_session_list, "__defaults__", (run_default, 0.3))
    (env.bin / "zellij").write_text('#!/bin/sh\nexec sleep 30\n')
    r = env.project()
    t0 = time.time()
    out = env.open(r)
    assert time.time() - t0 < 3
    assert out[0] == "inline"
    assert env.launched == [r["path"]] and env.opened == []
    assert env.kdl_files() == []


def test_zellij_refusing_to_execute_falls_back(env):
    (env.bin / "zellij").write_text("#!/nonexistent/interp\n")
    r = env.project()
    out = env.open(r)
    assert out[0] == "inline" and env.launched == [r["path"]] and env.opened == []


def test_unwritable_layout_dir_falls_back_with_one_stderr_line(env, capsys):
    ld = env.layout_dir()
    os.makedirs(os.path.dirname(ld), exist_ok=True)
    open(ld, "w").close()  # a regular file where the directory should be
    r = env.project()
    env.sessions(EXITED % "proj-quant")
    out = env.open(r)
    assert out[0] == "inline"
    assert env.launched == [r["path"]] and env.opened == []
    assert env.kdl_files() == []
    err = capsys.readouterr().err
    assert len([l for l in err.splitlines() if l.strip()]) == 1
    assert not any(k.get("created") for _a, k in env.logged)
    assert not any(c[0] == "delete-session" for c in env.zcalls())


def test_unwritable_layout_dir_stderr_text_is_sanitised(env, capsys):
    ld = env.layout_dir()
    os.makedirs(os.path.dirname(ld), exist_ok=True)
    open(ld, "w").close()
    r = env.project("q\x1b[31mx\x07y")
    env.sessions()
    env.open(r)
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\x07" not in err
    assert len(err.strip().splitlines()) == 1


def test_unwritable_layout_dir_notice_is_sanitised_too(env, capsys):
    ld = env.layout_dir()
    os.makedirs(os.path.dirname(ld), exist_ok=True)
    open(ld, "w").close()
    r = env.project("q\x1b[31mx")
    env.sessions()
    out = env.open(r)
    assert out[1] is None
    err = capsys.readouterr().err
    assert len([l for l in err.splitlines() if l.strip()]) == 1
    assert "\x1b" not in err


def _prepare_executable(env, tmp_path, lines):
    r = env.project()
    env.sessions(*lines)
    env.open(r)
    cmd, _k, _a = command_of(env)
    return r, cmd


def test_executed_create_runs_zellij_and_not_the_fallback(env, tmp_path):
    r, cmd = _prepare_executable(env, tmp_path, [EXITED % "proj-quant", EXITED % "quant"])
    res = env.run_command(cmd)
    assert res.returncode == 0, res.stderr
    calls = [c for c in env.zcalls() if c[0] != "list-sessions"]
    assert calls[0] == ["delete-session", "proj-quant"]
    assert calls[1][:3] == ["--session", "proj-quant", "--new-session-with-layout"]
    assert calls[1][3] == layout_path_in(cmd)
    assert len(calls) == 2
    assert env.claude_calls() == []


def test_executed_create_without_exited_has_no_delete(env, tmp_path):
    r, cmd = _prepare_executable(env, tmp_path, [EXITED % "quant"])
    env.run_command(cmd)
    calls = [c for c in env.zcalls() if c[0] != "list-sessions"]
    assert [c[0] for c in calls] == ["--session"]
    assert env.claude_calls() == []


def test_failing_delete_falls_back_to_inline_claude_and_skips_creation(env, tmp_path):
    r, cmd = _prepare_executable(env, tmp_path, [EXITED % "proj-quant"])
    res = env.run_command(cmd, ZFAIL_DELETE="1")
    assert res.returncode == 0, res.stderr
    calls = [c for c in env.zcalls() if c[0] != "list-sessions"]
    assert [c[0] for c in calls] == ["delete-session"], "a failed delete must not be followed by a create"
    cc = env.claude_calls()
    assert len(cc) == 1 and cc[0].endswith("|--resume")
    assert os.path.realpath(cc[0].split("|")[0]) == os.path.realpath(r["path"])


def test_failing_new_session_falls_back_to_inline_claude(env, tmp_path):
    r, cmd = _prepare_executable(env, tmp_path, [EXITED % "proj-quant"])
    res = env.run_command(cmd, ZFAIL_SESSION="1")
    assert res.returncode == 0, res.stderr
    calls = [c for c in env.zcalls() if c[0] != "list-sessions"]
    assert [c[0] for c in calls] == ["delete-session", "--session"]
    cc = env.claude_calls()
    assert len(cc) == 1 and cc[0].endswith("|--resume")
    assert os.path.realpath(cc[0].split("|")[0]) == os.path.realpath(r["path"])


def test_failing_new_session_without_delete_also_falls_back(env, tmp_path):
    r, cmd = _prepare_executable(env, tmp_path, [])
    env.run_command(cmd, ZFAIL_SESSION="1")
    cc = env.claude_calls()
    assert len(cc) == 1 and cc[0].endswith("|--resume")


def test_fallback_when_project_path_has_odd_characters_still_quotes(env, tmp_path):
    r = env.project("sp ace's")
    env.sessions()
    env.open(r)
    cmd, _k, _a = command_of(env)
    res = env.run_command(cmd, ZFAIL_SESSION="1")
    assert res.returncode == 0, res.stderr
    cc = env.claude_calls()
    assert len(cc) == 1 and os.path.realpath(cc[0].split("|")[0]) == os.path.realpath(r["path"])


# ------------------------------------------------------------------ AC5: unchanged paths
def test_one_live_session_is_attached(env):
    r = env.project()
    env.sessions(LIVE % "research")
    env.own(r, "research")
    out = env.open(r)
    assert out == ("inline", None)
    cmd, kwargs, args = command_of(env)
    assert "attach research" in cmd and kwargs.get("iterm") is False
    assert "--new-session-with-layout" not in cmd and "delete-session" not in cmd
    a, k = env.logged[0]
    assert k.get("attach") == "research" and "created" not in k


def test_several_live_sessions_show_the_picker_first(env):
    r = env.project()
    env.sessions(LIVE % "research", LIVE % "poly")
    env.own(r, "research", "poly")
    env.open(r)
    assert len(env.picked) == 1
    cmd, _k, _a = command_of(env)
    assert "attach research" in cmd


def test_inside_zellij_with_live_session_only_tells(env, monkeypatch):
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "poly")
    r = env.project()
    env.sessions(LIVE % "research")
    env.own(r, "research")
    out = env.open(r)
    assert out[0] is None and "research" in out[1]
    assert env.opened == [] and env.launched == [] and env.tabs == []


def test_inside_zellij_without_live_session_opens_a_tab(env, monkeypatch):
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "poly")
    r = env.project()
    env.sessions(EXITED % "proj-quant")
    out = env.open(r)
    assert out[0] == "tab"
    assert env.tabs == [r["path"]]
    assert env.opened == [] and env.launched == []
    assert not any(c[0] in ("delete-session", "--session") for c in env.zcalls())


@pytest.mark.parametrize("kw", [
    dict(mux="tmux"), dict(mux=False), dict(mux="tmux", local=False, ssh="vm"),
    dict(mux=False, local=False, ssh="vm"),
])
def test_other_hosts_still_just_launch_and_never_touch_zellij(env, kw):
    r = env.project(**kw)
    env.sessions(EXITED % "proj-quant", LIVE % "research")
    out = env.open(r)
    assert out[0] == "inline"
    assert env.launched == [r["path"]]
    assert env.opened == [] and env.zcalls() == [] and env.layout_dir() not in str(env.kdl_files())
    assert env.kdl_files() == []
    assert not any("created" in k for _a, k in env.logged)


# ------------------------------------------------------------------ ruling 2: carried over from T28
def _cfg(**kw):
    c = copy.deepcopy(config.DEFAULTS)
    c["hosts"] = [{"name": "vm", "ssh": "vm"}]
    c.update(kw)
    return c


def _browse(monkeypatch, picks, cfg):
    cache = {"host": "vm", "projects": [{"path": "/p", "last_mtime": NOW - 100}]}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg_, now: [cache])
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg_: [cache])
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg_, now: [])
    it = iter(picks)
    seen = []

    def fake_pick(rows, now, notice=None, select=None, board=None):
        seen.append(notice)
        try:
            return next(it)
        except StopIteration:
            raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_project", fake_pick)
    monkeypatch.setattr(cli, "launch", lambda r, path, s=None: "iterm")
    assert cli.cmd_browse(cfg, None) == 0
    return seen


@pytest.mark.parametrize("bad", ["ftp://x/y", "javascript:alert(1)", "file:///etc/passwd", "dash.local:8787"])
def test_m4_non_http_dashboard_url_is_called_out_not_blamed_on_the_mac(env, monkeypatch, bad):
    monkeypatch.setattr(opener, "open_url", lambda url: False)
    seen = _browse(monkeypatch, [("ctrl-v", "vm", "/p")], _cfg(dashboard_url=bad))
    n = seen[1] or ""
    assert n
    assert "Mac" not in n and "浏览器里打开" not in n
    assert bad in n


def test_m4_http_url_without_display_keeps_the_open_in_browser_hint(env, monkeypatch):
    monkeypatch.setattr(opener, "open_url", lambda url: False)
    seen = _browse(monkeypatch, [("ctrl-v", "vm", "/p")], _cfg(dashboard_url="http://dash.example:1"))
    assert "http://dash.example:1" in (seen[1] or "")


def test_m3_claim_marker_survives_oserror_and_reports_a_problem(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("PROJ_CACHE", str(blocker / "sub"))  # makedirs under a regular file fails
    problems = []
    assert sync.claim_marker(".syncing-live", problems=problems) is False
    assert problems and all(isinstance(p, str) and p for p in problems)
    assert sync.claim_marker(".syncing-live") is False  # also without a problems list


def test_m3_claim_marker_normal_case_still_claims_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "c"))
    problems = []
    assert sync.claim_marker(".syncing-live", problems=problems) is True
    assert sync.claim_marker(".syncing-live", problems=problems) is False
    assert problems == []
