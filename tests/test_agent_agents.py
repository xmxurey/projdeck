import json
import os
import stat as stat_mod

from projdeck import agent


def stub(tmp_path, name, body):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(d)


def use_path(monkeypatch, d):
    monkeypatch.setenv("PATH", d + ":/usr/bin:/bin")


def fake_proc(root, procs, start=None):
    """procs: {pid: (ppid, comm, argv, cwd)}; start: {pid: stat column 22}"""
    root.mkdir(exist_ok=True)
    for pid, (ppid, comm, argv, cwd) in procs.items():
        d = root / str(pid)
        d.mkdir()
        cols = ["S", str(ppid)] + ["0"] * 17 + [str((start or {}).get(pid, 1))]
        (d / "stat").write_text("%d (%s) %s\n" % (pid, comm, " ".join(cols)))
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        if cwd:
            os.symlink(cwd, str(d / "cwd"))
    return str(root)


AGENTS = [
    {"id": "a1", "cwd": "/w", "kind": "interactive", "startedAt": 1700000000000,
     "sessionId": "S1", "pid": 11, "status": "busy", "name": "x"},
]


def test_claude_agents_json(tmp_path, monkeypatch):
    use_path(monkeypatch, stub(tmp_path, "claude", "echo '%s'" % json.dumps(AGENTS)))
    assert agent.claude_agents() == AGENTS


def test_claude_agents_failures(tmp_path, monkeypatch):
    use_path(monkeypatch, stub(tmp_path, "claude", "echo 'not json'"))
    assert agent.claude_agents() is None
    use_path(monkeypatch, stub(tmp_path, "claude", "echo '[]'; exit 3"))
    assert agent.claude_agents() is None
    use_path(monkeypatch, stub(tmp_path, "claude", "sleep 5"))
    assert agent.claude_agents(timeout=0.3) is None
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert agent.claude_agents() is None


def write_session(cdir, pid, **kw):
    d = cdir / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.json" % pid)).write_text(json.dumps(dict(pid=pid, **kw)))


def test_sessions_fallback_checks_procstart(tmp_path):
    cdir = tmp_path / "c"
    proc = fake_proc(tmp_path / "proc", {1: (0, "a", ["a"], None), 2: (0, "b", ["b"], None)},
                     start={1: 111624776, 2: 5})
    write_session(cdir, 1, sessionId="ok", procStart="111624776", cwd="/a", kind="interactive")
    write_session(cdir, 2, sessionId="reused", procStart="999", cwd="/b")
    write_session(cdir, 3, sessionId="gone", procStart="1", cwd="/c")
    got = agent.sessions_fallback(str(cdir), proc)
    assert [a["sessionId"] for a in got] == ["ok"]


def test_sessions_fallback_never_opens_key_files(tmp_path, monkeypatch):
    cdir = tmp_path / "c"
    proc = fake_proc(tmp_path / "proc", {1: (0, "a", ["a"], None)})
    write_session(cdir, 1, sessionId="ok")
    (cdir / "sessions" / "1.key").write_text("secret")
    opened = []
    real = open

    def spy(p, *a, **k):
        opened.append(str(p))
        return real(p, *a, **k)
    monkeypatch.setattr("builtins.open", spy)
    got = agent.sessions_fallback(str(cdir), proc)
    assert len(got) == 1
    assert not [p for p in opened if p.endswith(".key")]


def test_sessions_fallback_without_proc(tmp_path):
    assert agent.sessions_fallback(str(tmp_path), str(tmp_path / "nope")) is None


def test_live_agents_sources(tmp_path, monkeypatch):
    cdir = tmp_path / "c"
    cdir.mkdir()
    use_path(monkeypatch, stub(tmp_path, "claude", "echo '%s'" % json.dumps(AGENTS)))
    got = agent.live_agents(str(cdir), str(tmp_path / "nope"))
    assert got["source"] == "claude agents" and got["agents"][0]["sessionId"] == "S1"
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    proc = fake_proc(tmp_path / "proc", {})
    assert agent.live_agents(str(cdir), proc) == {"source": "sessions files", "agents": []}
    assert agent.live_agents(str(cdir), str(tmp_path / "nope")) == {"source": "none", "agents": []}


def test_live_agents_dedup_and_enrich(tmp_path, monkeypatch):
    cdir = tmp_path / "c"
    proc = fake_proc(tmp_path / "proc", {11: (0, "claude", ["claude"], None)}, start={11: 77})
    write_session(cdir, 11, sessionId="S1", procStart="77", statusUpdatedAt=1700000005000)
    job = cdir / "jobs" / "j1"
    job.mkdir(parents=True)
    (job / "state.json").write_text(json.dumps(
        {"detail": "d", "needs": "approve?", "updatedAt": "2026-11-18T10:00:00Z",
         "createdAt": "2026-11-18T09:00:00Z"}))
    agents = [
        {"id": "j1", "kind": "background", "sessionId": "S1", "state": "blocked"},
        dict(AGENTS[0]),
        {"id": "j2", "kind": "background", "sessionId": "S2", "state": "running"},
    ]
    use_path(monkeypatch, stub(tmp_path, "claude", "echo '%s'" % json.dumps(agents)))
    got = agent.live_agents(str(cdir), proc)["agents"]
    by = {a["sessionId"]: a for a in got}
    assert len(got) == 2 and by["S1"]["kind"] == "interactive"
    assert by["S1"]["statusUpdatedAt"] == 1700000005000
    assert by["S2"]["kind"] == "background" and "needs" not in by["S2"]
    # background session keeps its needs when it is the only one
    agents2 = [{"id": "j1", "kind": "background", "sessionId": "S9", "state": "blocked"}]
    use_path(monkeypatch, stub(tmp_path, "claude", "echo '%s'" % json.dumps(agents2)))
    bg = agent.live_agents(str(cdir), proc)["agents"][0]
    assert bg["needs"] == "approve?" and bg["detail"] == "d"
    assert bg["updatedAt"] == "2026-11-18T10:00:00Z"


def test_attach_map_zellij(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    proc = fake_proc(tmp_path / "proc", {
        10: (1, "zellij", ["/u/zellij", "--server", "/run/z/research"], None),
        11: (10, "zsh", ["zsh"], None),
        12: (11, "claude", ["claude"], None),
        20: (1, "claude", ["claude"], None),
    })
    assert agent.attach_map(proc) == {12: "zellij attach research", 20: ""}


def test_attach_map_tmux(tmp_path, monkeypatch):
    use_path(monkeypatch, stub(tmp_path, "tmux", "printf '31\\tproj-x\\n99\\tother\\n'"))
    proc = fake_proc(tmp_path / "proc", {
        30: (1, "tmux: server", ["tmux"], None),
        31: (30, "zsh", ["zsh"], None),
        32: (31, "claude", ["claude"], None),
    })
    assert agent.attach_map(proc) == {32: "tmux attach -t proj-x"}


def test_attach_map_claude_is_the_pane_itself(tmp_path, monkeypatch):
    use_path(monkeypatch, stub(tmp_path, "tmux", "printf '32\\tproj-x\\n'"))
    proc = fake_proc(tmp_path / "proc", {
        30: (1, "tmux: server", ["tmux"], None),
        32: (30, "claude", ["claude"], None),
    })
    assert agent.attach_map(proc) == {32: "tmux attach -t proj-x"}


def test_attach_map_quotes_session_names(tmp_path, monkeypatch):
    use_path(monkeypatch, stub(tmp_path, "tmux", "printf '32\\tmy proj;x\\n'"))
    proc = fake_proc(tmp_path / "proc", {
        10: (1, "zellij", ["/u/zellij", "--server", "/run/z/a b"], None),
        12: (10, "claude", ["claude"], None),
        30: (1, "tmux: server", ["tmux"], None),
        32: (30, "claude", ["claude"], None),
    })
    assert agent.attach_map(proc) == {12: "zellij attach 'a b'",
                                      32: "tmux attach -t 'my proj;x'"}


def test_claude_agents_timeout_not_blocked_by_escaped_holder(tmp_path, monkeypatch):
    import time
    use_path(monkeypatch, stub(tmp_path, "claude", "setsid sleep 4 &\nsleep 30"))
    t = time.time()
    assert agent.claude_agents(timeout=0.3) is None
    assert time.time() - t < 3


def _gone(pid, within=3.0):
    """True once pid no longer exists (a killed orphan is reaped by init shortly after)."""
    import time
    deadline = time.time() + within
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_claude_agents_timeout_kills_the_whole_process_group(tmp_path, monkeypatch):
    # F9 (T06 M-9): the timing tests also pass with p.kill() instead of killpg (one more
    # second at most); only the children left running tell the two apart
    pidfile = tmp_path / "child.pid"
    use_path(monkeypatch, stub(tmp_path, "claude", "sleep 30 &\necho $! > %s\nwait" % pidfile))
    assert agent.claude_agents(timeout=0.5) is None
    pid = int(pidfile.read_text())
    try:
        assert _gone(pid), "claude's child %d outlived the timeout" % pid
    finally:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
