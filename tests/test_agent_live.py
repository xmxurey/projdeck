import datetime
import json
import os
import subprocess
import sys

import pytest

from projdeck import agent
from tests.conftest import write

NOW = 1_800_000_000
DAY = 86400
AGENT_PY = os.path.join(os.path.dirname(agent.__file__), "agent.py")


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def u(cwd, ts, text, entry="cli"):
    return {"type": "user", "cwd": cwd, "entrypoint": entry, "timestamp": iso(ts),
            "message": {"role": "user", "content": text}}


def a(cwd, ts, text):
    return {"type": "assistant", "cwd": cwd, "timestamp": iso(ts),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def fake_claude(tmp_path, monkeypatch, agents):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / "claude"
    p.write_text("#!/bin/sh\necho '%s'\n" % json.dumps(agents))
    p.chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (d, os.environ["PATH"]))


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    claude = home / ".claude"
    claude.mkdir()
    return home, claude


def live(claude, **kw):
    req = {"action": "live", "claude_dir": str(claude), "paths": [], "now": NOW}
    req.update(kw)
    return agent.act_live(req)


def by_id(out):
    return {s["id"]: s for s in out["sessions"]}


def test_live_and_ended_and_sdkcli(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    make_session(claude, cwd, "S1", [u(cwd, NOW - 3 * DAY, "go"),
                                     a(cwd, NOW - 3 * DAY + 5, "Done.\n\nShall I merge it?")],
                 mtime=NOW - 3 * DAY)
    make_session(claude, cwd, "S2", [u(cwd, NOW - 7200, "hi"), a(cwd, NOW - 7000, "ok")],
                 mtime=NOW - 7000)
    make_session(claude, cwd, "S3", [u(cwd, NOW - 100, "x", entry="sdk-cli")], mtime=NOW - 100)
    fake_claude(tmp_path, monkeypatch, [
        {"id": "a1", "cwd": cwd, "kind": "interactive", "startedAt": (NOW - 3 * DAY) * 1000,
         "sessionId": "S1", "pid": 999999, "status": "idle", "name": "first"}])
    out = live(claude, paths=[cwd])
    assert out["schema"] == 1 and out["source"] == "claude agents"
    s = by_id(out)
    assert set(s) == {"S1", "S2"}
    assert s["S1"]["state"] == "waiting" and s["S1"]["live"] is True
    assert s["S1"]["question"] == "Shall I merge it?" and s["S1"]["name"] == "first"
    assert s["S1"]["project"] == cwd
    assert s["S2"]["live"] is False and s["S2"]["state"] == "ended"
    assert s["S2"]["started_at"] and s["S2"]["status"] == "idle"
    assert out["zellij_exited"] is None
    assert "task_uses" not in s["S1"]


def ledger(root, run, tasks):
    write(os.path.join(root, "docs/dev/runs/.active"), run)
    write(os.path.join(root, "docs/dev/runs", run, "state.json"),
          json.dumps({"run_id": run, "tasks": tasks}))


def test_same_checkout_only_earliest_owns_tasks(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    ledger(cwd, "r1", [{"id": "T1", "title": "one", "status": "pending"}])
    for sid in ("LATE", "EARLY"):
        make_session(claude, cwd, sid, [u(cwd, NOW - 500, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, [
        {"id": "a1", "cwd": cwd, "kind": "interactive", "startedAt": (NOW - 100) * 1000,
         "sessionId": "LATE", "pid": 999998, "status": "idle"},
        {"id": "a2", "cwd": cwd, "kind": "interactive", "startedAt": (NOW - 900) * 1000,
         "sessionId": "EARLY", "pid": 999999, "status": "idle"}])
    s = by_id(live(claude, paths=[cwd]))
    assert [i["id"] for i in s["EARLY"]["tasks"]["items"]] == ["T1"]
    assert s["EARLY"]["tasks_owner"] is None
    assert s["LATE"]["tasks"] is None and s["LATE"]["tasks_owner"] == "EARLY"


def test_worktree_ledger_belongs_to_worktree_session(env, tmp_path, monkeypatch, make_session,
                                                     make_repo):
    home, claude = env
    root = make_repo(home / "proj")
    wt = os.path.join(root, ".claude", "worktrees", "w1")
    subprocess.run(["git", "-C", root, "worktree", "add", "-q", wt, "-b", "w1"], check=True)
    ledger(wt, "rw", [{"id": "W1", "title": "wt task", "status": "in_progress"}])
    ledger(root, "rr", [{"id": "R1", "title": "root task", "status": "pending"}])
    make_session(claude, wt, "WT", [u(wt, NOW - 500, "go")], mtime=NOW - 10)
    make_session(claude, root, "ROOT", [u(root, NOW - 500, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, [
        {"id": "a1", "cwd": wt, "kind": "interactive", "startedAt": (NOW - 900) * 1000,
         "sessionId": "WT", "pid": 999998, "status": "idle"},
        {"id": "a2", "cwd": root, "kind": "interactive", "startedAt": (NOW - 100) * 1000,
         "sessionId": "ROOT", "pid": 999999, "status": "idle"}])
    out = live(claude, paths=[root], since=1)
    s = by_id(out)
    assert [i["id"] for i in s["WT"]["tasks"]["items"]] == ["W1"]
    assert [i["id"] for i in s["ROOT"]["tasks"]["items"]] == ["R1"]
    assert s["WT"]["tasks_owner"] is None and s["ROOT"]["tasks_owner"] is None
    assert out["projects"][root]["last_commit"]["subject"] == "init"
    assert len(out["projects"][root]["commits"]) == 1


def test_subagent_tasks_not_in_main_tasks(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    tc = lambda n, i: {"type": "assistant", "cwd": cwd, "timestamp": iso(NOW - 50),
                       "message": {"role": "assistant", "content": [
                           {"type": "tool_use", "id": i, "name": "TaskCreate",
                            "input": {"subject": n}}]}}
    fp = make_session(claude, cwd, "S1", [u(cwd, NOW - 100, "go"), tc("main task", "m1")],
                      mtime=NOW - 10)
    sub = os.path.join(os.path.dirname(fp), "S1", "subagents")
    os.makedirs(sub)
    with open(os.path.join(sub, "agent-1.jsonl"), "w") as f:
        f.write(json.dumps(tc("sub task", "s1")) + "\n")
    fake_claude(tmp_path, monkeypatch, [
        {"id": "a1", "cwd": cwd, "kind": "interactive", "startedAt": (NOW - 100) * 1000,
         "sessionId": "S1", "pid": 999999, "status": "idle"}])
    t = by_id(live(claude, paths=[cwd]))["S1"]["tasks"]
    assert t["source"] == "session"
    assert [i["title"] for i in t["items"]] == ["main task"]


def test_memo_skips_unchanged_and_prunes(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    f1 = make_session(claude, cwd, "S1", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    f2 = make_session(claude, cwd, "S2", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, [])
    calls = []
    real = agent.transcript_events
    monkeypatch.setattr(agent, "transcript_events", lambda fp, since: calls.append(fp) or real(fp, since))
    memo = {}
    req = {"action": "live", "claude_dir": str(claude), "paths": [cwd], "now": NOW}
    agent.act_live(req, memo)
    assert sorted(calls) == sorted([f1, f2]) and len(memo) == 2
    del calls[:]
    agent.act_live(req, memo)
    assert calls == []
    os.remove(f2)
    with open(f1, "a") as f:
        f.write(json.dumps(a(cwd, NOW - 5, "more")) + "\n")
    os.utime(f1, (NOW - 5, NOW - 5))
    agent.act_live(req, memo)
    assert calls == [f1]
    assert len(memo) == 1 and all(k[0] == f1 for k in memo)


def test_one_broken_session_does_not_stop_the_rest(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    make_session(claude, cwd, "BAD", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    make_session(claude, cwd, "GOOD", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, [
        {"id": "a%d" % i, "cwd": cwd, "kind": "interactive", "startedAt": (NOW - 100) * 1000,
         "sessionId": sid, "pid": 999990 + i, "status": "idle"}
        for i, sid in enumerate(("BAD", "GOOD"))])
    real = agent.transcript_events

    def flaky(fp, since):
        if "BAD" in fp:
            raise OSError("boom")
        return real(fp, since)
    monkeypatch.setattr(agent, "transcript_events", flaky)
    s = by_id(live(claude, paths=[cwd]))
    assert "boom" in s["BAD"]["errors"][0]
    assert s["GOOD"]["errors"] == []


def test_ssh_style_run_returns_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    os.makedirs(str(tmp_path / "h"))
    with open(AGENT_PY) as f:
        r = subprocess.run([sys.executable, "-", '{"action":"live"}'], stdin=f,
                           stdout=subprocess.PIPE, universal_newlines=True)
    out = json.loads(r.stdout)
    assert out["schema"] == 1 and out["sessions"] == [] and out["zellij_exited"] is None


def fake_zellij(tmp_path, monkeypatch, body):
    d = tmp_path / "zbin"
    d.mkdir(exist_ok=True)
    p = d / "zellij"
    p.write_text("#!/bin/sh\necho \"$@\" >> %s/calls\n%s\n" % (tmp_path, body))
    p.chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (d, os.environ["PATH"]))


LIST = ("echo 'dao [Created 3months ago] (EXITED - attach to resurrect)'; "
        "echo 'proj-quant [Created 2h ago]'; echo 'projdeck [Created 2m ago] (current)'")


def test_zellij_session_list(tmp_path, monkeypatch):
    fake_zellij(tmp_path, monkeypatch, LIST)
    assert agent.zellij_session_list() == [
        {"name": "dao", "exited": True}, {"name": "proj-quant", "exited": False},
        {"name": "projdeck", "exited": False}]
    assert open(str(tmp_path / "calls")).read().split() == ["list-sessions", "-n"]


def test_zellij_session_list_none_and_unavailable(tmp_path, monkeypatch):
    fake_zellij(tmp_path, monkeypatch, "echo 'No active zellij sessions found.' >&2; exit 1")
    assert agent.zellij_session_list() == []
    fake_zellij(tmp_path, monkeypatch, "sleep 5")
    assert agent.zellij_session_list(timeout=0.3) is None
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert agent.zellij_session_list() is None


def test_live_zellij_exited_flag(env, tmp_path, monkeypatch):
    home, claude = env
    fake_claude(tmp_path, monkeypatch, [])
    fake_zellij(tmp_path, monkeypatch, "echo 'zeta [Created 1d ago] (EXITED - attach to resurrect)'; " + LIST)
    assert live(claude)["zellij_exited"] is None
    assert live(claude, zellij=True)["zellij_exited"] == ["dao", "zeta"]
    fake_zellij(tmp_path, monkeypatch, "exit 3")
    assert live(claude, zellij=True)["zellij_exited"] is None


def test_git_failure_is_reported_not_zero(env, tmp_path, monkeypatch, make_session, make_repo):
    home, claude = env
    root = make_repo(home / "proj")
    make_session(claude, root, "S1", [u(root, NOW - 100, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, [])
    real = agent.git

    def broken(path, *args):
        if args and args[0] == "log":
            raise RuntimeError("git timed out after 5s")
        return real(path, *args)
    monkeypatch.setattr(agent, "git", broken)
    out = live(claude, paths=[root], since=1)
    p = out["projects"][root]
    assert p["commits"] is None and "timed out" in p["error"]
    assert any("timed out" in e for e in out["errors"])


def test_empty_repo_and_plain_dir_are_not_errors(env, tmp_path, monkeypatch, make_session, make_repo):
    home, claude = env
    empty = make_repo(home / "empty", commit=False)
    plain = str(home / "plain")
    os.makedirs(plain)
    for d, sid in ((empty, "E"), (plain, "P")):
        make_session(claude, d, sid, [u(d, NOW - 100, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, [])
    out = live(claude, paths=[empty, plain], since=1)
    for d in (empty, plain):
        assert out["projects"][d] == {"last_commit": None, "commits": []}
    assert out["errors"] == []


def test_unreadable_ended_transcript_is_reported(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    make_session(claude, cwd, "GONE", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    make_session(claude, cwd, "OK", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, [])
    real = agent.transcript_events

    def flaky(fp, since):
        if "GONE" in fp:
            raise PermissionError("denied")
        return real(fp, since)
    monkeypatch.setattr(agent, "transcript_events", flaky)
    out = live(claude, paths=[cwd])
    assert set(by_id(out)) == {"OK"}
    assert len(out["errors"]) == 1 and "denied" in out["errors"][0]


def test_vanished_transcript_is_skipped_silently(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    make_session(claude, cwd, "GONE", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, [])

    def gone(fp, since):
        raise FileNotFoundError(fp)
    monkeypatch.setattr(agent, "transcript_events", gone)
    out = live(claude, paths=[cwd])
    assert out["sessions"] == [] and out["errors"] == []


def test_live_agents_and_attach_failures_degrade(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    make_session(claude, cwd, "S1", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, [
        {"id": "a1", "cwd": cwd, "kind": "interactive", "startedAt": (NOW - 100) * 1000,
         "sessionId": "S1", "pid": 999999, "status": "idle"}])

    def boom(*a, **k):
        raise OSError("proc broke")
    monkeypatch.setattr(agent, "attach_map", boom)
    out = live(claude, paths=[cwd])
    assert by_id(out)["S1"]["attach"] == "" and any("proc broke" in e for e in out["errors"])
    monkeypatch.setattr(agent, "live_agents", boom)
    out = live(claude, paths=[cwd])
    assert out["source"] == "none" and any("proc broke" in e for e in out["errors"])
    assert set(by_id(out)) == {"S1"} and by_id(out)["S1"]["live"] is False
