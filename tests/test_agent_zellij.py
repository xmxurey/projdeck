import os
import re
import shutil
import subprocess

import pytest

from projdeck import agent


def fake_proc(root, procs):
    """procs: {pid: (ppid, comm, argv list, cwd or None)}"""
    for pid, (ppid, comm, argv, cwd) in procs.items():
        d = root / str(pid)
        d.mkdir()
        (d / "stat").write_text("%d (%s) S %d 1 1 0 -1\n" % (pid, comm, ppid))
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "comm").write_text(comm + "\n")
        if cwd:
            os.symlink(cwd, str(d / "cwd"))
    (root / "self").mkdir()  # non-numeric entries are ignored
    return str(root)


SERVER = "/home/u/.local/bin/zellij"
SOCK = "/run/user/1000/zellij/contract_version_1/"


def test_zellij_sessions_maps_descendants(tmp_path):
    proc = fake_proc(tmp_path, {
        10: (1, "zellij", [SERVER, "--server", SOCK + "research"], "/home/u"),
        11: (10, "zsh", ["/usr/bin/zsh"], "/home/u/invest"),
        12: (11, "claude", ["claude", "--resume"], "/home/u/invest"),
        13: (10, "zsh", ["/usr/bin/zsh"], "/home/u/invest/sub"),
        20: (1, "zellij", [SERVER, "--server", SOCK + "empty"], "/home/u"),
        21: (20, "bash", ["bash"], "/home/u/fin"),
        30: (1, "zellij", [SERVER, "attach", "research"], "/home/u"),  # a client, not a server
        40: (1, "zsh", ["zsh"], None),  # cwd unreadable
    })
    got = sorted(agent.zellij_sessions(proc))
    assert got == [("empty", ["/home/u/fin"], 0),
                   ("research", ["/home/u/invest", "/home/u/invest/sub"], 1)]


def test_comm_with_spaces_and_parens(tmp_path):
    proc = fake_proc(tmp_path, {
        10: (1, "zellij", [SERVER, "--server", SOCK + "s"], "/"),
        11: (10, "a (b) c", ["x"], "/w"),
    })
    assert agent.zellij_sessions(proc) == [("s", ["/w"], 0)]


def test_zellij_by_project_uses_longest_owner(tmp_path):
    proc = fake_proc(tmp_path, {
        10: (1, "zellij", [SERVER, "--server", SOCK + "dev"], "/"),
        11: (10, "zsh", ["zsh"], "/home/u/dev/projdeck/tests"),
        12: (10, "zsh", ["zsh"], "/home/u/dev"),
        13: (10, "zsh", ["zsh"], "/tmp"),
    })
    out = agent.zellij_by_project(["/home/u/dev", "/home/u/dev/projdeck"], proc)
    assert out == {"/home/u/dev": [{"name": "dev", "claude": 0}],
                   "/home/u/dev/projdeck": [{"name": "dev", "claude": 0}]}


def test_no_proc_means_no_sessions(tmp_path):
    assert agent.zellij_sessions(str(tmp_path / "missing")) == []


def test_collect_marks_live_zellij(tmp_path, make_repo, monkeypatch):
    root = tmp_path / "home"
    repo = make_repo(root / "proj", {"a.py": "1"})
    monkeypatch.setattr(agent, "tmux_sessions", lambda: [])
    monkeypatch.setattr(agent, "zellij_sessions", lambda proc="/proc": [("research", [repo], 2)])
    out = agent.collect({"roots": [str(root)], "scan_depth": 2, "claude_dir": str(tmp_path / "c")})
    p = [p for p in out["projects"] if p["path"] == repo][0]
    assert p["live_zellij"] == [{"name": "research", "claude": 2}]




@pytest.mark.skipif(os.environ.get("PROJ_LIVE_TESTS") != "1",
                    reason="set PROJ_LIVE_TESTS=1 to run against the real zellij on this machine")
def test_matches_zellij_dump_layout_on_this_machine(real_which, real_path, monkeypatch):
    # conftest puts stand-ins first on PATH; this test is about the real zellij
    zellij = real_which("zellij")
    monkeypatch.setenv("PATH", real_path)
    live = agent.zellij_sessions()
    if not live:
        pytest.skip("no live zellij session here")
    for name, cwds, _ in live:
        r = subprocess.run([zellij, "--session", name, "action", "dump-layout"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True, timeout=5)
        assert r.returncode == 0, (name, r.returncode)
        for top in re.findall(r'cwd "(/[^"]*)"', r.stdout):
            assert any(c == top or c.startswith(top.rstrip("/") + "/") for c in cwds), (name, top)


def test_attach_map_no_command_for_dash_session_name(tmp_path):
    proc = fake_proc(tmp_path, {
        10: (1, "zellij", [SERVER, "--server", SOCK + "-evil"], "/"),
        11: (10, "claude", ["claude"], "/w"),
        20: (1, "zellij", [SERVER, "--server", SOCK + "proj-ok"], "/"),
        21: (20, "claude", ["claude"], "/w"),
    })
    got = agent.attach_map(proc)
    assert got[11] == ""
    assert got[21] == "zellij attach proj-ok"
