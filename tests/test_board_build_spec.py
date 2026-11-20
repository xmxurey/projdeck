"""Spec-derived tests for board.build / board.statusline_doc (T13)."""
import calendar
import copy
import time

import pytest

from projdeck import board

KEYS = ["hosts", "summary", "waiting", "busy", "idle", "ended_today", "stale",
        "stale_host_sessions", "lanes", "top_todos", "backlogs", "tiles"]
NOW = calendar.timegm((2026, 11, 18, 12, 0, 0))
DAY0 = calendar.timegm((2026, 11, 18, 0, 0, 0))

CFG = {"hosts": [{"name": "loc", "local": True}, {"name": "s-host", "ssh": "s-host"}]}


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def sid(x):
    return x.get("id") or x.get("session_id")


def ids(lst):
    return [sid(x) for x in lst]


def sess(i, state, project="/home/u/proj", kind="interactive", **kw):
    d = {"id": i, "project": project, "cwd": project, "latest_cwd": project, "kind": kind,
         "live": True, "name": "n-" + i, "title": "", "started_at": NOW - 1000,
         "status": "idle", "state": state, "idle_since": NOW - 100, "question": "",
         "last_reply": "", "last_human": {"ts": 0, "text": ""}, "last_input": "human",
         "subagents_running": 0, "segments": [], "attach": "", "tasks": None,
         "tasks_owner": None, "errors": []}
    d.update(kw)
    return d


def live(host, sessions, at, source="claude agents"):
    return {"schema": 1, "host": host, "collected_at": at, "source": source, "since": at - 86400,
            "sessions": sessions, "projects": {}}


def cache(host, at=NOW - 60, projects=None, **kw):
    d = {"schema": 1, "host": host, "collected_at": at, "synced_at": at, "stale_since": None,
         "last_error": None, "projects": projects or []}
    d.update(kw)
    return d


def two_hosts(loc_sessions, sg_sessions, sh_age=30, loc_age=10):
    return [cache("loc"), cache("s-host")], [live("loc", loc_sessions, NOW - loc_age),
                                         live("s-host", sg_sessions, NOW - sh_age)]


# ---------- shape ----------

def test_empty_input_has_all_top_level_keys():
    b = board.build([], [], {"hosts": []}, NOW)
    for k in KEYS:
        assert k in b
    for k in ("waiting", "busy", "idle", "ended_today", "stale", "stale_host_sessions"):
        assert b[k] == []


def test_lanes_todos_backlogs_tiles_carry_the_content_of_the_input():
    items = [{"title": "write docs", "done": False, "section": None, "text": "write docs"},
             {"title": "ship it", "done": True, "section": None, "text": "ship it"}]
    proj = {"path": "/home/u/proj", "exists": True, "git": None, "last_mtime": NOW - 50,
            "sessions": [], "flag": True,
            "backlog": {"path": "docs/backlog.md", "mtime": NOW - 100, "items": items,
                        "open": 1, "done": 1, "raw": None}}
    c = [cache("loc", projects=[proj]), cache("s-host")]
    l = [live("loc", [sess("a", "waiting", question="which db?")], NOW - 10),
         live("s-host", [], NOW - 30)]
    b = board.build(c, l, CFG, NOW)
    assert [(t["host"], t["project"], t["title"], t["flag"]) for t in b["top_todos"]] == [
        ("loc", "/home/u/proj", "write docs", True)]
    assert [(x["host"], x["project"], x["open"], x["done"]) for x in b["backlogs"]] == [
        ("loc", "/home/u/proj", 1, 1)]
    assert [i["title"] for i in b["backlogs"][0]["items"]] == ["write docs", "ship it"]
    assert [(t["host"], t["project"], t["state"], t["todo"]) for t in b["tiles"]] == [
        ("loc", "/home/u/proj", "wait", 1)]
    assert b["tiles"][0]["text"] == "which db?"
    assert [(r["host"], r["session_id"], r["state"]) for r in b["lanes"]["rows"]] == [
        ("loc", "a", "waiting")]
    assert b["lanes"]["end"] == NOW


# ---------- acceptance 1: grouping and order ----------

def make_board():
    loc = [
        sess("w1", "waiting", idle_since=NOW - 500),
        sess("b1", "busy", status="busy", started_at=NOW - 1000),
        sess("b2", "busy", status="busy", started_at=NOW - 50),
        sess("s1", "sub", status="busy", started_at=NOW - 5000, subagents_running=2),
        sess("s2", "sub", status="busy", started_at=NOW - 100, subagents_running=1),
        sess("i1", "idle", idle_since=NOW - 300),
        sess("i2", "idle", idle_since=NOW - 200),
        sess("bg", "stale", kind="background", idle_since=NOW - 200000),
    ]
    sh = [
        sess("w2", "waiting", idle_since=NOW - 900),
        sess("w3", "waiting", idle_since=NOW - 100),
    ]
    c, l = two_hosts(loc, sh)
    return board.build(c, l, CFG, NOW)


def test_waiting_sorted_by_idle_since_oldest_first_across_hosts():
    assert ids(make_board()["waiting"]) == ["w2", "w1", "w3"]


def test_busy_sub_before_busy_each_newest_started_first():
    assert ids(make_board()["busy"]) == ["s2", "s1", "b2", "b1"]


def test_idle_group_newest_idle_since_first_and_not_in_busy():
    b = make_board()
    assert ids(b["idle"]) == ["i2", "i1"]
    assert not set(ids(b["idle"])) & set(ids(b["busy"]))


def test_stale_background_only_in_stale():
    b = make_board()
    assert ids(b["stale"]) == ["bg"]
    for g in ("waiting", "busy", "idle", "stale_host_sessions", "ended_today"):
        assert "bg" not in ids(b[g])


def test_stale_background_never_waiting_even_if_it_looks_blocked():
    loc = [sess("bg", "stale", kind="background", status="idle", question="really?")]
    c, l = two_hosts(loc, [])
    b = board.build(c, l, CFG, NOW)
    assert ids(b["waiting"]) == []
    assert ids(b["stale"]) == ["bg"]


def ended(i, last, **kw):
    return sess(i, "ended", live=False, status="idle",
                segments=[["busy", last - 600, last]], **kw)


def test_ended_today_comes_from_live_ended_sessions_newest_end_first():
    loc = [ended("e1", DAY0 + 3600), ended("e2", DAY0 + 7200), sess("w1", "waiting")]
    c, l = two_hosts(loc, [])
    b = board.build(c, l, CFG, NOW)
    assert ids(b["ended_today"]) == ["e2", "e1"]
    assert "w1" not in ids(b["ended_today"])


# ---------- acceptance 2: summary ----------

def test_summary_counts_busy_without_idle():
    s = make_board()["summary"]
    assert s["waiting"] == 3
    assert s["busy"] == 4      # sub + busy, idle excluded (D6)
    assert s["idle"] == 2


def test_summary_counts_exclude_stale_and_stale_host_sessions():
    loc = [sess("b", "busy", status="busy"), sess("bg", "stale", kind="background")]
    sh = [sess("w", "waiting"), sess("b3", "busy", status="busy"), sess("i", "idle")]
    c, l = two_hosts(loc, sh, sh_age=700)
    s = board.build(c, l, CFG, NOW)["summary"]
    assert (s["waiting"], s["busy"], s["idle"]) == (0, 1, 0)


def test_summary_today_seconds_clipped_at_midnight_and_wait_now_is_wait():
    now = DAY0 + 30
    loc = [sess("a", "busy", status="busy", segments=[
        ["busy", DAY0 - 100, DAY0 + 20],      # 20 s today
        ["sub", DAY0 + 5, DAY0 + 10],         # 5 s
        ["wait", DAY0 - 50, DAY0 - 10],       # 0 s today
        ["wait_now", DAY0 + 21, DAY0 + 30],   # 9 s -> wait
    ])]
    sh = [sess("b", "busy", status="busy", segments=[
        ["wait", DAY0 + 0, DAY0 + 10],        # 10 s
        ["busy", DAY0 + 25, DAY0 + 30],       # 5 s
    ])]
    c = [cache("loc", at=now), cache("s-host", at=now)]
    l = [live("loc", loc, now - 5), live("s-host", sh, now - 5)]
    t = board.build(c, l, CFG, now)["summary"]["today"]
    assert t["busy"] == 25
    assert t["sub"] == 5
    assert t["wait"] == 19


def test_summary_today_not_extrapolated_past_segment_end():
    loc = [sess("a", "busy", status="busy", segments=[["busy", DAY0 + 100, DAY0 + 200]])]
    c, l = two_hosts(loc, [])
    t = board.build(c, l, CFG, NOW)["summary"]["today"]
    assert t["busy"] == 100


# ---------- acceptance 3: stale remote data ----------

def stale_board(sh_age):
    loc = [sess("lw", "waiting", idle_since=NOW - 50), sess("lb", "busy", status="busy")]
    sh = [sess("rw", "waiting", idle_since=NOW - 800), sess("rb", "busy", status="busy"),
          sess("ri", "idle")]
    c, l = two_hosts(loc, sh, sh_age=sh_age, loc_age=10)
    b = board.build(c, l, CFG, NOW)
    return b, board.statusline_doc(b, NOW)


def test_stale_remote_sessions_leave_groups_and_enter_stale_host_sessions():
    b, doc = stale_board(700)
    assert ids(b["waiting"]) == ["lw"]
    assert ids(b["busy"]) == ["lb"]
    assert ids(b["idle"]) == []
    assert "rw" in ids(b["stale_host_sessions"])
    rw = [x for x in b["stale_host_sessions"] if sid(x) == "rw"][0]
    assert rw["age"] == 700
    assert rw.get("host") == "s-host"


def test_stale_remote_not_in_statusline_but_local_is():
    b, doc = stale_board(700)
    assert [w["session_id"] for w in doc["waiting"]] == ["lw"]


def test_remote_exactly_600_is_fresh_601_is_stale():
    b, _ = stale_board(600)
    assert "rw" in ids(b["waiting"])
    assert "rw" not in ids(b["stale_host_sessions"])
    b, _ = stale_board(601)
    assert "rw" not in ids(b["waiting"])
    assert "rw" in ids(b["stale_host_sessions"])


def test_fresh_remote_has_no_stale_host_sessions():
    b, doc = stale_board(30)
    assert b["stale_host_sessions"] == []
    assert {w["session_id"] for w in doc["waiting"]} == {"lw", "rw"}


# ---------- acceptance 4: statusline_doc and hosts ----------

def test_statusline_doc_matches_waiting_order_and_keys():
    b = make_board()
    doc = board.statusline_doc(b, NOW)
    assert doc["updated"] == NOW - 10  # F4: local live collected_at (loc_age 10), not now
    assert [w["session_id"] for w in doc["waiting"]] == ids(b["waiting"])
    for w, src in zip(doc["waiting"], b["waiting"]):
        assert set(w) >= {"host", "project", "name", "session_id", "since"}
        assert w["since"] == src["idle_since"]
        assert w["name"] == src["name"]
    assert [w["host"] for w in doc["waiting"]] == ["s-host", "loc", "s-host"]


def test_statusline_doc_empty_when_nobody_waits():
    b = board.build(*two_hosts([sess("b", "busy", status="busy")], []), CFG, NOW)
    assert board.statusline_doc(b, NOW) == {"updated": NOW - 10, "waiting": []}  # F4


def test_hosts_fields_and_values():
    c = [cache("loc", at=NOW - 20),
         cache("s-host", at=NOW - 3000, stale_since=NOW - 1000, last_error="ssh timeout")]
    l = [live("loc", [], NOW - 10, source="sessions files"), live("s-host", [], NOW - 40)]
    b = board.build(c, l, CFG, NOW)
    by = {h["name"]: h for h in b["hosts"]}
    assert set(by) == {"loc", "s-host"}
    for h in by.values():
        for k in ("name", "live_at", "collect_at", "stale_since", "error", "source"):
            assert k in h
    assert by["loc"]["live_at"] == NOW - 10
    assert by["loc"]["collect_at"] == NOW - 20
    assert by["loc"]["source"] == "sessions files"
    assert by["loc"]["stale_since"] is None and by["loc"]["error"] is None
    assert by["s-host"]["collect_at"] == NOW - 3000
    assert by["s-host"]["stale_since"] == NOW - 1000
    assert "ssh timeout" in by["s-host"]["error"]


def test_host_without_live_cache_still_listed_with_empty_live():
    b = board.build([cache("loc"), cache("s-host")], [live("loc", [], NOW - 5)], CFG, NOW)
    by = {h["name"]: h for h in b["hosts"]}
    assert "s-host" in by
    assert not by["s-host"]["live_at"]


def test_errors_passed_through():
    loc = [sess("w", "waiting", errors=["plan: bad json"])]
    b = board.build(*two_hosts(loc, []), CFG, NOW)
    assert b["waiting"][0]["errors"] == ["plan: bad json"]


def test_build_is_deterministic_and_does_not_mutate_inputs():
    c, l = two_hosts([sess("a", "waiting"), sess("b", "busy", status="busy")], [])
    c0, l0 = copy.deepcopy(c), copy.deepcopy(l)
    b1 = board.build(c, l, CFG, NOW)
    b2 = board.build(c, l, CFG, NOW)
    assert b1 == b2
    assert c == c0 and l == l0


# ---------- review rounds 1-2: ended_today derives from live only ----------

def _collect_proj(last_ts):
    return [{"path": "/home/u/proj", "sessions": [
        {"id": "e1", "cwd": "/home/u/proj", "title": "t1", "last_ts": last_ts}]}]


def _ended_ids(sg_live, last_ts=DAY0 + 3600, cache_at=NOW - 60):
    c = [cache("loc"), cache("s-host", at=cache_at, projects=_collect_proj(last_ts))]
    l = [live("loc", [], NOW - 10)] + ([sg_live] if sg_live is not None else [])
    return ids(board.build(c, l, CFG, NOW)["ended_today"])


def test_collect_session_not_ended_today_even_when_live_fresh():
    assert _ended_ids(live("s-host", [], NOW - 30)) == []


def test_collect_session_newer_than_live_snapshot_not_ended_today():
    # collect is newer than live and the session's last_ts is after the live snapshot
    assert _ended_ids(live("s-host", [], NOW - 300), last_ts=NOW - 100, cache_at=NOW - 10) == []


@pytest.mark.parametrize("sg_live", [None, live("s-host", [], 0), live("s-host", [], NOW - 700)])
def test_collect_session_not_ended_today_when_live_missing_stub_or_stale(sg_live):
    assert _ended_ids(sg_live) == []


def test_live_ended_session_without_errors_key_does_not_crash():
    e = ended("e9", DAY0 + 3600)
    del e["errors"]
    c, l = two_hosts([e], [])
    b = board.build(c, l, CFG, NOW)
    assert "e9" in ids(b["ended_today"])


def test_collect_session_without_errors_key_does_not_crash_build():
    # collect sessions carry no `errors` key (it lives on the project); build must cope
    c = [cache("loc", projects=_collect_proj(DAY0 + 3600)), cache("s-host")]
    l = [live("loc", [sess("w", "waiting")], NOW - 10), live("s-host", [], NOW - 30)]
    b = board.build(c, l, CFG, NOW)
    assert ids(b["waiting"]) == ["w"]


def test_ended_today_has_no_collect_sourced_items():
    c = [cache("loc", projects=_collect_proj(DAY0 + 3600)), cache("s-host")]
    l = [live("loc", [ended("e7", DAY0 + 7200)], NOW - 10), live("s-host", [], NOW - 30)]
    assert ids(board.build(c, l, CFG, NOW)["ended_today"]) == ["e7"]
