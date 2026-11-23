"""act_live marks each session with `state_error` (D7: an errored session is never folded).

True only when the session's state cannot be trusted: its transcript read raised something
other than FileNotFoundError, or the session assembly fell into the whole-session fallback.
Task-list warnings (unknown taskId, unknown ledger status) leave it false.
"""
import os

import pytest

from projdeck import agent
from tests.test_agent_live import NOW, a, by_id, fake_claude, iso, ledger, live, u


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    claude = home / ".claude"
    claude.mkdir()
    return home, claude


def agents(cwd, sids):
    return [{"id": "a%d" % i, "cwd": cwd, "kind": "interactive", "startedAt": (NOW - 100) * 1000,
             "sessionId": sid, "pid": 999990 + i, "status": "idle"}
            for i, sid in enumerate(sids)]


def use(cwd, uid, name, inp):
    return {"type": "assistant", "cwd": cwd, "timestamp": iso(NOW - 90),
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]}}


def test_unreadable_transcript_marks_only_that_session(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    good = make_session(claude, cwd, "GOOD", [u(cwd, NOW - 100, "go"), a(cwd, NOW - 90, "ok")],
                        mtime=NOW - 10)
    os.makedirs(os.path.join(os.path.dirname(good), "BAD.jsonl"))  # reading it raises
    fake_claude(tmp_path, monkeypatch, agents(cwd, ("BAD", "GOOD")))
    s = by_id(live(claude, paths=[cwd]))
    assert s["BAD"]["state_error"] is True
    assert any(e.startswith("transcript ") for e in s["BAD"]["errors"])
    assert s["GOOD"]["state_error"] is False and s["GOOD"]["errors"] == []


def test_assembly_fallback_marks_session(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    for sid in ("BROKE", "FINE"):
        make_session(claude, cwd, sid, [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, agents(cwd, ("BROKE", "FINE")))
    real = agent.segments
    calls = []

    def flaky(tx, *rest):
        calls.append(1)
        if len(calls) == 1:  # the first session assembled (BROKE, by candidate order)
            raise ValueError("segments broke")
        return real(tx, *rest)
    monkeypatch.setattr(agent, "segments", flaky)
    s = by_id(live(claude, paths=[cwd]))
    assert any("segments broke" in e for e in s["BROKE"]["errors"])
    assert s["BROKE"]["state_error"] is True
    assert s["FINE"]["state_error"] is False and s["FINE"]["errors"] == []


def test_unknown_task_id_warning_is_not_a_state_error(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    make_session(claude, cwd, "S1",
                 [u(cwd, NOW - 100, "go"), use(cwd, "t1", "TaskUpdate", {"taskId": "99", "status": "completed"})],
                 mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, agents(cwd, ("S1",)))
    s = by_id(live(claude, paths=[cwd]))["S1"]
    assert any("TaskUpdate: unknown taskId 99" in e for e in s["errors"])
    assert s["state_error"] is False


def test_unknown_ledger_status_warning_is_not_a_state_error(env, tmp_path, monkeypatch,
                                                            make_session):
    home, claude = env
    cwd = str(home / "proj")
    ledger(cwd, "r1", [{"id": "T1", "title": "one", "status": "weird"}])
    make_session(claude, cwd, "S1", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, agents(cwd, ("S1",)))
    s = by_id(live(claude, paths=[cwd]))["S1"]
    assert any("unknown status 'weird'" in e for e in s["errors"])
    assert s["state_error"] is False


def test_task_lookup_failure_is_not_a_state_error(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    make_session(claude, cwd, "S1", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    fake_claude(tmp_path, monkeypatch, agents(cwd, ("S1",)))

    def boom(*args, **kw):
        raise RuntimeError("tasks broke")
    monkeypatch.setattr(agent, "find_tasks", boom)
    s = by_id(live(claude, paths=[cwd]))["S1"]
    assert any("tasks broke" in e for e in s["errors"])
    assert s["state_error"] is False


def test_every_session_has_boolean_state_error(env, tmp_path, monkeypatch, make_session):
    home, claude = env
    cwd = str(home / "proj")
    os.makedirs(cwd)
    make_session(claude, cwd, "LIVE", [u(cwd, NOW - 100, "go")], mtime=NOW - 10)
    make_session(claude, cwd, "ENDED", [u(cwd, NOW - 7200, "hi"), a(cwd, NOW - 7000, "ok")],
                 mtime=NOW - 7000)
    fake_claude(tmp_path, monkeypatch, agents(cwd, ("LIVE",)))
    out = live(claude, paths=[cwd])
    assert set(by_id(out)) == {"LIVE", "ENDED"}
    for s in out["sessions"]:
        assert type(s["state_error"]) is bool and s["state_error"] is False
