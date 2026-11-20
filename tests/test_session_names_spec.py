"""Independent spec tests for T09: zellij session names no longer collide across projects (D4).
Written from the brief only.  A stub `zellij` (script in a tmp dir, first on PATH) records every
call; no real zellij is ever started."""
import hashlib
import os
import re

import pytest

from projdeck import cli, opener, ui

HOME = "/home/u"
RX = re.compile(r"^proj-[A-Za-z0-9._-]+$")
SEP = "\x1f"

ZELLIJ_STUB = """#!/bin/sh
{ for a in "$@"; do printf '%s\\037' "$a"; done; printf '\\n'; } >> "$ZLOG"
case "$1" in
  list-sessions) cat "$ZLIST"; exit 0;;
esac
exit 0
"""


def h4(path):
    return hashlib.sha1(path.encode("utf-8", "surrogateescape")).hexdigest()[:4]


def name_of(path, home=HOME):
    return opener.proj_session_name(ui.project_name({"path": path, "home": home}), path, home)


SEVEN = [HOME + "/work/foo-bar", HOME + "/work-foo/bar", HOME + "/work/foo/bar",
         HOME + "/dev/projdeck", HOME + "/dev-projdeck", "/srv/x/app", "/opt/app"]


def test_seven_projects_distinct_and_valid():
    names = [name_of(p) for p in SEVEN]
    assert len(set(names)) == 7, names
    for n in names:
        assert RX.match(n), n


def test_stable_for_same_path():
    for p in SEVEN:
        assert name_of(p) == name_of(p)


def test_plain_top_level_has_no_hash():
    assert name_of(HOME + "/projdeck") == "proj-projdeck"
    assert name_of(HOME + "/quant") == "proj-quant"
    assert opener.proj_session_name("a.b_c-d", HOME + "/a.b_c-d", HOME) == "proj-a.b_c-d"


def test_slash_names_get_hash_of_absolute_path():
    p = HOME + "/dev/projdeck"
    assert name_of(p) == "proj-dev-projdeck-" + h4(p)
    p = HOME + "/work/foo/bar"
    assert name_of(p) == "proj-work-foo-bar-" + h4(p)


def test_dash_sibling_does_not_get_hash_but_differs():
    assert name_of(HOME + "/dev-projdeck") == "proj-dev-projdeck"
    assert name_of(HOME + "/dev/projdeck") != "proj-dev-projdeck"


@pytest.mark.parametrize("home", ["", None])
def test_home_empty_or_none_counts_as_outside(home):
    p = "/srv/x/app"
    n = opener.proj_session_name("app", p, home)
    assert n == "proj-app-" + h4(p)


def test_outside_home_gets_hash():
    for p in ("/srv/x/app", "/opt/app"):
        assert name_of(p) == "proj-app-" + h4(p)


def test_outside_home_even_when_name_matches_a_home_project():
    assert name_of("/opt/quant") != name_of(HOME + "/quant")
    assert name_of("/opt/quant") == "proj-quant-" + h4("/opt/quant")


def test_prefix_of_home_is_not_inside_home():
    p = "/home/user2/quant"  # starts with /home/u but is another directory
    assert name_of(p) == "proj-quant-" + h4(p)


def test_lossy_cleaning_gets_hash_and_distinguishes():
    a, b = HOME + "/a b", HOME + "/a-b"
    na, nb = name_of(a), name_of(b)
    assert na != nb
    assert na == "proj-a-b-" + h4(a)
    assert nb == "proj-a-b"
    assert RX.match(na) and RX.match(nb)


def test_non_ascii_and_symbols_valid_and_distinct():
    ps = [HOME + "/中文", HOME + "/日本", HOME + "/café", HOME + "/a;b", HOME + "/a$b"]
    names = [name_of(p) for p in ps]
    assert len(set(names)) == len(ps), names
    for n in names:
        assert RX.match(n), n


def test_long_names_valid_and_distinct_after_truncation():
    base = HOME + "/" + "x" * 300
    a, b = base + "1", base + "2"
    na, nb = name_of(a), name_of(b)
    assert RX.match(na) and RX.match(nb)
    assert na != nb
    assert len(na) < 300


def test_surrogate_paths_do_not_crash_and_are_distinct():
    a = HOME + "/caf\udce9"
    b = HOME + "/caf\udcea"
    na, nb = name_of(a), name_of(b)
    assert RX.match(na) and RX.match(nb)
    assert na != nb
    assert na == name_of(a)
    c = "/srv/\udcff\udcfe"
    assert name_of(c) == name_of(c) and RX.match(name_of(c))


def test_surrogate_hash_uses_surrogateescape_bytes():
    p = "/srv/x\udce9"
    assert name_of(p).endswith("-" + h4(p))


def test_empty_name_still_valid():
    n = opener.proj_session_name("", HOME + "/", HOME)
    assert RX.match(n), n


# ------------------------------------------------------------------ Enter end to end
@pytest.fixture
def env(tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    z = b / "zellij"
    z.write_text(ZELLIJ_STUB)
    z.chmod(0o755)
    for n in ("claude", "fakeshell"):
        s = b / n
        s.write_text("#!/bin/sh\nexit 0\n")
        s.chmod(0o755)
    for k in ("PROJ_CACHE", "ZELLIJ", "ZELLIJ_SESSION_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(b), os.environ["PROJ_STUB_DIRS"], "/usr/bin", "/bin"]))
    monkeypatch.setenv("SHELL", str(b / "fakeshell"))
    zlog, zlist = tmp_path / "zlog", tmp_path / "zlist"
    zlog.write_text("")
    zlist.write_text("")
    monkeypatch.setenv("ZLOG", str(zlog))
    monkeypatch.setenv("ZLIST", str(zlist))
    opened = []
    monkeypatch.setattr(opener, "open_command", lambda *a, **k: opened.append(a) or "inline")
    monkeypatch.setattr(cli, "launch", lambda r, path, s=None: "inline")
    monkeypatch.setattr(cli, "log_event", lambda *a, **k: None)
    monkeypatch.setattr(opener, "zellij_new_tab", lambda *a, **k: None)
    monkeypatch.setattr(ui, "pick_live", lambda live, name: live[0])
    monkeypatch.setattr(cli.agent, "zellij_by_project", lambda paths: {})
    home = tmp_path / "hhome"
    home.mkdir()

    class E:
        pass
    e = E()
    e.home, e.zlist, e.zlog, e.opened = home, zlist, zlog, opened

    def project(rel):
        path = str(home / rel)
        os.makedirs(path, exist_ok=True)
        return {"local": True, "ssh": None, "mux": "zellij", "host": "s-host", "path": path,
                "home": str(home)}
    e.project = project

    def go(r):
        cli.open_project([r], r)
        cmd = e.opened[-1][0]
        calls = [[a for a in l.split(SEP) if a] for l in zlog.read_text().splitlines()]
        return cmd, calls
    e.go = go
    return e


EXITED = "%s [Created 2h ago] (EXITED - attach to resurrect)"
LIVE = "%s [Created 1h ago]"


def targets(cmd, verb):
    return re.findall(r"zellij\s+(?:--session\s+)?%s\s+(\S+)" % verb, cmd)


def test_enter_does_not_touch_exited_legacy_name(env):
    r = env.project("dev/projdeck")
    env.zlist.write_text(EXITED % "proj-dev-projdeck" + "\n")
    cmd, calls = env.go(r)
    new = name_of(r["path"], r["home"])
    assert new != "proj-dev-projdeck" and new.startswith("proj-dev-projdeck-")
    assert "delete-session proj-dev-projdeck " not in cmd + " "
    assert "delete-session proj-dev-projdeck;" not in cmd
    assert not re.search(r"delete-session\s+'?proj-dev-projdeck'?(\s|;|&|$)", cmd), cmd
    assert not re.search(r"attach\s+'?proj-dev-projdeck'?(\s|;|&|$)", cmd), cmd
    assert not re.search(r"--session\s+'?proj-dev-projdeck'?(\s|;|&|$)", cmd), cmd
    assert new in cmd
    for c in calls:
        if c[0] in ("delete-session", "attach"):
            assert c[1] != "proj-dev-projdeck", calls


def test_enter_does_not_attach_live_legacy_name_of_other_project(env):
    r = env.project("dev/projdeck")
    env.zlist.write_text(LIVE % "proj-dev-projdeck" + "\n")
    cmd, calls = env.go(r)
    assert not re.search(r"attach\s+'?proj-dev-projdeck'?(\s|;|&|$)", cmd), cmd
    assert not re.search(r"--session\s+'?proj-dev-projdeck'?(\s|;|&|$)", cmd), cmd
    assert name_of(r["path"], r["home"]) in cmd
    assert not any(c[:2] == ["attach", "proj-dev-projdeck"] for c in calls)


def test_enter_in_dash_project_leaves_slash_projects_session_alone(env):
    r = env.project("dev-projdeck")  # legacy name proj-dev-projdeck is legitimately its own
    other = name_of(env.home.as_posix() + "/dev/projdeck", str(env.home))
    env.zlist.write_text(EXITED % other + "\n")
    cmd, calls = env.go(r)
    assert not re.search(r"delete-session\s+'?%s'?(\s|;|&|$)" % re.escape(other), cmd), cmd
    assert not re.search(r"attach\s+'?%s'?(\s|;|&|$)" % re.escape(other), cmd), cmd
    assert "proj-dev-projdeck" in cmd


def test_enter_replaces_exited_session_of_exactly_this_project(env):
    r = env.project("dev/projdeck")
    mine = name_of(r["path"], r["home"])
    env.zlist.write_text(EXITED % mine + "\n" + EXITED % "proj-dev-projdeck" + "\n")
    cmd, _ = env.go(r)
    assert re.search(r"delete-session\s+'?%s'?(\s|;|&|$)" % re.escape(mine), cmd), cmd
    assert not re.search(r"delete-session\s+'?proj-dev-projdeck'?(\s|;|&|$)", cmd), cmd


def test_enter_attaches_live_session_of_exactly_this_project(env):
    r = env.project("dev/projdeck")
    mine = name_of(r["path"], r["home"])
    env.zlist.write_text(LIVE % mine + "\n")
    cmd, _ = env.go(r)
    assert re.search(r"attach\s+'?%s'?(\s|;|&|$)" % re.escape(mine), cmd), cmd
    assert "delete-session" not in cmd


def test_enter_plain_project_still_uses_unhashed_name(env):
    r = env.project("quant")
    cmd, _ = env.go(r)
    assert re.search(r"--session\s+'?proj-quant'?(\s|;|&|$)", cmd), cmd
