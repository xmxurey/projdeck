"""Spec-derived tests for serve.Refresher and push_statusline (T24)."""
import copy
import json
import os
import stat
import sys
import threading
import time

import pytest

from projdeck import board, config, serve, sync

T0 = 1_800_000_000
BOARD_KEYS = ["hosts", "summary", "waiting", "busy", "idle", "ended_today", "stale",
              "stale_host_sessions", "lanes", "top_todos", "backlogs", "tiles"]


def make_cfg(push=True, auto=30):
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["hosts"] = [{"name": "loc", "local": True}, {"name": "s-host", "ssh": "s-host"}]
    cfg["auto_sync_minutes"] = auto
    cfg["serve"] = dict(cfg["serve"], push_statusline=push)
    return cfg


def sess(i, state="waiting", project="/home/u/proj", idle_since=T0 - 100):
    return {"id": i, "project": project, "cwd": project, "latest_cwd": project,
            "kind": "interactive", "live": True, "name": "n-" + i, "title": "",
            "started_at": T0 - 1000, "status": "idle", "state": state,
            "idle_since": idle_since, "question": "", "last_reply": "",
            "last_human": {"ts": 0, "text": ""}, "last_input": "human",
            "subagents_running": 0, "segments": [], "attach": "", "tasks": None,
            "tasks_owner": None, "errors": []}


def live_doc(host, sessions, at):
    return {"schema": 1, "host": host, "collected_at": at, "source": "claude agents",
            "since": at - 86400, "sessions": sessions, "projects": {}}


def cache_doc(host, synced):
    return {"schema": 1, "host": host, "collected_at": synced, "synced_at": synced,
            "stale_since": None, "last_error": None, "projects": []}


class Fakes:
    def __init__(self):
        self.live_calls = []      # (host name, now, memo)
        self.all_calls = []
        self.pushes = []
        self.waiting = []         # ids of locally waiting sessions
        self.block = {}           # host name -> Event that must be set to proceed
        self.entered = {}         # host name -> Event set when the fake is entered
        self.push_error = None
        self.remote_error = None
        self.lock = threading.Lock()

    def sync_live(self, host, cfg, now=None, memo=None):
        name = host["name"]
        with self.lock:
            self.live_calls.append((name, now, memo))
        self.entered.setdefault(name, threading.Event()).set()
        if name in self.block:
            assert self.block[name].wait(20)
        if name != "loc" and self.remote_error:
            raise RuntimeError(self.remote_error)
        sessions = []
        if name == "loc":
            sessions = [sess(i, idle_since=T0 - 500 + n) for n, i in enumerate(self.waiting)]
        sync._write_json(sync._live_path(name), live_doc(name, sessions, now))
        return {"host": name, "ok": True, "error": None}

    def sync_all(self, cfg):
        with self.lock:
            self.all_calls.append(1)
        return []

    def push(self, doc):
        self.pushes.append(copy.deepcopy(doc))
        if self.push_error:
            raise self.push_error

    def locals(self):
        return [c for c in self.live_calls if c[0] == "loc"]

    def remotes(self):
        return [c for c in self.live_calls if c[0] == "s-host"]


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    monkeypatch.setenv("PROJ_CACHE", str(d))
    return d


@pytest.fixture
def fk():
    return Fakes()


def seed_collect(age):
    """Collect caches synced `age` seconds before T0 (None: no caches at all)."""
    if age is not None:
        for h in ("loc", "s-host"):
            sync._write_cache(h, cache_doc(h, T0 - age))


def make(fk, cfg=None, clock=None):
    r = serve.Refresher(cfg or make_cfg(), clock or (lambda: T0), fk.sync_live, fk.sync_all, fk.push)
    return r


def tick(r, now):
    r.tick(now)
    assert r.wait_idle(20)


def state_doc(cache_dir):
    with open(str(cache_dir / "live" / "statusline.state")) as f:
        return json.load(f)


# ---------- 1. cadence ----------

def test_first_tick_runs_local_and_remote_with_memo_only_local(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    assert len(fk.locals()) == 1 and len(fk.remotes()) == 1
    assert fk.locals()[0][2] is not None      # local gets a memo
    assert fk.remotes()[0][2] is None         # remote gets none


def test_local_memo_is_the_same_object_across_ticks(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    tick(r, T0 + 16)
    assert len(fk.locals()) == 2
    assert fk.locals()[0][2] is fk.locals()[1][2]


def test_local_refresh_every_15s(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    tick(r, T0 + 10)
    tick(r, T0 + 14)
    assert len(fk.locals()) == 1
    tick(r, T0 + 16)
    assert len(fk.locals()) == 2
    tick(r, T0 + 25)
    tick(r, T0 + 30)       # 14s after the refresh at +16
    assert len(fk.locals()) == 2
    tick(r, T0 + 32)
    assert len(fk.locals()) == 3


def test_local_receives_tick_now(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    tick(r, T0 + 16)
    assert [c[1] for c in fk.locals()] == [T0, T0 + 16]


def test_poke_refreshes_early_after_5s(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    r.poke()
    tick(r, T0 + 4)
    assert len(fk.locals()) == 1
    tick(r, T0 + 6)
    assert len(fk.locals()) == 2


def test_poke_exactly_5s_counts(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    r.poke()
    tick(r, T0 + 5)
    assert len(fk.locals()) == 2


def test_poke_stays_pending_until_5s_passed(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    r.poke()
    tick(r, T0 + 2)
    assert len(fk.locals()) == 1
    tick(r, T0 + 6)       # no new poke, the early one is still pending
    assert len(fk.locals()) == 2


def test_poke_is_consumed_by_the_refresh(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    r.poke()
    tick(r, T0 + 6)
    assert len(fk.locals()) == 2
    tick(r, T0 + 12)      # 6s after refresh, no new poke, not yet 15
    assert len(fk.locals()) == 2


def test_no_poke_no_early_refresh(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    tick(r, T0 + 8)
    assert len(fk.locals()) == 1


def test_remote_live_every_120s(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    for dt in (16, 32, 48, 64, 80, 96, 112, 119):
        tick(r, T0 + dt)
    assert len(fk.remotes()) == 1
    tick(r, T0 + 121)
    assert len(fk.remotes()) == 2
    tick(r, T0 + 200)
    assert len(fk.remotes()) == 2
    tick(r, T0 + 242)
    assert len(fk.remotes()) == 3


def test_remote_does_not_run_on_local_host_and_local_not_in_remote_pass(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    tick(r, T0 + 121)
    assert {c[0] for c in fk.live_calls} == {"loc", "s-host"}
    assert len(fk.remotes()) == 2


def test_collect_runs_immediately_when_caches_missing(cache_dir, fk):
    seed_collect(None)
    r = make(fk)
    tick(r, T0)
    assert len(fk.all_calls) == 1


def test_collect_runs_immediately_when_caches_older_than_interval(cache_dir, fk):
    seed_collect(31 * 60)
    r = make(fk)
    tick(r, T0)
    assert len(fk.all_calls) == 1


def test_collect_waits_when_caches_fresh(cache_dir, fk):
    seed_collect(600)
    r = make(fk)
    tick(r, T0)
    assert fk.all_calls == []
    tick(r, T0 + 60)
    assert fk.all_calls == []


def test_collect_counted_from_oldest_synced_at(cache_dir, fk):
    sync._write_cache("loc", cache_doc("loc", T0 - 100))
    sync._write_cache("s-host", cache_doc("s-host", T0 - 600))   # oldest: due at T0 + 1200
    r = make(fk)
    tick(r, T0)
    tick(r, T0 + 1190)
    assert fk.all_calls == []
    tick(r, T0 + 1210)
    assert len(fk.all_calls) == 1


def test_collect_repeats_every_auto_sync_minutes(cache_dir, fk):
    seed_collect(None)
    r = make(fk)
    tick(r, T0)
    assert len(fk.all_calls) == 1
    tick(r, T0 + 1700)
    assert len(fk.all_calls) == 1
    tick(r, T0 + 1810)
    assert len(fk.all_calls) == 2
    tick(r, T0 + 1810 + 1700)
    assert len(fk.all_calls) == 2
    tick(r, T0 + 1810 + 1810)
    assert len(fk.all_calls) == 3


def test_collect_interval_follows_config(cache_dir, fk):
    seed_collect(None)
    r = make(fk, make_cfg(auto=5))
    tick(r, T0)
    assert len(fk.all_calls) == 1
    tick(r, T0 + 290)
    assert len(fk.all_calls) == 1
    tick(r, T0 + 310)
    assert len(fk.all_calls) == 2


# ---------- 1b. slow remotes do not block ----------

def test_blocked_remote_does_not_block_tick_or_local(cache_dir, fk):
    seed_collect(0)
    fk.block["s-host"] = threading.Event()
    r = make(fk)
    done = []
    t = threading.Thread(target=lambda: (r.tick(T0), done.append(1)))
    t.start()
    t.join(10)
    assert done, "tick() waited for a blocked remote"
    assert fk.entered["s-host"].wait(10)
    assert len(fk.locals()) == 1
    r.tick(T0 + 16)
    r.tick(T0 + 32)
    assert len(fk.locals()) == 3
    assert r.wait_idle(0.3) is False       # the blocked remote is still running
    fk.block["s-host"].set()
    assert r.wait_idle(20) is True


def test_blocked_remote_skips_next_round_instead_of_stacking(cache_dir, fk):
    seed_collect(0)
    fk.block["s-host"] = threading.Event()
    r = make(fk)
    r.tick(T0)
    assert fk.entered["s-host"].wait(10)
    r.tick(T0 + 121)
    r.tick(T0 + 242)
    assert len(fk.remotes()) == 1
    fk.block["s-host"].set()
    assert r.wait_idle(20)
    tick(r, T0 + 400)
    assert len(fk.remotes()) == 2


def test_blocked_collect_does_not_block_and_skips(cache_dir, fk):
    seed_collect(None)
    gate = threading.Event()
    orig = fk.sync_all

    def slow(cfg):
        orig(cfg)
        assert gate.wait(20)
    r = serve.Refresher(make_cfg(), lambda: T0, fk.sync_live, slow, fk.push)
    r.tick(T0)
    r.tick(T0 + 16)
    assert len(fk.locals()) == 2
    r.tick(T0 + 2000)
    assert len(fk.all_calls) == 1
    gate.set()
    assert r.wait_idle(20)


def test_remote_failure_is_contained(cache_dir, fk, capsys):
    seed_collect(0)
    fk.remote_error = "ssh down"
    r = make(fk)
    tick(r, T0)
    tick(r, T0 + 16)
    assert len(fk.locals()) == 2
    assert r.snapshot()["hosts"]


def test_wait_idle_true_when_nothing_running(cache_dir, fk):
    r = make(fk)
    assert r.wait_idle(1) is True


# ---------- 2. statusline.state ----------

def test_state_file_equals_statusline_doc_after_each_refresh(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a", "b"]
    r = make(fk)
    for now in (T0, T0 + 16, T0 + 32):
        tick(r, now)
        assert state_doc(cache_dir) == board.statusline_doc(r.snapshot(), now)


def test_state_file_content_follows_waiting_changes(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)
    assert [w["session_id"] for w in state_doc(cache_dir)["waiting"]] == ["a"]
    fk.waiting = ["a", "b", "c"]
    tick(r, T0 + 16)
    got = state_doc(cache_dir)
    assert sorted(w["session_id"] for w in got["waiting"]) == ["a", "b", "c"]
    assert got["updated"] == T0 + 16
    fk.waiting = []
    tick(r, T0 + 32)
    assert state_doc(cache_dir)["waiting"] == []


def test_state_file_is_written_even_when_push_disabled(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a"]
    r = make(fk, make_cfg(push=False))
    tick(r, T0)
    assert os.path.exists(str(cache_dir / "live" / "statusline.state"))


def test_state_file_leaves_no_temp_files(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    tick(r, T0 + 16)
    assert [n for n in os.listdir(str(cache_dir / "live")) if n.endswith(".tmp")] == []


def test_state_file_not_rewritten_between_refreshes(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)
    fk.waiting = []
    tick(r, T0 + 5)     # not a local refresh
    assert [w["session_id"] for w in state_doc(cache_dir)["waiting"]] == ["a"]


# ---------- 3. push ----------

def waiting_ids(doc):
    return [w["session_id"] for w in doc["waiting"]]


def test_first_waiting_list_is_pushed(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)
    assert len(fk.pushes) == 1
    assert waiting_ids(fk.pushes[0]) == ["a"]


def test_unchanged_list_only_updated_differs_not_pushed_within_60s(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)
    for dt in (16, 32, 48):
        tick(r, T0 + dt)
        assert state_doc(cache_dir)["updated"] == T0 + dt    # `updated` did change
    assert len(fk.pushes) == 1


def test_heartbeat_after_60s(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)
    for dt in (16, 32, 48):
        tick(r, T0 + dt)
    assert len(fk.pushes) == 1
    tick(r, T0 + 64)
    assert len(fk.pushes) == 2
    assert waiting_ids(fk.pushes[1]) == ["a"]
    assert fk.pushes[1]["updated"] == T0 + 64


def test_heartbeat_repeats_every_60s_from_last_push(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)
    tick(r, T0 + 64)
    assert len(fk.pushes) == 2
    for dt in (80, 96, 112):
        tick(r, T0 + dt)
    assert len(fk.pushes) == 2
    tick(r, T0 + 128)
    assert len(fk.pushes) == 3


def test_changed_list_pushed_same_tick(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)
    fk.waiting = ["a", "b"]
    r.tick(T0 + 16)           # no wait_idle: push must already have happened
    assert len(fk.pushes) == 2
    assert sorted(waiting_ids(fk.pushes[1])) == ["a", "b"]
    assert r.wait_idle(20)


def test_list_emptied_is_pushed(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)
    fk.waiting = []
    tick(r, T0 + 16)
    assert len(fk.pushes) == 2
    assert fk.pushes[1]["waiting"] == []


def test_change_resets_heartbeat_clock(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)
    fk.waiting = ["a", "b"]
    tick(r, T0 + 48)                      # change push at +48
    assert len(fk.pushes) == 2
    tick(r, T0 + 64)                      # only 16s since last push
    tick(r, T0 + 80)
    tick(r, T0 + 96)
    assert len(fk.pushes) == 2
    tick(r, T0 + 112)                     # 64s since the change push
    assert len(fk.pushes) == 3


def test_pushed_doc_matches_state_file(cache_dir, fk):
    seed_collect(0)
    fk.waiting = ["a", "b"]
    r = make(fk)
    tick(r, T0)
    assert fk.pushes[0] == state_doc(cache_dir)


def test_push_disabled_never_pushes(cache_dir, fk):
    seed_collect(0)
    r = make(fk, make_cfg(push=False))
    fk.waiting = ["a"]
    tick(r, T0)
    fk.waiting = ["a", "b"]
    tick(r, T0 + 16)
    fk.waiting = []
    tick(r, T0 + 32)
    for dt in (64, 128, 256):
        tick(r, T0 + dt)
    assert fk.pushes == []


def test_push_failure_is_logged_and_does_not_stop_refresh(cache_dir, fk, capsys):
    seed_collect(0)
    fk.push_error = OSError("ssh exploded")
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)                           # must not raise
    assert len(fk.pushes) == 1
    assert capsys.readouterr().err.strip() != ""
    fk.waiting = ["a", "b"]
    tick(r, T0 + 16)
    assert len(fk.locals()) == 2
    assert sorted(w["session_id"] for w in state_doc(cache_dir)["waiting"]) == ["a", "b"]
    assert sorted(s["id"] for s in r.snapshot()["waiting"]) == ["a", "b"]


def test_push_failure_with_any_exception_type(cache_dir, fk):
    seed_collect(0)
    fk.push_error = ValueError("bad")
    fk.waiting = ["a"]
    r = make(fk)
    tick(r, T0)
    tick(r, T0 + 16)
    assert len(fk.locals()) == 2


# ---------- 4. concurrency ----------

def test_snapshot_always_complete_while_refreshing(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    tick(r, T0)
    stop = threading.Event()
    errors, reads = [], [0]

    def reader():
        while not stop.is_set():
            try:
                b = r.snapshot()
                for k in BOARD_KEYS:
                    assert k in b, k
                assert b["summary"]["waiting"] == len(b["waiting"])
                assert b["summary"]["idle"] == len(b["idle"])
                reads[0] += 1
            except BaseException as e:   # noqa
                errors.append(repr(e))
                return

    ts = [threading.Thread(target=reader) for _ in range(8)]
    for t in ts:
        t.start()
    try:
        for n in range(1, 60):
            fk.waiting = ["s%d" % i for i in range(n % 7)]
            tick(r, T0 + 16 * n)
    finally:
        stop.set()
        for t in ts:
            t.join(10)
    assert errors == []
    assert reads[0] > 0


# ---------- 5. startup with existing cache ----------

def test_first_snapshot_has_cached_data_before_any_tick(cache_dir, fk):
    seed_collect(0)
    sync._write_json(sync._live_path("loc"), live_doc("loc", [sess("cached")], T0 - 5))
    r = make(fk)
    snap = r.snapshot()
    for k in BOARD_KEYS:
        assert k in snap
    assert [s["id"] for s in snap["waiting"]] == ["cached"]
    assert fk.live_calls == [] and fk.all_calls == []


def test_snapshot_without_any_cache_is_a_valid_empty_board(cache_dir, fk):
    r = make(fk)
    snap = r.snapshot()
    for k in BOARD_KEYS:
        assert k in snap
    assert snap["waiting"] == []


def test_snapshot_uses_injected_clock(cache_dir, fk):
    seed_collect(0)
    # remote live data 700s old at clock time: stale host sessions, not waiting
    sync._write_json(sync._live_path("s-host"), live_doc("s-host", [sess("old")], T0 - 700))
    r = make(fk)
    snap = r.snapshot()
    assert snap["waiting"] == []
    assert [s["id"] for s in snap["stale_host_sessions"]] == ["old"]


# ---------- run loop ----------

def test_run_loop_ticks_and_stops(cache_dir, fk):
    seed_collect(0)
    r = make(fk)
    stop = threading.Event()
    t = threading.Thread(target=r.run, args=(stop,))
    t.start()
    try:
        end = time.time() + 20
        while time.time() < end and not fk.locals():
            time.sleep(0.02)
        assert fk.locals(), "run() never refreshed the local host"
    finally:
        stop.set()
        t.join(20)
    assert not t.is_alive()


# ---------- real push_statusline through a fake ssh ----------

@pytest.fixture
def ssh_log(tmp_path, monkeypatch):
    bindir = tmp_path / "pushbin"
    bindir.mkdir()
    log = tmp_path / "sshlog"
    log.mkdir()
    script = bindir / "ssh"
    script.write_text(
        "#!/bin/sh\n"
        "n=$$\n"
        "cat > \"" + str(log) + "/stdin.$n\"\n"
        "printf '%s\\n' \"$@\" > \"" + str(log) + "/args.$n\"\n"
        "exit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    return log


def ssh_calls(log):
    out = []
    for n in sorted(os.listdir(str(log))):
        if n.startswith("args."):
            with open(str(log / n)) as f:
                args = f.read().split("\n")
            with open(str(log / ("stdin." + n.split(".", 1)[1]))) as f:
                stdin = f.read()
            out.append((args, stdin))
    return out


def wait_calls(log, n):
    """push_statusline fans out to threads: poll (bounded) until n complete calls are logged."""
    end = time.time() + 15
    calls = ssh_calls(log)
    while len(calls) < n and time.time() < end:
        time.sleep(0.05)
        calls = ssh_calls(log)
    time.sleep(0.2)
    return ssh_calls(log)


def test_push_statusline_sends_doc_to_each_ssh_host_not_local(ssh_log):
    cfg = make_cfg()
    cfg["hosts"] = [{"name": "loc", "local": True}, {"name": "s-host", "ssh": "s-host"},
                    {"name": "em-host", "ssh": "em-host"}]
    doc = {"updated": T0, "waiting": [{"host": "x", "project": "p", "name": "n",
                                       "session_id": "s1", "since": 5}]}
    try:
        serve.push_statusline(cfg, doc)
    except Exception:
        pass
    calls = wait_calls(ssh_log, 2)
    assert len(calls) == 2
    aliases = set()
    for args, stdin in calls:
        assert json.loads(stdin) == doc
        joined = " ".join(args)
        assert "statusline.state" in joined
        assert ".cache/proj/live" in joined
        assert "mv" in joined
        aliases |= {a for a in ("s-host", "em-host") if a in args}
    assert aliases == {"s-host", "em-host"}


def test_push_statusline_with_no_remote_hosts_does_nothing(ssh_log):
    cfg = make_cfg()
    cfg["hosts"] = [{"name": "loc", "local": True}]
    serve.push_statusline(cfg, {"updated": T0, "waiting": []})
    time.sleep(0.3)
    assert ssh_calls(ssh_log) == []


# ---------- ordered pushes: one in flight per host, newest doc wins ----------

@pytest.fixture
def slow_ssh(tmp_path, monkeypatch):
    """Fake ssh that records every start, blocks while block.<alias> exists, then lands its
    stdin as final.<alias> (last landing wins, like the remote file)."""
    bindir = tmp_path / "slowbin"
    bindir.mkdir()
    log = tmp_path / "slowlog"
    log.mkdir()
    script = bindir / "ssh"
    script.write_text(
        "#!/bin/sh\n"
        "L=" + str(log) + "\n"
        "for a; do prev=$last; last=$a; done\n"
        "alias=$prev\n"
        "cat > $L/stdin.$$\n"
        "echo $alias >> $L/starts\n"
        "while [ -e $L/block.$alias ]; do sleep 0.05; done\n"
        "cp $L/stdin.$$ $L/final.$alias.$$ && mv $L/final.$alias.$$ $L/final.$alias\n"
        "exit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    return log


def starts(log, alias):
    try:
        with open(str(log / "starts")) as f:
            return [l for l in f.read().split() if l == alias]
    except IOError:
        return []


def final(log, alias):
    try:
        with open(str(log / ("final." + alias))) as f:
            return json.load(f)
    except (IOError, ValueError):
        return None


def wait_for(pred, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def pdoc(sid):
    return {"updated": T0, "waiting": [{"host": "x", "project": "p", "name": "n",
                                        "session_id": sid, "since": 1}]}


def push_returns(pusher, doc):
    t = threading.Thread(target=pusher.push, args=(doc,))
    t.start()
    t.join(10)
    return not t.is_alive()


def one_host_cfg(*aliases):
    cfg = make_cfg()
    cfg["hosts"] = [{"name": "loc", "local": True}] + [{"name": a, "ssh": a} for a in aliases]
    return cfg


def test_three_docs_during_inflight_push_collapse_to_first_and_last(slow_ssh):
    (slow_ssh / "block.s-host").write_text("")
    pusher = serve.StatuslinePusher(one_host_cfg("s-host"))
    assert push_returns(pusher, pdoc("d1"))
    assert wait_for(lambda: len(starts(slow_ssh, "s-host")) == 1)
    assert push_returns(pusher, pdoc("d2"))
    assert push_returns(pusher, pdoc("d3"))
    time.sleep(0.3)
    assert len(starts(slow_ssh, "s-host")) == 1       # nothing overlaps the in-flight push
    os.remove(str(slow_ssh / "block.s-host"))
    assert wait_for(lambda: final(slow_ssh, "s-host") == pdoc("d3"))
    time.sleep(0.5)
    assert len(starts(slow_ssh, "s-host")) == 2       # first and last only
    assert final(slow_ssh, "s-host") == pdoc("d3")


def test_single_doc_is_pushed_once(slow_ssh):
    pusher = serve.StatuslinePusher(one_host_cfg("s-host"))
    assert push_returns(pusher, pdoc("only"))
    assert wait_for(lambda: final(slow_ssh, "s-host") == pdoc("only"))
    time.sleep(0.4)
    assert len(starts(slow_ssh, "s-host")) == 1


def test_sequential_pushes_each_land(slow_ssh):
    pusher = serve.StatuslinePusher(one_host_cfg("s-host"))
    for sid in ("a", "b"):
        assert push_returns(pusher, pdoc(sid))
        assert wait_for(lambda: final(slow_ssh, "s-host") == pdoc(sid))
    time.sleep(0.3)
    assert len(starts(slow_ssh, "s-host")) == 2


def test_blocked_host_does_not_block_other_host(slow_ssh):
    (slow_ssh / "block.s-host").write_text("")
    pusher = serve.StatuslinePusher(one_host_cfg("s-host", "em-host"))
    assert push_returns(pusher, pdoc("d1"))
    assert wait_for(lambda: final(slow_ssh, "em-host") == pdoc("d1"))
    assert final(slow_ssh, "s-host") is None          # s-host is still stuck
    assert push_returns(pusher, pdoc("d2"))
    assert wait_for(lambda: final(slow_ssh, "em-host") == pdoc("d2"))
    assert final(slow_ssh, "s-host") is None
    os.remove(str(slow_ssh / "block.s-host"))
    assert wait_for(lambda: final(slow_ssh, "s-host") == pdoc("d2"))


def test_refresher_pushes_through_ordered_pusher(cache_dir, fk, slow_ssh):
    seed_collect(0)
    fk.waiting = ["a"]
    r = serve.Refresher(make_cfg(), lambda: T0, fk.sync_live, fk.sync_all)
    tick(r, T0)
    assert wait_for(lambda: final(slow_ssh, "s-host") is not None)
    assert [w["session_id"] for w in final(slow_ssh, "s-host")["waiting"]] == ["a"]


# ---------- run loop survives errors ----------

def test_run_loop_survives_clock_exception(cache_dir, fk, capsys):
    seed_collect(0)
    state = {"armed": False, "n": 0, "raised": 0}

    def clock():
        if not state["armed"]:
            return T0
        state["n"] += 1
        if state["raised"] == 0:
            state["raised"] = 1
            raise RuntimeError("clock broke")
        return T0 + 16 * state["n"]

    r = make(fk, clock=clock)
    state["armed"] = True
    stop = threading.Event()
    t = threading.Thread(target=r.run, args=(stop,))
    t.start()
    try:
        assert wait_for(lambda: len(fk.locals()) >= 2, 25), "refresh did not continue after the error"
        assert t.is_alive()
        assert state["raised"] == 1
    finally:
        stop.set()
        t.join(20)
    assert not t.is_alive()
    assert "clock broke" in capsys.readouterr().err


def test_run_loop_survives_sync_exception(cache_dir, fk):
    seed_collect(0)
    n = {"c": 0}
    orig = fk.sync_live

    def flaky(host, cfg, now=None, memo=None):
        if host["name"] == "loc":
            n["c"] += 1
            if n["c"] == 1:
                raise RuntimeError("sync broke")
        return orig(host, cfg, now, memo)
    ticks = {"n": 0}

    def clock():
        ticks["n"] += 1
        return T0 + 16 * ticks["n"]
    r = serve.Refresher(make_cfg(), clock, flaky, fk.sync_all, fk.push)
    stop = threading.Event()
    t = threading.Thread(target=r.run, args=(stop,))
    t.start()
    try:
        assert wait_for(lambda: n["c"] >= 2, 25)
        assert t.is_alive()
    finally:
        stop.set()
        t.join(20)
    assert not t.is_alive()


# ---------- SIGTERM ----------

CHILD = """
import json, signal, sys
from projdeck import serve
cfg = json.loads(sys.argv[1])
before = signal.getsignal(signal.SIGTERM)
rc = serve.serve(cfg, "127.0.0.1", int(sys.argv[2]))
print("RC", rc, "RESTORED", signal.getsignal(signal.SIGTERM) == before, flush=True)
sys.exit(rc if isinstance(rc, int) else 0)
"""


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def listening(port):
    import socket
    try:
        socket.create_connection(("127.0.0.1", port), 0.5).close()
        return True
    except OSError:
        return False


def sigterm_run(tmp_path, wait_for_response):
    import signal
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = make_cfg(push=False)
    cfg["hosts"] = [{"name": "loc", "local": True}]
    port = free_port()
    env = dict(os.environ, HOME=str(tmp_path / "h"), PROJ_CACHE=str(tmp_path / "pc"),
               PYTHONPATH=root)
    os.makedirs(env["HOME"])
    p = subprocess.Popen([sys.executable, "-c", CHILD, json.dumps(cfg), str(port)], cwd=root,
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         universal_newlines=True)
    try:
        assert wait_for(lambda: listening(port) or p.poll() is not None, 30)
        assert p.poll() is None, p.communicate()
        if wait_for_response:
            assert wait_for(lambda: answers(port), 30)
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=30)
    finally:
        if p.poll() is None:
            p.kill()
            p.communicate()
    assert p.returncode == 0, (p.returncode, err[-500:])      # not killed by the signal
    assert "RC 0" in out and "RESTORED True" in out
    assert not listening(port)                                 # shutdown ran, socket closed


def answers(port):
    import http.client
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        c.request("GET", "/api/state.json")
        ok = c.getresponse().status == 200
        c.close()
        return ok
    except (OSError, http.client.HTTPException):
        return False


def test_sigterm_ends_serve_through_normal_exit(tmp_path):
    """SIGTERM once the server is answering requests."""
    sigterm_run(tmp_path, True)


def test_sigterm_right_after_bind_still_exits(tmp_path):
    """SIGTERM as soon as the port is open (systemd may stop a service that just started)."""
    sigterm_run(tmp_path, False)
