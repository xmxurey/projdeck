"""Spec-derived tests for T10: agent.act_live ("live" action) and zellij_session_list.

Written from the brief / design 3.1.9, 3.12-3.13 and the rulings, without reading the implementation.
act_live takes no injectable "now" (collected_at is its own clock), so timestamps are relative to
time.time() taken at the start of each test.
"""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from projdeck import agent, sync


def run_git(path, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "-C", str(path)] + list(args), check=True, env=env,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def write(path, content="x"):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w") as f:
        f.write(content)

DAY = 86400


# ---------------------------------------------------------------- helpers

def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".000Z"


def urow(ts, content, cwd, entry="cli", **extra):
    d = {"type": "user", "timestamp": iso(ts), "cwd": cwd, "entrypoint": entry,
         "message": {"role": "user", "content": content}}
    d.update(extra)
    return d


def human(ts, text, cwd, entry="cli"):
    return urow(ts, text, cwd, entry, origin={"kind": "human"})


def asst(ts, blocks, cwd):
    if isinstance(blocks, str):
        blocks = [{"type": "text", "text": blocks}]
    return {"type": "assistant", "timestamp": iso(ts), "cwd": cwd,
            "message": {"role": "assistant", "content": blocks}}


def tuse(ts, tid, name, inp, cwd):
    return asst(ts, [{"type": "tool_use", "id": tid, "name": name, "input": inp}], cwd)


def stub(bindir, name, body):
    bindir.mkdir(exist_ok=True)
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return p


def agent_entry(sid, cwd, started_s, kind="interactive", status="idle", name=None, **kw):
    e = {"id": "ag-" + sid, "cwd": cwd, "kind": kind, "startedAt": int(started_s * 1000),
         "sessionId": sid}
    if kind == "interactive":
        e.update({"pid": 990000 + abs(hash(sid)) % 9000, "status": status, "name": name or sid})
    else:
        e["state"] = kw.pop("state", "running")
    e.update(kw)
    return e


class Env:
    def __init__(self, tmp_path, monkeypatch, make_repo, make_session):
        self.tmp = tmp_path
        self.home = os.environ["HOME"]
        self.cd = os.path.join(self.home, ".claude")
        os.makedirs(self.cd, exist_ok=True)
        self.bin = tmp_path / "stubs"
        self.bin.mkdir()
        self.mp = monkeypatch
        self.make_repo = make_repo
        self.make_session = make_session
        self.now = time.time()
        monkeypatch.setenv("PATH", str(self.bin) + os.pathsep + os.environ["PATH"])
        self.agents([])

    def agents(self, entries):
        f = self.tmp / "agents.json"
        f.write_text(json.dumps(entries))
        stub(self.bin, "claude", "/bin/cat '%s'\n" % f)

    def zellij(self, lines, code=0):
        f = self.tmp / "zellij_out.txt"
        f.write_text("".join(l + "\n" for l in lines))
        self.zlog = self.tmp / "zellij_calls.txt"
        stub(self.bin, "zellij",
             "echo \"$@\" >> '%s'\n/bin/cat '%s'\nexit %d\n" % (self.zlog, f, code))

    def repo(self, name="proj"):
        return self.make_repo(os.path.join(self.home, name), {"a.py": "1"})

    def session(self, cwd, sid, records, mtime):
        return self.make_session(self.cd, cwd, sid, records, mtime=mtime)

    def req(self, paths, **kw):
        r = {"action": "live", "host": "t", "claude_dir": self.cd, "paths": list(paths),
             "since": int(self.now - DAY)}
        r.update(kw)
        return r

    def live(self, paths, memo=None, **kw):
        return agent.act_live(self.req(paths, **kw), memo)


@pytest.fixture
def env(tmp_path, monkeypatch, make_repo, make_session):
    return Env(tmp_path, monkeypatch, make_repo, make_session)


def by_id(out):
    return {s["id"]: s for s in out["sessions"]}


def simple_recs(env_, cwd, age=300, reply="ok done."):
    t = env_.now - age
    return [human(t, "do the thing", cwd), asst(t + 5, reply, cwd)]


def ledger(root, titles, run_id="2026-11-18-demo"):
    runs = os.path.join(str(root), "docs", "dev", "runs")
    write(os.path.join(runs, ".active"), run_id + "\n")
    tasks = [{"id": i, "title": t, "status": "in_progress", "round": 0, "stage": "impl"}
             for i, t in titles]
    write(os.path.join(runs, run_id, "state.json"),
          json.dumps({"run_id": run_id, "kind": "dev", "mode": None, "tasks": tasks}))


# ---------------------------------------------------------------- AC1

def test_ac1_waiting_live_ended_and_sdk_cli(env):
    repo = env.repo()
    n = env.now
    env.session(repo, "live1", [human(n - 3 * DAY, "go", repo),
                                asst(n - 3 * DAY + 30, "Part done.\n\nShall we go on?", repo)],
                mtime=n - 3 * DAY)
    env.session(repo, "ended1", simple_recs(env, repo, age=7200), mtime=n - 7000)
    env.session(repo, "sdk1", [human(n - 200, "hi", repo, entry="sdk-cli"),
                               asst(n - 190, "yo", repo)], mtime=n - 100)
    env.agents([agent_entry("live1", repo, n - 3 * DAY, status="idle")])
    out = env.live([repo])
    assert out["schema"] == 1
    ss = by_id(out)
    assert "sdk1" not in ss
    assert "live1" in ss and "ended1" in ss
    assert ss["live1"]["live"] is True
    assert ss["live1"]["state"] == "waiting"
    assert "Shall we go on?" in ss["live1"]["question"]
    assert ss["ended1"]["live"] is False
    assert ss["live1"]["project"] == repo
    assert ss["live1"]["kind"] == "interactive"


def test_output_shape_and_collected_at(env):
    repo = env.repo()
    env.session(repo, "s1", simple_recs(env, repo), mtime=env.now - 200)
    before = time.time()
    out = env.live([repo])
    after = time.time()
    assert before - 1 <= out["collected_at"] <= after + 1
    assert out["host"] == "t"
    assert out["since"] == int(env.now - DAY)
    json.dumps(out)  # serialisable
    assert "task_uses" not in json.dumps(out)
    s = by_id(out)["s1"]
    for k in ("id", "project", "cwd", "latest_cwd", "kind", "live", "started_at", "state",
              "last_human", "segments", "tasks", "tasks_owner", "errors", "subagents_running"):
        assert k in s, k
    assert s["errors"] == []


def test_default_since_is_a_day_before_now(env):
    repo = env.repo()
    env.session(repo, "old", simple_recs(env, repo, age=2 * DAY), mtime=env.now - 2 * DAY)
    env.session(repo, "new", simple_recs(env, repo, age=600), mtime=env.now - 500)
    req = env.req([repo])
    del req["since"]
    out = agent.act_live(req)
    assert abs(out["since"] - (out["collected_at"] - DAY)) <= 5
    assert set(by_id(out)) == {"new"}


def test_since_filters_nonlive_transcripts_by_mtime(env):
    repo = env.repo()
    env.session(repo, "inside", simple_recs(env, repo, age=1000), mtime=env.now - 900)
    env.session(repo, "outside", simple_recs(env, repo, age=5000), mtime=env.now - 4000)
    out = env.live([repo], since=int(env.now - 2000))
    assert set(by_id(out)) == {"inside"}


def test_live_session_without_recent_mtime_kept_regardless_of_since(env):
    repo = env.repo()
    env.session(repo, "liveold", simple_recs(env, repo, age=10 * DAY), mtime=env.now - 10 * DAY)
    env.agents([agent_entry("liveold", repo, env.now - 10 * DAY)])
    assert "liveold" in by_id(env.live([repo]))


def test_session_outside_home_and_unknown_excluded(env):
    repo = env.repo()
    elsewhere = str(env.tmp / "elsewhere")
    os.makedirs(elsewhere)
    env.session(elsewhere, "away", simple_recs(env, elsewhere), mtime=env.now - 100)
    env.session(repo, "home", simple_recs(env, repo), mtime=env.now - 100)
    ss = by_id(env.live([repo]))
    assert "away" not in ss and "home" in ss


def test_session_outside_home_but_in_known_project_kept(env):
    elsewhere = env.make_repo(str(env.tmp / "elsewhere"), {"a": "1"})
    env.session(elsewhere, "known", simple_recs(env, elsewhere), mtime=env.now - 100)
    ss = by_id(env.live([elsewhere]))
    assert ss["known"]["project"] == elsewhere


def test_project_attribution_owner_and_git_root_fallback(env):
    repo = env.repo("proj")
    sub = os.path.join(repo, "sub")
    os.makedirs(sub)
    other = env.repo("other")  # git repo under home but not in paths
    env.session(sub, "insub", simple_recs(env, sub), mtime=env.now - 100)
    env.session(other, "unlisted", simple_recs(env, other), mtime=env.now - 100)
    ss = by_id(env.live([repo]))
    assert ss["insub"]["project"] == repo
    assert ss["unlisted"]["project"] == other


def test_duplicate_session_id_keeps_interactive(env):
    repo = env.repo()
    env.session(repo, "dup", simple_recs(env, repo), mtime=env.now - 100)
    env.agents([agent_entry("dup", repo, env.now - 500, kind="background"),
                agent_entry("dup", repo, env.now - 500, kind="interactive")])
    out = env.live([repo])
    assert [s["id"] for s in out["sessions"]].count("dup") == 1
    assert by_id(out)["dup"]["kind"] == "interactive"


def test_agent_without_session_id_skipped(env):
    repo = env.repo()
    e = agent_entry("x", repo, env.now - 100)
    del e["sessionId"]
    env.agents([e])
    assert env.live([repo])["sessions"] == []


def test_no_sessions_gives_empty(env):
    out = env.live([env.repo()])
    assert out["schema"] == 1 and out["sessions"] == [] and out["projects"] == {}


def test_text_fields_capped_at_300(env):
    repo = env.repo()
    n = env.now
    env.session(repo, "big", [human(n - 100, "q" * 1000, repo),
                              asst(n - 90, "r" * 998 + "?", repo)], mtime=n - 90)
    env.agents([agent_entry("big", repo, n - 100, status="idle")])
    s = by_id(env.live([repo]))["big"]
    assert len(s["last_human"]["text"]) <= 300
    assert len(s["last_reply"]) <= 300
    assert len(s["question"]) <= 300


def test_segments_clipped_to_since_and_collected_at(env):
    repo = env.repo()
    n = env.now
    env.session(repo, "seg", [human(n - 4000, "a", repo), asst(n - 3900, "b", repo),
                              human(n - 1000, "c", repo), asst(n - 900, "d", repo)],
                mtime=n - 900)
    env.agents([agent_entry("seg", repo, n - 4000, status="busy")])
    since = int(n - 2000)
    out = env.live([repo], since=since)
    s = by_id(out)["seg"]
    for kind, a, b in s["segments"]:
        assert kind in ("busy", "wait", "wait_now", "sub")
        assert since <= a <= b <= out["collected_at"] + 1


# ---------------------------------------------------------------- AC2 ownership

def test_two_live_sessions_same_checkout_earlier_owns_tasks(env):
    repo = env.repo()
    ledger(repo, [("T01", "root task")])
    n = env.now
    env.session(repo, "later", simple_recs(env, repo, age=200), mtime=n - 100)
    env.session(repo, "earlier", simple_recs(env, repo, age=400), mtime=n - 100)
    env.agents([agent_entry("later", repo, n - 1000), agent_entry("earlier", repo, n - 5000)])
    ss = by_id(env.live([repo]))
    assert ss["earlier"]["tasks"] is not None
    assert [i["id"] for i in ss["earlier"]["tasks"]["items"]] == ["T01"]
    assert ss["later"]["tasks"] is None
    assert ss["later"]["tasks_owner"] == "earlier"


def test_ownership_independent_of_listing_order(env):
    repo = env.repo()
    ledger(repo, [("T01", "root task")])
    n = env.now
    env.session(repo, "a", simple_recs(env, repo), mtime=n - 100)
    env.session(repo, "b", simple_recs(env, repo), mtime=n - 100)
    env.agents([agent_entry("a", repo, n - 5000), agent_entry("b", repo, n - 1000)])
    ss = by_id(env.live([repo]))
    assert ss["a"]["tasks"] is not None and ss["b"]["tasks"] is None
    env.agents([agent_entry("b", repo, n - 1000), agent_entry("a", repo, n - 5000)])
    ss = by_id(env.live([repo]))
    assert ss["a"]["tasks"] is not None and ss["b"]["tasks"] is None


def test_worktree_and_root_each_own_their_ledger(env):
    repo = env.repo()
    wt = os.path.join(repo, ".worktrees", "w1")
    run_git(repo, "worktree", "add", "-q", "-b", "w1", wt)
    ledger(repo, [("R01", "root task")])
    ledger(wt, [("W01", "worktree task")])
    n = env.now
    env.session(wt, "wtsess", simple_recs(env, wt), mtime=n - 100)
    env.session(repo, "rootsess", simple_recs(env, repo), mtime=n - 100)
    env.agents([agent_entry("wtsess", wt, n - 9000), agent_entry("rootsess", repo, n - 1000)])
    ss = by_id(env.live([repo]))
    assert [i["id"] for i in ss["wtsess"]["tasks"]["items"]] == ["W01"]
    assert [i["id"] for i in ss["rootsess"]["tasks"]["items"]] == ["R01"]


def test_broken_ledger_error_goes_to_session_errors_once(env):
    repo = env.repo()
    runs = os.path.join(repo, "docs", "dev", "runs")
    write(os.path.join(runs, ".active"), "r1\n")
    write(os.path.join(runs, "r1", "state.json"), "{not json")
    env.session(repo, "s", simple_recs(env, repo), mtime=env.now - 100)
    env.agents([agent_entry("s", repo, env.now - 500)])
    out = env.live([repo])
    errs = by_id(out)["s"]["errors"]
    assert errs, "bad ledger must surface as an error"
    assert len(errs) == len(set(map(json.dumps, errs)))


# ---------------------------------------------------------------- AC5 subagents / in-session tasks

def test_subagent_task_create_not_in_main_tasks(env):
    repo = env.repo()
    n = env.now
    recs = [human(n - 300, "plan", repo),
            tuse(n - 290, "t1", "TaskCreate", {"subject": "main task", "description": "d"}, repo),
            asst(n - 280, "working", repo)]
    fp = env.session(repo, "mainsess", recs, mtime=n - 60)
    subdir = os.path.join(fp[:-len(".jsonl")], "subagents")
    os.makedirs(subdir)
    sub_recs = [human(n - 250, "sub", repo),
                tuse(n - 240, "u1", "TaskCreate", {"subject": "sub task", "description": "d"}, repo)]
    jp = os.path.join(subdir, "agent-1.jsonl")
    with open(jp, "w") as f:
        for r in sub_recs:
            f.write(json.dumps(r) + "\n")
    with open(os.path.join(subdir, "agent-1.meta.json"), "w") as f:
        json.dump({"finished": False}, f)
    os.utime(jp, (n - 30, n - 30))
    env.agents([agent_entry("mainsess", repo, n - 300, status="busy")])
    s = by_id(env.live([repo]))["mainsess"]
    assert s["tasks"] is not None
    titles = [i["title"] for i in s["tasks"]["items"]]
    assert titles == ["main task"]
    assert s["subagents_running"] == 1


# ---------------------------------------------------------------- per-session errors

def test_exception_in_one_session_does_not_break_host(env):
    repo = env.repo()
    n = env.now
    bad = env.session(repo, "bad", simple_recs(env, repo), mtime=n - 100)
    env.session(repo, "good", simple_recs(env, repo), mtime=n - 100)
    env.agents([agent_entry("bad", repo, n - 500), agent_entry("good", repo, n - 400)])
    orig = agent.transcript_events

    def boom(path, *a, **k):
        if os.path.abspath(path) == os.path.abspath(bad):
            raise RuntimeError("kaboom")
        return orig(path, *a, **k)

    env.mp.setattr(agent, "transcript_events", boom)
    out = env.live([repo])
    ss = by_id(out)
    assert out["schema"] == 1
    assert "good" in ss and ss["good"]["errors"] == []
    assert "bad" in ss and ss["bad"]["errors"]  # live session kept, error recorded


# ---------------------------------------------------------------- AC3 memo

def counting(env_, calls):
    orig = agent.transcript_events

    def wrapper(path, *a, **k):
        calls.append(os.path.abspath(path))
        return orig(path, *a, **k)

    env_.mp.setattr(agent, "transcript_events", wrapper)


def test_memo_skips_unchanged_and_reparses_changed(env):
    repo = env.repo()
    n = env.now
    p1 = env.session(repo, "m1", simple_recs(env, repo), mtime=n - 100)
    p2 = env.session(repo, "m2", simple_recs(env, repo), mtime=n - 100)
    calls = []
    counting(env, calls)
    memo = {}
    env.live([repo], memo)
    assert sorted(calls) == sorted([os.path.abspath(p1), os.path.abspath(p2)])
    assert len(memo) == 2
    calls[:] = []
    out = env.live([repo], memo)
    assert calls == []
    assert set(by_id(out)) == {"m1", "m2"}
    with open(p1, "a") as f:
        f.write(json.dumps(asst(n - 50, "more text", repo)) + "\n")
    calls[:] = []
    env.live([repo], memo)
    assert calls == [os.path.abspath(p1)]


def test_memo_drops_unused_keys(env):
    repo = env.repo()
    n = env.now
    env.session(repo, "k1", simple_recs(env, repo), mtime=n - 100)
    p2 = env.session(repo, "k2", simple_recs(env, repo), mtime=n - 100)
    memo = {("/nonexistent/x.jsonl", 1, 2): {"junk": True}}
    env.live([repo], memo)
    assert ("/nonexistent/x.jsonl", 1, 2) not in memo
    assert len(memo) == 2
    os.remove(p2)
    env.live([repo], memo)
    assert len(memo) == 1
    assert all(k[0] != p2 for k in memo)


def test_memo_none_is_fine(env):
    repo = env.repo()
    env.session(repo, "s", simple_recs(env, repo), mtime=env.now - 100)
    assert "s" in by_id(env.live([repo], None))


# ---------------------------------------------------------------- AC4 projects / ssh / zellij

def test_projects_only_with_sessions_and_commit_info(env, monkeypatch):
    home = env.home
    repo = os.path.join(home, "proj")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2020-01-01T00:00:00")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2020-01-01T00:00:00")
    env.make_repo(repo, {"a.py": "1"})
    monkeypatch.delenv("GIT_COMMITTER_DATE")
    monkeypatch.delenv("GIT_AUTHOR_DATE")
    write(os.path.join(repo, "b.py"), "2")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "recent commit")
    quiet = env.repo("quiet")
    env.session(repo, "s", simple_recs(env, repo), mtime=env.now - 100)
    out = env.live([repo, quiet])
    assert list(out["projects"]) == [repo]
    p = out["projects"][repo]
    assert p["last_commit"]["subject"] == "recent commit"
    assert abs(p["last_commit"]["ts"] - env.now) < 120
    assert len(p["commits"]) == 1  # the 2020 commit is before since
    assert abs(p["commits"][0] - env.now) < 120


def test_ssh_path_returns_schema1_with_projects(env, fake_ssh, monkeypatch):
    monkeypatch.setenv("HOME", env.home)
    repo = env.repo()
    env.session(repo, "s", simple_recs(env, repo), mtime=env.now - 100)
    out = sync.run_agent({"name": "vm", "ssh": "vm"}, env.req([repo]))
    assert out["schema"] == 1
    assert "s" in by_id(out)
    assert out["projects"][repo]["last_commit"]["subject"] == "init"
    assert len(out["projects"][repo]["commits"]) >= 1


def test_ssh_minimal_request(env, fake_ssh):
    out = sync.run_agent({"name": "vm", "ssh": "vm"}, {"action": "live"})
    assert out["schema"] == 1
    assert out["sessions"] == []


def test_live_registered_in_actions():
    assert agent.ACTIONS["live"] is agent.act_live


def test_zellij_exited_sorted_names(env):
    env.zellij(["zeta [Created 3months ago] (EXITED - attach to resurrect)",
                "proj-x [Created 2h ago]",
                "dao [Created 3months ago] (EXITED - attach to resurrect)"])
    out = env.live([env.repo()], zellij=True)
    assert out["zellij_exited"] == ["dao", "zeta"]


def test_zellij_exited_none_without_flag(env):
    env.zellij(["dao [Created 3months ago] (EXITED - attach to resurrect)"])
    repo = env.repo()
    assert env.live([repo]).get("zellij_exited") is None
    assert env.live([repo], zellij=False).get("zellij_exited") is None


def minimal_path(env_, with_zellij=False):
    d = env_.tmp / "minbin"
    d.mkdir(exist_ok=True)
    for name in ("git", "sh", "cat", "python3"):
        src = shutil.which(name)
        if src and not (d / name).exists():
            os.symlink(src, str(d / name))
    return str(d)


def test_zellij_exited_none_when_zellij_missing(env):
    repo = env.repo()
    env.mp.setenv("PATH", minimal_path(env))
    out = env.live([repo], zellij=True)
    assert out["schema"] == 1
    assert out.get("zellij_exited") is None


def test_zellij_exited_empty_list_when_no_exited(env):
    env.zellij(["proj-x [Created 2h ago]"])
    assert env.live([env.repo()], zellij=True)["zellij_exited"] == []


# ---------------------------------------------------------------- zellij_session_list

def test_zellij_session_list_parses_three_lines(env):
    env.zellij(["dao [Created 3months ago] (EXITED - attach to resurrect)",
                "proj-quant [Created 2h ago]",
                "projdeck [Created 2m ago] (current)"])
    r = agent.zellij_session_list()
    assert [(x["name"], x["exited"]) for x in r] == [
        ("dao", True), ("proj-quant", False), ("projdeck", False)]
    calls = env.zlog.read_text().split("\n")
    calls = [c for c in calls if c.strip()]
    assert calls and all(c.split()[0] == "list-sessions" for c in calls)


def test_zellij_session_list_no_sessions_nonzero_is_empty(env):
    env.zellij(["No active zellij sessions found."], code=1)
    assert agent.zellij_session_list() == []


def test_zellij_session_list_missing_binary_none(env):
    env.mp.setenv("PATH", minimal_path(env))
    assert agent.zellij_session_list() is None


def test_zellij_session_list_timeout_none(env):
    stub(env.bin, "zellij", "exec /bin/sleep 8\n")
    t0 = time.time()
    assert agent.zellij_session_list(timeout=0.5) is None
    assert time.time() - t0 < 6


def test_zellij_session_list_default_run_and_timeout_signature():
    import inspect
    p = inspect.signature(agent.zellij_session_list).parameters
    assert list(p)[:2] == ["run", "timeout"]
    assert p["timeout"].default == 5


# ---------------------------------------------------------------- review round 1: top-level errors

def git_fails_on_log(env_):
    real = shutil.which("git")
    stub(env_.bin, "git",
         "for a in \"$@\"; do\n  if [ \"$a\" = log ]; then echo 'fatal: boom' >&2; exit 128; fi\n"
         "done\nexec %s \"$@\"\n" % real)


def test_git_log_failure_gives_none_commits_and_top_error(env):
    repo = env.repo()
    env.session(repo, "s", simple_recs(env, repo), mtime=env.now - 100)
    git_fails_on_log(env)
    out = env.live([repo])
    assert "s" in by_id(out)
    assert out["projects"][repo]["commits"] is None
    assert out["errors"]
    assert all(isinstance(e, str) for e in out["errors"])


def test_empty_repo_gives_empty_commits_without_error(env):
    repo = env.make_repo(os.path.join(env.home, "empty"), commit=False)
    env.session(repo, "s", simple_recs(env, repo), mtime=env.now - 100)
    out = env.live([repo])
    assert out["projects"][repo]["commits"] == []
    assert not out.get("errors")


def test_healthy_run_has_no_top_level_errors(env):
    repo = env.repo()
    env.session(repo, "s", simple_recs(env, repo), mtime=env.now - 100)
    assert not env.live([repo]).get("errors")


def test_unreadable_ended_transcript_goes_to_top_errors(env):
    repo = env.repo()
    env.session(repo, "good", simple_recs(env, repo), mtime=env.now - 100)
    bad = env.session(repo, "bad", simple_recs(env, repo), mtime=env.now - 100)
    os.chmod(bad, 0)
    try:
        if os.access(bad, os.R_OK):
            pytest.skip("cannot make file unreadable (root?)")
        out = env.live([repo])
    finally:
        os.chmod(bad, 0o644)
    ss = by_id(out)
    assert "bad" not in ss and "good" in ss
    assert out["errors"]


def test_parse_exception_on_ended_session_goes_to_top_errors(env):
    repo = env.repo()
    env.session(repo, "good", simple_recs(env, repo), mtime=env.now - 100)
    bad = env.session(repo, "bad", simple_recs(env, repo), mtime=env.now - 100)
    orig = agent.transcript_events

    def boom(path, *a, **k):
        if os.path.abspath(path) == os.path.abspath(bad):
            raise ValueError("cannot parse")
        return orig(path, *a, **k)

    env.mp.setattr(agent, "transcript_events", boom)
    out = env.live([repo])
    assert "good" in by_id(out)
    assert out["errors"]


def test_transcript_deleted_after_listdir_is_silent(env):
    repo = env.repo()
    env.session(repo, "good", simple_recs(env, repo), mtime=env.now - 100)
    gone = env.session(repo, "gone", simple_recs(env, repo), mtime=env.now - 100)
    real = os.listdir
    hit = []

    def listdir(path="."):
        res = real(path)
        if os.path.basename(gone) in res and os.path.abspath(str(path)) == os.path.dirname(gone):
            os.remove(gone)
            hit.append(1)
        return res

    env.mp.setattr(os, "listdir", listdir)
    out = env.live([repo])
    if not hit:
        pytest.skip("implementation does not index via os.listdir; race not reproducible")
    ss = by_id(out)
    assert "gone" not in ss and "good" in ss
    assert not out.get("errors")


def test_live_agents_exception_still_returns_with_top_error(env):
    repo = env.repo()
    env.session(repo, "ended", simple_recs(env, repo), mtime=env.now - 100)

    def boom(*a, **k):
        raise RuntimeError("agents exploded")

    env.mp.setattr(agent, "live_agents", boom)
    out = env.live([repo])
    assert out["schema"] == 1
    assert out["errors"]
    assert "ended" in by_id(out)


def test_attach_map_exception_still_returns_with_top_error(env):
    repo = env.repo()
    env.session(repo, "s", simple_recs(env, repo), mtime=env.now - 100)
    env.agents([agent_entry("s", repo, env.now - 500)])

    def boom(*a, **k):
        raise RuntimeError("attach exploded")

    env.mp.setattr(agent, "attach_map", boom)
    out = env.live([repo])
    assert out["errors"]
    s = by_id(out)["s"]
    assert s["live"] is True
    assert not s.get("attach")
