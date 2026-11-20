"""Different projects get different zellij session names, and Enter never attaches to or
deletes another project's session, including exited ones named by the old rule."""
import os
import re
import subprocess

import pytest

from projdeck import cli, opener

HOME = "/home/u"
NAME_RE = re.compile(r"^proj-[A-Za-z0-9._-]+$")
HASHED = re.compile(r"-[0-9a-f]{4}$")

PROJECTS = [  # (name as ui.project_name gives it, absolute path)
    ("work/foo-bar", HOME + "/work/foo-bar"),
    ("work-foo/bar", HOME + "/work-foo/bar"),
    ("work/foo/bar", HOME + "/work/foo/bar"),
    ("dev/projdeck", HOME + "/dev/projdeck"),
    ("dev-projdeck", HOME + "/dev-projdeck"),
    ("app", "/srv/x/app"),
    ("app", "/opt/app"),
]


def names():
    return [opener.proj_session_name(n, p, HOME) for n, p in PROJECTS]


def test_seven_projects_get_distinct_valid_names():
    got = names()
    assert len(set(got)) == 7, got
    assert all(NAME_RE.match(n) for n in got), got


def test_same_path_same_name():
    assert names() == names()


def test_top_level_project_in_home_has_no_hash():
    assert opener.proj_session_name("projdeck", HOME + "/projdeck", HOME) == "proj-projdeck"


@pytest.mark.parametrize("home", [None, ""])
def test_empty_home_counts_as_outside(home):
    assert HASHED.search(opener.proj_session_name("app", "/srv/x/app", home))


def test_outside_home_and_nested_and_truncated_get_a_hash():
    assert HASHED.search(opener.proj_session_name("app", "/opt/app", HOME))
    assert HASHED.search(opener.proj_session_name("a/b", HOME + "/a/b", HOME))
    assert HASHED.search(opener.proj_session_name("a b", HOME + "/a b", HOME))
    long = opener.proj_session_name("x" * 40, HOME + "/" + "x" * 40, HOME)
    assert HASHED.search(long) and len(long) <= len("proj-") + 32


def test_surrogate_path_does_not_raise():
    p = HOME + "/d\udcff\udc80"
    n = opener.proj_session_name("d\udcff\udc80", p, HOME)
    assert NAME_RE.match(n) and n == opener.proj_session_name("d\udcff\udc80", p, HOME)


def test_hash_follows_the_absolute_path():
    a = opener.proj_session_name("app", "/srv/x/app", HOME)
    b = opener.proj_session_name("app", "/srv/y/app", HOME)
    assert a != b and a.rsplit("-", 1)[0] == b.rsplit("-", 1)[0]


def test_enter_ignores_exited_session_named_by_the_old_rule(tmp_path, monkeypatch):
    """Fake zellij lists an exited proj-dev-projdeck; opening /home/u/dev/projdeck must neither
    delete nor attach to it, and creates a hashed session."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "zj.log"
    zj = bindir / "zellij"
    zj.write_text('#!/bin/sh\necho "$*" >> "%s"\n'
                  'if [ "$1" = list-sessions ]; then '
                  'echo "proj-dev-projdeck [Created 2h ago] (EXITED - attach to resurrect)"; fi\n'
                  'exit 0\n' % log)
    zj.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join(
        [str(bindir), os.environ["PROJ_STUB_DIRS"], "/usr/bin", "/bin"]))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    for k in ("ZELLIJ", "ZELLIJ_SESSION_NAME"):
        monkeypatch.delenv(k, raising=False)
    seen = []
    monkeypatch.setattr(cli.opener, "open_command",
                        lambda cmd, title, run=None, iterm=None: seen.append(cmd) or "inline")
    path = str(tmp_path / "home" / "dev" / "projdeck")
    os.makedirs(path)
    r = {"local": True, "ssh": None, "mux": "zellij", "host": "s-host", "path": path,
         "home": str(tmp_path / "home")}
    cli.open_project([r], r)
    cmd = seen[0]
    want = opener.proj_session_name("dev/projdeck", path, r["home"])
    assert HASHED.search(want) and want != "proj-dev-projdeck"
    assert "delete-session" not in cmd
    assert "--session %s " % want in cmd
    assert not re.search(r"proj-dev-projdeck(\s|$)", cmd.replace(want, ""))
    assert "delete-session" not in (log.read_text() if log.exists() else "")


def test_basename_for_nested_path_in_home_still_gets_a_hash():
    """If a caller's name is only the basename while the path is nested under home (the two
    in-home verdicts would disagree), the name must not stand alone: it would collide with
    /home/u/app."""
    n = opener.proj_session_name("app", HOME + "/work/app", HOME)
    assert HASHED.search(n) and n != opener.proj_session_name("app", HOME + "/app", HOME)


def test_ui_and_opener_share_the_in_home_verdict():
    from projdeck import paths, ui
    for path, home in [(HOME + "/a", HOME), (HOME + "/a/b", HOME + "/"), ("/opt/a", HOME),
                       ("/home/ux/a", HOME), ("/x/a", ""), ("/x/a", None)]:
        r = {"path": path, "home": home}
        rel = paths.rel_to_home(path, home)
        assert ui.project_name(r) == (rel if rel is not None else path.rsplit("/", 1)[-1])
        n = opener.proj_session_name(ui.project_name(r), path, home)
        assert bool(HASHED.search(n)) == (rel is None or "/" in rel)
