"""zellij attach: "already exited" is judged like the agent does (trailing marker only),
and a session name that starts with "-" is never put into a zellij command."""
import subprocess

import pytest

from projdeck import cli, opener


def _fake_zellij(bindir, listing):
    bindir.mkdir()
    z = bindir / "zellij"
    z.write_text('#!/bin/sh\nif [ "$1" = list-sessions ]; then cat "%s"; exit 0; fi\n'
                 'for a in "$@"; do echo "[$a]"; done\n' % (bindir / "list.txt"))
    z.chmod(0o755)
    (bindir / "list.txt").write_text(listing)


def _run(name, tmp_path, listing):
    d = tmp_path / "p"
    d.mkdir()
    _fake_zellij(tmp_path / "bin", listing)
    script = opener.zellij_attach(name, str(d)).replace("claude --resume", "pwd")
    env = {"SHELL": "/bin/sh", "PATH": str(tmp_path / "bin") + ":/usr/bin:/bin"}
    out = subprocess.run(["/bin/sh", "-c", script], env=env, stdout=subprocess.PIPE,
                         universal_newlines=True, input="").stdout.splitlines()
    return out, str(d)


def test_live_session_named_exited_is_attached(tmp_path):
    out, _ = _run("proj-EXITED", tmp_path,
                  "proj-EXITED [Created 2h ago]\nproj-other [Created 1h ago]\n")
    assert out == ["[attach]", "[proj-EXITED]"]


def test_trailing_exited_marker_is_not_attached(tmp_path):
    out, d = _run("proj-a", tmp_path,
                  "proj-a [Created 2h ago] (EXITED - attach to resurrect)\n")
    assert d in out and "[attach]" not in out  # (login shells on some hosts print a motd too)


def test_exited_word_inside_the_line_does_not_make_it_exited(tmp_path):
    out, _ = _run("proj-a", tmp_path, "proj-a [Created 2h ago] (EXITEDX)\n")
    assert out == ["[attach]", "[proj-a]"]


def test_name_starting_with_dash_raises():
    with pytest.raises(ValueError):
        opener.zellij_attach("-d", "/home/u/p")


def test_open_project_with_dash_session_gives_a_notice_and_runs_nothing(monkeypatch):
    r = {"host": "s-host", "path": "/home/u/p", "local": True, "ssh": None, "mux": "zellij"}
    monkeypatch.setattr(cli.config, "zellij_in_scope", lambda row: True)
    monkeypatch.setattr(cli.agent, "zellij_by_project",
                        lambda paths: {"/home/u/p": [{"name": "-x", "exited": False}]})
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
    ran = []
    monkeypatch.setattr(cli.opener, "open_command", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(cli, "log_event", lambda *a, **k: None)
    outcome, notice = cli.open_project([r], r)
    assert outcome is None and "-" in notice and "attach" in notice
    assert ran == []
