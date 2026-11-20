import os
import time

import pytest

from projdeck import board

NOW = 1_800_000_000


def sess(sid, state, **kw):
    s = {"id": sid, "project": "/home/u/" + kw.pop("proj", "alpha"), "kind": "interactive",
         "live": True, "name": sid, "title": sid, "started_at": NOW - 5000, "status": "idle",
         "state": state, "idle_since": NOW - 1000, "question": "", "subagents_running": 0,
         "segments": [], "attach": "", "errors": []}
    s.update(kw)
    return s


def live(host, sessions, at=NOW - 10, **kw):
    d = {"schema": 1, "host": host, "collected_at": at, "source": "claude agents",
         "sessions": sessions, "projects": {}}
    d.update(kw)
    return d


def cache(host, projects=(), at=NOW - 100, **kw):
    d = {"schema": 1, "host": host, "collected_at": at, "synced_at": at, "stale_since": None,
         "last_error": None, "projects": list(projects)}
    d.update(kw)
    return d


def cfg(*names):
    return {"hosts": [{"name": n, "local": i == 0, "ssh": None if i == 0 else n}
                      for i, n in enumerate(names)]}


@pytest.fixture
def tz_utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_toplevel_keys_and_empty_lists():
    b = board.build([], [], cfg("a"), NOW)
    assert set(b) == {"hosts", "summary", "waiting", "busy", "idle", "ended_today", "stale",
                      "stale_host_sessions", "lanes", "top_todos", "backlogs", "tiles",
                      "unknown", "built_at"}  # F3, F4
    assert b["lanes"]["rows"] == [] and b["lanes"]["more"] == 0
    assert b["top_todos"] == b["backlogs"] == b["tiles"] == []


def two_hosts():
    a = live("a", [
        sess("w2", "waiting", idle_since=NOW - 100),
        sess("w1", "waiting", idle_since=NOW - 900),
        sess("b-old", "busy", status="busy", started_at=NOW - 9000),
        sess("b-new", "busy", status="busy", started_at=NOW - 3000),
        sess("s-old", "sub", subagents_running=1, started_at=NOW - 8000),
        sess("s-new", "sub", subagents_running=2, started_at=NOW - 2000),
        sess("i-old", "idle", idle_since=NOW - 4000),
        sess("i-new", "idle", idle_since=NOW - 50),
        sess("bg-stale", "stale", kind="background", idle_since=NOW - 200000),
    ])
    r = live("r", [sess("rw", "waiting", idle_since=NOW - 500)], at=NOW - 60)
    return [cache("a"), cache("r")], [a, r], cfg("a", "r")


def test_grouping_and_order():
    caches, lives, c = two_hosts()
    b = board.build(caches, lives, c, NOW)
    assert [s["id"] for s in b["waiting"]] == ["w1", "rw", "w2"]
    assert [s["id"] for s in b["busy"]] == ["s-new", "s-old", "b-new", "b-old"]
    assert [s["id"] for s in b["idle"]] == ["i-new", "i-old"]
    assert [s["id"] for s in b["stale"]] == ["bg-stale"]
    assert "bg-stale" not in [s["id"] for s in b["waiting"]]
    assert {s["host"] for s in b["waiting"]} == {"a", "r"}


def test_summary_counts_exclude_idle_from_busy():
    caches, lives, c = two_hosts()
    s = board.build(caches, lives, c, NOW)["summary"]
    assert s["waiting"] == 3
    assert s["busy"] == 4          # sub + busy only
    assert s["sub"] == 2
    assert s["idle"] == 2


def test_summary_today_seconds_clipped_to_midnight(tz_utc):
    mid = board.today_start(NOW)
    now = mid + 30
    segs = [["busy", mid - 100, mid + 20], ["sub", mid - 50, mid - 10],
            ["wait", mid + 5, mid + 10], ["wait_now", mid + 10, now]]
    lv = live("a", [sess("x", "busy", segments=segs)], at=now)
    s = board.build([cache("a")], [lv], cfg("a"), now)["summary"]
    assert s["today"] == {"busy": 20, "sub": 0, "wait": 25}


def test_remote_stale_host_goes_to_stale_host_sessions():
    r = live("r", [sess("rw", "waiting"), sess("rb", "busy", status="busy")], at=NOW - 700)
    loc = live("a", [sess("lw", "waiting")], at=NOW - 10)
    b = board.build([cache("a"), cache("r")], [loc, r], cfg("a", "r"), NOW)
    assert [s["id"] for s in b["waiting"]] == ["lw"]
    assert b["busy"] == [] and b["idle"] == []
    got = {s["id"]: s for s in b["stale_host_sessions"]}
    assert set(got) == {"rw", "rb"}
    assert got["rw"]["age"] == 700 and got["rw"]["host"] == "r"
    doc = board.statusline_doc(b, NOW)
    assert [w["session_id"] for w in doc["waiting"]] == ["lw"]


def test_local_host_goes_stale_too():
    loc = live("a", [sess("lw", "waiting")], at=NOW - 5000)
    b = board.build([cache("a")], [loc], cfg("a"), NOW)
    assert b["waiting"] == []
    assert [(s["id"], s["age"]) for s in b["stale_host_sessions"]] == [("lw", 5000)]


def test_statusline_doc_shape_and_order():
    caches, lives, c = two_hosts()
    b = board.build(caches, lives, c, NOW)
    doc = board.statusline_doc(b, NOW)
    # F4: updated is when local live last succeeded (host a, collected NOW - 10), not now
    assert doc["updated"] == NOW - 10 and isinstance(doc["updated"], int)
    assert [w["session_id"] for w in doc["waiting"]] == [s["id"] for s in b["waiting"]]
    for w in doc["waiting"]:
        assert set(w) == {"host", "project", "name", "session_id", "since"}
    assert doc["waiting"][0] == {"host": "a", "project": "alpha", "name": "w1",
                                 "session_id": "w1", "since": NOW - 900}


def test_hosts_entries():
    caches = [cache("a", at=NOW - 100), cache("r", at=NOW - 300, stale_since=NOW - 200,
                                              last_error="boom")]
    lives = [live("a", [], at=NOW - 10)]
    hosts = board.build(caches, lives, cfg("a", "r"), NOW)["hosts"]
    assert [h["name"] for h in hosts] == ["a", "r"]
    for h in hosts:
        assert set(h) == {"name", "live_at", "collect_at", "stale_since", "error", "source",
                          "local", "errors", "live_unknown", "zellij_checked",
                          "live_synced_at", "freshness"}  # F3
    assert hosts[0]["live_at"] == NOW - 10 and hosts[0]["collect_at"] == NOW - 100
    assert hosts[0]["source"] == "claude agents"
    assert hosts[1]["live_at"] is None and hosts[1]["error"] == "boom"
    assert hosts[1]["stale_since"] == NOW - 200


def test_ended_today_sorted_newest_first(tz_utc):
    now = board.today_start(NOW) + 50000
    mid = board.today_start(now)
    older = sess("e1", "ended", live=False, started_at=mid + 100, segments=[["busy", mid + 100, mid + 2000],
                                                      ["wait", mid + 2000, mid + 2600]])
    newer = sess("e2", "ended", live=False, segments=[["busy", mid + 3000, mid + 9000]])
    yesterday = sess("e0", "ended", live=False, segments=[["busy", mid - 900, mid - 100]])
    lv = live("a", [older, newer, yesterday, sess("w", "waiting")], at=now)
    pr = {"path": "/home/u/alpha", "sessions": [
        {"id": "c1", "title": "from collect", "last_ts": mid + 5000, "cwd": "/home/u/alpha"},
        {"id": "w", "title": "live one", "last_ts": mid + 6000, "cwd": "/home/u/alpha"},
        {"id": "c0", "title": "old", "last_ts": mid - 5, "cwd": "/home/u/alpha"}],
        "git": {"commit_ts_14d": [mid + 10, mid + 20, mid - 10]}}
    b = board.build([cache("a", [pr], at=now)], [lv], cfg("a"), now)
    assert [e["id"] for e in b["ended_today"]] == ["e2", "e1"]
    e1 = b["ended_today"][1]
    assert e1["end"] == mid + 2600 and e1["wait_total"] == 600 and e1["commits_today"] == 2
    assert e1["duration"] == 2500


@pytest.mark.parametrize("lv", [
    live("r", [], at=0, last_error="boom"),               # never-succeeded stub
    live("r", [sess("old", "waiting")], at=NOW - 700),    # stale live
    live("r", [], at=NOW - 300),                          # fresh live, older than collect
    None,                                                 # missing live
])
def test_collect_only_sessions_never_ended_today(lv):
    pr = {"path": "/home/u/alpha", "sessions": [
        {"id": "running", "title": "t", "last_ts": NOW - 30, "cwd": "/home/u/alpha"}]}
    lives = [lv] if lv else []
    b = board.build([cache("a"), cache("r", [pr], at=NOW - 30)], lives, cfg("a", "r"), NOW)
    assert b["ended_today"] == []
