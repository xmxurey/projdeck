"""Spec tests for T07: agent.act_live marks every session with a boolean state_error.

state_error is true only when the session state could not be read reliably: a transcript read
raising something other than FileNotFoundError, or the whole-session fallback branch.
Replay warnings (TaskUpdate unknown taskId, ledger unknown status) must NOT set it.
"""
import json
import os

from projdeck import agent

from tests.test_agent_live_spec import (  # noqa: F401  (fixtures and helpers reused as-is)
    DAY, agent_entry, asst, by_id, env, human, ledger, simple_recs, tuse, write,
)


def _two(env_, bad_name="bad"):
    repo = env_.repo()
    n = env_.now
    bad = env_.session(repo, bad_name, simple_recs(env_, repo), mtime=n - 100)
    env_.session(repo, "good", simple_recs(env_, repo), mtime=n - 100)
    env_.agents([agent_entry(bad_name, repo, n - 500), agent_entry("good", repo, n - 400)])
    return repo, bad


def test_ac1_transcript_path_is_directory(env):
    repo = env.repo()
    n = env.now
    bad = env.session(repo, "bad", simple_recs(env, repo), mtime=n - 100)
    env.session(repo, "good", simple_recs(env, repo), mtime=n - 100)
    os.remove(bad)
    os.makedirs(bad)  # reading a directory raises IsADirectoryError, not FileNotFoundError
    env.agents([agent_entry("bad", repo, n - 500), agent_entry("good", repo, n - 400)])
    ss = by_id(env.live([repo]))
    assert ss["bad"]["state_error"] is True
    assert ss["bad"]["errors"], "the read failure must still be recorded"
    assert ss["good"]["state_error"] is False
    assert ss["good"]["errors"] == []


def test_ac1_unreadable_permission_error(env):
    repo, bad = _two(env)
    env.mp.setattr(agent, "transcript_events",
                   _raiser(agent.transcript_events, bad, PermissionError("denied")))
    ss = by_id(env.live([repo]))
    assert ss["bad"]["state_error"] is True
    assert ss["bad"]["errors"] == ["transcript %s: denied" % bad]  # the read path, not the fallback
    assert ss["bad"]["name"] == "bad"  # assembled from the agent entry: not the fallback record
    assert ss["good"]["state_error"] is False


def test_ac1_file_not_found_is_not_a_state_error(env):
    repo, bad = _two(env)
    env.mp.setattr(agent, "transcript_events",
                   _raiser(agent.transcript_events, bad, FileNotFoundError("gone")))
    ss = by_id(env.live([repo]))
    assert ss["good"]["state_error"] is False
    if "bad" in ss:  # a vanished transcript is not a failed read
        assert ss["bad"]["state_error"] is False


def _raiser(orig, bad, exc):
    def boom(path, *a, **k):
        if os.path.abspath(path) == os.path.abspath(bad):
            raise exc
        return orig(path, *a, **k)
    return boom


def test_ac2_assembly_exception_goes_to_fallback(env):
    # the transcript reads fine; assembling the session raises, so the whole-session except
    # builds the fallback record (blank name, no segments) and says which session failed
    repo, _ = _two(env)
    orig = agent.session_state

    def boom(srec, *a, **k):
        if srec.get("sessionId") == "bad":
            raise RuntimeError("kaboom")
        return orig(srec, *a, **k)

    env.mp.setattr(agent, "session_state", boom)
    ss = by_id(env.live([repo]))
    assert ss["bad"]["state_error"] is True
    assert ss["bad"]["errors"] == ["session bad: kaboom"]
    assert ss["bad"]["name"] == "" and ss["bad"]["segments"] == []
    assert ss["good"]["state_error"] is False and ss["good"]["errors"] == []


def test_ac3_taskupdate_unknown_taskid_only_warns(env):
    repo = env.repo()
    n = env.now
    recs = [human(n - 300, "plan", repo),
            tuse(n - 290, "t1", "TaskUpdate", {"taskId": "99", "status": "completed"}, repo),
            asst(n - 280, "working", repo)]
    env.session(repo, "s", recs, mtime=n - 60)
    env.agents([agent_entry("s", repo, n - 300, status="busy")])
    s = by_id(env.live([repo]))["s"]
    assert s["errors"] == ["TaskUpdate: unknown taskId 99"]
    assert s["state_error"] is False


def test_ac3_ledger_unknown_status_only_warns(env):
    repo = env.repo()
    ledger(repo, [("T01", "one")])
    sp = os.path.join(repo, "docs", "dev", "runs", "2026-11-18-demo", "state.json")
    d = json.load(open(sp))
    d["tasks"][0]["status"] = "bogus_status"
    write(sp, json.dumps(d))
    env.session(repo, "s", simple_recs(env, repo), mtime=env.now - 60)
    env.agents([agent_entry("s", repo, env.now - 300, status="busy")])
    s = by_id(env.live([repo]))["s"]
    assert s["errors"] == ["run ledger %s: unknown status 'bogus_status' for T01" % sp]
    assert s["state_error"] is False


def test_ac3_warning_session_does_not_taint_neighbour(env):
    repo = env.repo()
    n = env.now
    recs = [human(n - 300, "plan", repo),
            tuse(n - 290, "t1", "TaskUpdate", {"taskId": "99", "status": "completed"}, repo)]
    env.session(repo, "warn", recs, mtime=n - 60)
    env.session(repo, "ok", simple_recs(env, repo), mtime=n - 60)
    env.agents([agent_entry("warn", repo, n - 300), agent_entry("ok", repo, n - 200)])
    ss = by_id(env.live([repo]))
    assert ss["warn"]["state_error"] is False
    assert ss["ok"]["state_error"] is False


def test_ac4_every_session_has_bool_state_error(env):
    repo = env.repo()
    n = env.now
    env.session(repo, "live1", simple_recs(env, repo), mtime=n - 100)
    env.session(repo, "ended1", simple_recs(env, repo, age=7200), mtime=n - 7000)
    env.agents([agent_entry("live1", repo, n - 500)])
    out = env.live([repo])
    assert {"live1", "ended1"} <= set(by_id(out))
    for s in out["sessions"]:
        assert "state_error" in s
        assert type(s["state_error"]) is bool
        assert s["state_error"] is False


def test_ac4_all_sessions_boolean_even_when_some_fail(env):
    repo, bad = _two(env)
    env.mp.setattr(agent, "transcript_events",
                   _raiser(agent.transcript_events, bad, RuntimeError("x")))
    out = env.live([repo])
    assert out["sessions"]
    for s in out["sessions"]:
        assert type(s["state_error"]) is bool


def test_ac4_empty_collection_has_no_sessions(env):
    repo = env.repo()
    assert env.live([repo])["sessions"] == []
