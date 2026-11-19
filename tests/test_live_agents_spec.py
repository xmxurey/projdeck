"""Spec tests for T06: live agent list and attach commands (design 3.1.2, 3.1.8)."""
import builtins
import json
import os

import pytest

from projdeck import agent

BG = 1700000000123


def stub(bindir, name, body):
    bindir.mkdir(exist_ok=True)
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return p


@pytest.fixture
def bindir(tmp_path, monkeypatch):
    """PATH holds only our stubs (plus /bin-less basics via absolute sh in shebang)."""
    d = tmp_path / "stubs"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    return d


def claude_out(bindir, payload, code=0):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    f = bindir.parent / "claude_out.txt"
    f.write_text(text)
    stub(bindir, "claude", "/bin/cat '%s'\nexit %d\n" % (f, code))


def stat_line(pid, comm, ppid, start=1):
    fields = ["S", str(ppid)] + ["0"] * 50      # fields 3..52
    fields[22 - 3] = str(start)
    return "%d (%s) %s\n" % (pid, comm, " ".join(fields))


def add_proc(root, pid, comm, ppid, argv=None, start=1):
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stat").write_text(stat_line(pid, comm, ppid, start))
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in (argv or [comm])) + b"\0")
    (d / "comm").write_text(comm + "\n")


def write_session(claude_dir, pid, **kw):
    d = claude_dir / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    data = {"pid": pid}
    data.update(kw)
    (d / ("%d.json" % pid)).write_text(json.dumps(data))


def pids(items):
    return sorted(int(a["pid"]) for a in items if a.get("pid") is not None)


def lookup(m, pid):
    return m.get(pid, m.get(str(pid), ""))


# ---------- claude_agents ----------

def test_claude_agents_parses_fields(bindir):
    claude_out(bindir, [{"id": "a1", "cwd": "/w", "kind": "interactive", "startedAt": BG,
                         "sessionId": "s1", "pid": 5, "status": "busy", "name": "n"},
                        {"id": "a2"}])
    r = agent.claude_agents()
    assert isinstance(r, list) and len(r) == 2
    assert r[0]["id"] == "a1" and r[0]["sessionId"] == "s1" and r[0]["kind"] == "interactive"
    assert r[0]["startedAt"] == BG and r[0]["pid"] == 5 and r[0]["status"] == "busy"
    assert r[1]["id"] == "a2"  # missing fields do not raise


def test_claude_agents_invalid_json_none(bindir):
    claude_out(bindir, "not json{")
    assert agent.claude_agents() is None


def test_claude_agents_nonzero_exit_none(bindir):
    claude_out(bindir, [{"id": "a"}], code=1)
    assert agent.claude_agents() is None


def test_claude_agents_timeout_none(bindir):
    stub(bindir, "claude", "exec /bin/sleep 5\n")
    assert agent.claude_agents(timeout=0.5) is None


def test_claude_agents_missing_binary_none(bindir):
    assert agent.claude_agents() is None


def test_claude_agents_local_bin_fallback(bindir, tmp_path):
    home = os.environ["HOME"]
    d = os.path.join(home, ".local", "bin")
    os.makedirs(d)
    p = os.path.join(d, "claude")
    with open(p, "w") as f:
        f.write("#!/bin/sh\necho '[{\"id\":\"lb\"}]'\n")
    os.chmod(p, 0o755)
    r = agent.claude_agents()
    assert r and r[0]["id"] == "lb"


# ---------- live_agents sources ----------

def test_live_source_claude_agents(bindir, tmp_path):
    claude_out(bindir, [{"id": "a1", "sessionId": "s1", "kind": "interactive", "pid": 5}])
    r = agent.live_agents(str(tmp_path / "cd"), str(tmp_path / "noproc"))
    assert r["source"] == "claude agents"
    assert [a["id"] for a in r["agents"]] == ["a1"]


def test_live_source_empty_claude_list_is_still_claude_agents(bindir, tmp_path):
    claude_out(bindir, [])
    r = agent.live_agents(str(tmp_path / "cd"), str(tmp_path / "noproc"))
    assert r == {"source": "claude agents", "agents": []}


@pytest.mark.parametrize("mode", ["invalid", "exit1", "timeout", "missing"])
def test_live_falls_back_to_sessions_files(bindir, tmp_path, mode):
    if mode == "invalid":
        claude_out(bindir, "garbage")
    elif mode == "exit1":
        claude_out(bindir, [{"id": "x"}], code=3)
    elif mode == "timeout":
        stub(bindir, "claude", "exec /bin/sleep 5\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 10, "claude", 1, start=555)
    cd = tmp_path / "cd"
    write_session(cd, 10, sessionId="s10", cwd="/w", procStart="555", kind="interactive")
    if mode == "timeout":
        import projdeck.agent as a
        orig = a.claude_agents
        a.claude_agents = lambda timeout=5: orig(timeout=0.5)
        try:
            r = agent.live_agents(str(cd), str(proc))
        finally:
            a.claude_agents = orig
    else:
        r = agent.live_agents(str(cd), str(proc))
    assert r["source"] == "sessions files"
    assert pids(r["agents"]) == [10]


def test_live_source_none_when_no_proc(bindir, tmp_path):
    r = agent.live_agents(str(tmp_path / "cd"), str(tmp_path / "does-not-exist"))
    assert r["source"] == "none" and r["agents"] == []


def test_fallback_none_without_proc(tmp_path):
    assert agent.sessions_fallback(str(tmp_path), str(tmp_path / "nope")) is None


def test_fallback_empty_list_when_no_sessions(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    assert agent.sessions_fallback(str(tmp_path / "cd"), str(proc)) == []


# ---------- procStart verification ----------

def test_fallback_procstart_filter(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 11, "claude", 1, start=111624776)   # matches
    add_proc(proc, 12, "claude", 1, start=999)         # mismatch
    cd = tmp_path / "cd"
    write_session(cd, 11, sessionId="ok", procStart="111624776")
    write_session(cd, 12, sessionId="reused", procStart="111624776")
    write_session(cd, 13, sessionId="dead", procStart="111624776")      # no such pid
    write_session(cd, 14, sessionId="bad", procStart="notanumber")      # unparsable
    add_proc(proc, 14, "claude", 1, start=1)
    r = agent.sessions_fallback(str(cd), str(proc))
    assert pids(r) == [11]


def test_fallback_without_procstart_checks_pid_only(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 21, "claude", 1, start=5)
    cd = tmp_path / "cd"
    write_session(cd, 21, sessionId="a")
    write_session(cd, 22, sessionId="b")  # pid missing
    assert pids(agent.sessions_fallback(str(cd), str(proc))) == [21]


def test_fallback_procstart_as_int_also_compares(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 31, "claude", 1, start=42)
    cd = tmp_path / "cd"
    write_session(cd, 31, sessionId="a", procStart=42)
    assert pids(agent.sessions_fallback(str(cd), str(proc))) == [31]


def test_key_files_never_opened(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 41, "claude", 1, start=7)
    add_proc(proc, 777, "claude", 1, start=7)
    cd = tmp_path / "cd"
    write_session(cd, 41, sessionId="real", procStart="7")
    (cd / "sessions" / "777.key").write_text(json.dumps(
        {"pid": 777, "sessionId": "keyed", "procStart": "7"}))
    (cd / "sessions" / "secret.key").write_text("SECRET")
    opened = []
    real_open = builtins.open

    def spy(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)
    monkeypatch.setattr(builtins, "open", spy)
    r = agent.sessions_fallback(str(cd), str(proc))
    assert pids(r) == [41]
    assert not [p for p in opened if p.endswith(".key")]


# ---------- merging ----------

def test_dedup_prefers_interactive(bindir, tmp_path):
    cd = tmp_path / "cd"
    for order in (0, 1):
        items = [{"id": "bg1", "sessionId": "S", "kind": "background", "state": "running"},
                 {"id": "int1", "sessionId": "S", "kind": "interactive", "pid": 9,
                  "status": "busy"},
                 {"id": "other", "sessionId": "T", "kind": "background"}]
        if order:
            items.reverse()
        claude_out(bindir, items)
        r = agent.live_agents(str(cd), str(tmp_path / "noproc"))
        same = [a for a in r["agents"] if a["sessionId"] == "S"]
        assert len(same) == 1 and same[0]["kind"] == "interactive" and same[0]["id"] == "int1"
        assert len(r["agents"]) == 2


def test_background_gets_job_state_fields(bindir, tmp_path):
    cd = tmp_path / "cd"
    j = cd / "jobs" / "bg7"
    j.mkdir(parents=True)
    (j / "state.json").write_text(json.dumps(
        {"detail": "compiling", "needs": "approve plan", "updatedAt": "2026-11-18T10:00:00Z"}))
    claude_out(bindir, [{"id": "bg7", "sessionId": "S", "kind": "background",
                         "state": "blocked"}])
    r = agent.live_agents(str(cd), str(tmp_path / "noproc"))
    a = r["agents"][0]
    assert a["needs"] == "approve plan"
    assert a["detail"] == "compiling"
    assert a["updatedAt"] == "2026-11-18T10:00:00Z"
    assert a["state"] == "blocked"  # original key preserved


def test_background_without_job_file_ok(bindir, tmp_path):
    claude_out(bindir, [{"id": "bg8", "sessionId": "S", "kind": "background"}])
    r = agent.live_agents(str(tmp_path / "cd"), str(tmp_path / "noproc"))
    assert len(r["agents"]) == 1 and not r["agents"][0].get("needs")


def test_interactive_status_updated_at_verified_by_procstart(bindir, tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 51, "claude", 1, start=100)
    add_proc(proc, 52, "claude", 1, start=200)
    cd = tmp_path / "cd"
    write_session(cd, 51, sessionId="A", procStart="100", statusUpdatedAt=1700000111000)
    write_session(cd, 52, sessionId="B", procStart="999", statusUpdatedAt=1700000222000)
    claude_out(bindir, [
        {"id": "i1", "sessionId": "A", "kind": "interactive", "pid": 51, "status": "idle"},
        {"id": "i2", "sessionId": "B", "kind": "interactive", "pid": 52, "status": "idle"}])
    r = agent.live_agents(str(cd), str(proc))
    by = {a["sessionId"]: a for a in r["agents"]}
    assert by["A"]["statusUpdatedAt"] == 1700000111000
    assert by["B"].get("statusUpdatedAt") is None


def test_session_dict_uses_ruled_keys(bindir, tmp_path):
    """T04 ruling: kind / state / status keys as in `claude agents --json`."""
    claude_out(bindir, [{"id": "a", "sessionId": "S", "kind": "interactive", "pid": 1,
                         "status": "waiting"},
                        {"id": "b", "sessionId": "T", "kind": "background", "state": "running"}])
    r = agent.live_agents(str(tmp_path / "cd"), str(tmp_path / "np"))
    by = {a["sessionId"]: a for a in r["agents"]}
    assert by["S"]["kind"] == "interactive" and by["S"]["status"] == "waiting"
    assert by["T"]["kind"] == "background" and by["T"]["state"] == "running"


def test_fallback_agents_carry_ruled_keys(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 61, "claude", 1, start=3)
    cd = tmp_path / "cd"
    write_session(cd, 61, sessionId="S", cwd="/w", procStart="3", status="busy",
                  statusUpdatedAt=1700000333000, startedAt=BG)
    a = agent.sessions_fallback(str(cd), str(proc))[0]
    assert a["sessionId"] == "S" and a["pid"] == 61
    assert a.get("status") == "busy"
    assert a.get("statusUpdatedAt") == 1700000333000


def test_live_agents_writes_nothing(bindir, tmp_path):
    cd = tmp_path / "cd"
    (cd / "jobs" / "b").mkdir(parents=True)
    (cd / "jobs" / "b" / "state.json").write_text("{}")
    claude_out(bindir, [{"id": "b", "sessionId": "S", "kind": "background"}])

    def snap():
        return sorted((os.path.join(r, f), os.path.getmtime(os.path.join(r, f)))
                      for r, _, fs in os.walk(str(cd)) for f in fs)
    before = snap()
    agent.live_agents(str(cd), str(tmp_path / "np"))
    assert snap() == before


# ---------- attach_map ----------

def test_attach_zellij(tmp_path, bindir):
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 1, "init", 0)
    add_proc(proc, 100, "zellij", 1, ["zellij", "--server", "/run/user/1000/zellij/v1/research"])
    add_proc(proc, 110, "bash", 100, ["bash"])
    add_proc(proc, 120, "claude", 110, ["claude"])
    m = agent.attach_map(str(proc))
    assert lookup(m, 120) == "zellij attach research"


def test_attach_tmux(tmp_path, bindir):
    log = tmp_path / "tmux.log"
    stub(bindir, "tmux",
         "echo \"$@\" >> '%s'\n"
         "case \"$1\" in list-panes) printf '200\\tproj-x\\n300\\tother\\n';; esac\n" % log)
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 1, "init", 0)
    add_proc(proc, 50, "tmux: server", 1, ["tmux", "new", "-s", "proj-x"])
    add_proc(proc, 200, "bash", 50, ["bash"])
    add_proc(proc, 210, "node", 200, ["node"])
    add_proc(proc, 220, "claude", 210, ["claude"])
    m = agent.attach_map(str(proc))
    assert lookup(m, 220) == "tmux attach -t proj-x"
    calls = log.read_text().splitlines()
    assert calls and all(c.startswith("list-panes") for c in calls)  # never attaches


def test_attach_neither_is_empty_string(tmp_path, bindir):
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 1, "init", 0)
    add_proc(proc, 70, "sshd", 1, ["sshd"])
    add_proc(proc, 71, "bash", 70, ["bash"])
    add_proc(proc, 72, "claude", 71, ["claude"])
    m = agent.attach_map(str(proc))
    assert lookup(m, 72) == ""


def test_attach_two_claudes_get_own_commands(tmp_path, bindir):
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 1, "init", 0)
    add_proc(proc, 100, "zellij", 1, ["zellij", "--server", "/s/alpha"])
    add_proc(proc, 101, "zellij", 1, ["zellij", "--server", "/s/beta"])
    add_proc(proc, 110, "claude", 100, ["claude"])
    add_proc(proc, 111, "claude", 101, ["claude"])
    add_proc(proc, 112, "claude", 1, ["claude"])
    m = agent.attach_map(str(proc))
    assert lookup(m, 110) == "zellij attach alpha"
    assert lookup(m, 111) == "zellij attach beta"
    assert lookup(m, 112) == ""


def test_attach_no_proc_dir_is_empty(tmp_path, bindir):
    assert not agent.attach_map(str(tmp_path / "nope"))


# ---------- review round 1 additions ----------

def _tmux_stub(bindir, panes):
    stub(bindir, "tmux", "case \"$1\" in list-panes) printf '%s';; esac\n" % panes)


def test_attach_tmux_claude_is_pane_process(tmp_path, bindir):
    _tmux_stub(bindir, "220\\tproj-x\\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 1, "init", 0)
    add_proc(proc, 50, "tmux: server", 1, ["tmux", "new", "-s", "proj-x", "claude"])
    add_proc(proc, 220, "claude", 50, ["claude"])
    assert lookup(agent.attach_map(str(proc)), 220) == "tmux attach -t proj-x"


def test_attach_tmux_parent_is_server_pane_is_self(tmp_path, bindir):
    _tmux_stub(bindir, "230\\tproj-x\\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 50, "tmux: server", 1, ["tmux: server"])
    add_proc(proc, 230, "claude", 50, ["claude"])
    assert lookup(agent.attach_map(str(proc)), 230) == "tmux attach -t proj-x"


def test_attach_tmux_name_quoted(tmp_path, bindir):
    import shlex
    _tmux_stub(bindir, "200\\tmy proj;x\\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 50, "tmux: server", 1, ["tmux"])
    add_proc(proc, 200, "bash", 50, ["bash"])
    add_proc(proc, 210, "claude", 200, ["claude"])
    got = lookup(agent.attach_map(str(proc)), 210)
    assert got == "tmux attach -t " + shlex.quote("my proj;x")
    assert shlex.split(got) == ["tmux", "attach", "-t", "my proj;x"]


def test_attach_zellij_name_quoted(tmp_path, bindir):
    import shlex
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 100, "zellij", 1, ["zellij", "--server", "/s/my proj;x"])
    add_proc(proc, 120, "claude", 100, ["claude"])
    got = lookup(agent.attach_map(str(proc)), 120)
    assert got == "zellij attach " + shlex.quote("my proj;x")
    assert shlex.split(got) == ["zellij", "attach", "my proj;x"]


def test_claude_agents_timeout_with_descendant_holding_stdout(bindir):
    import time
    stub(bindir, "claude", "/bin/sleep 8 &\nexec /bin/sleep 8\n")
    t = time.time()
    assert agent.claude_agents(timeout=0.5) is None
    assert time.time() - t < 6


def test_live_agents_falls_back_when_claude_hangs_with_descendant(bindir, tmp_path, monkeypatch):
    import time
    stub(bindir, "claude", "/bin/sleep 8 &\nexec /bin/sleep 8\n")
    orig = agent.claude_agents
    monkeypatch.setattr(agent, "claude_agents", lambda timeout=5: orig(timeout=0.5))
    proc = tmp_path / "proc"
    proc.mkdir()
    add_proc(proc, 10, "claude", 1, start=5)
    cd = tmp_path / "cd"
    write_session(cd, 10, sessionId="s", procStart="5")
    t = time.time()
    r = agent.live_agents(str(cd), str(proc))
    assert time.time() - t < 6
    assert r["source"] == "sessions files" and pids(r["agents"]) == [10]
