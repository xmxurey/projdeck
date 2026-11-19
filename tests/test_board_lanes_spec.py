"""Spec-derived tests for board.build()["lanes"] (T14). Expectations hand-derived from design 3.3 + rulings."""
import calendar
import time

import pytest

from projdeck import board

H = 3600
NOW = calendar.timegm((2026, 11, 18, 12, 0, 0))
DAY0 = calendar.timegm((2026, 11, 18, 0, 0, 0))
CFG = {"hosts": [{"name": "loc", "local": True}, {"name": "s-host", "ssh": "s-host"}]}
PROJ = "/home/u/proj"


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def at(h, m=0, day=18):
    return calendar.timegm((2026, 11, day, h, m, 0))


def sess(i, state, project=PROJ, kind="interactive", **kw):
    d = {"id": i, "project": project, "cwd": project, "latest_cwd": project, "kind": kind,
         "live": True, "name": "n-" + i, "title": "", "started_at": NOW - 1000,
         "status": "idle", "state": state, "idle_since": NOW - 100, "question": "",
         "last_reply": "", "last_human": {"ts": 0, "text": ""}, "last_input": "human",
         "subagents_running": 0, "segments": [], "attach": "", "tasks": None,
         "tasks_owner": None, "errors": []}
    d.update(kw)
    return d


def live(host, sessions, at_, projects=None):
    return {"schema": 1, "host": host, "collected_at": at_, "source": "claude agents",
            "since": at_ - 86400, "sessions": sessions, "projects": projects or {}}


def cache(host, at_=NOW - 60):
    return {"schema": 1, "host": host, "collected_at": at_, "synced_at": at_, "stale_since": None,
            "last_error": None, "projects": []}


def build(loc, sh=(), now=NOW, projects=None, sh_age=30):
    lives = [live("loc", list(loc), now - 10, projects), live("s-host", list(sh), now - sh_age)]
    return board.build([cache("loc"), cache("s-host")], lives, CFG, now)


def lanes(*a, **k):
    return build(*a, **k)["lanes"]


def rid(row):
    return row["session_id"]


def rows_ids(L):
    return [rid(r) for r in L["rows"]]


def stamps(items):
    out = []
    for x in items:
        if isinstance(x, dict):
            out.append(x["ts"])
        elif isinstance(x, (list, tuple)):
            out.append(x[-1])
        else:
            out.append(x)
    return out


def seg_sess(i, state, segs, **kw):
    return sess(i, state, segments=segs, **kw)


# ---------- shape ----------

def test_shape_and_row_keys():
    L = lanes([seg_sess("a", "busy", [["busy", at(9), at(10)]], status="busy")])
    assert set(["start", "end", "rows", "more"]) <= set(L)
    assert L["more"] == 0
    assert len(L["rows"]) == 1
    for k in ("host", "project", "session_id", "state", "segments", "commits", "merges",
              "busy_s", "wait_s"):
        assert k in L["rows"][0]
    r = L["rows"][0]
    assert (r["host"], r["project"], r["session_id"], r["state"]) == ("loc", PROJ, "a", "busy")


def test_remote_host_row_carries_host_name():
    L = lanes([], [seg_sess("r", "busy", [["busy", at(9), at(10)]], status="busy")])
    assert L["rows"][0]["host"] == "s-host"


def test_no_sessions_empty_rows_and_window_defined():
    L = lanes([])
    assert L["rows"] == [] and L["more"] == 0
    assert L["end"] == NOW
    assert L["end"] - L["start"] >= 3 * H


# ---------- acceptance 1: window ----------

def test_start_is_hour_of_earliest_segment():
    L = lanes([seg_sess("a", "busy", [["busy", at(8, 40), at(9)]], status="busy"),
               seg_sess("b", "idle", [["busy", at(10, 5), at(10, 20)]])])
    assert L["start"] == at(8)
    assert L["end"] == NOW


def test_start_hour_floor_with_remote_earliest():
    L = lanes([seg_sess("a", "busy", [["busy", at(10, 5), at(10, 20)]], status="busy")],
              [seg_sess("r", "busy", [["busy", at(7, 59), at(8, 10)]], status="busy")])
    assert L["start"] == at(7)


def test_min_three_hours_when_activity_is_recent():
    L = lanes([seg_sess("a", "busy", [["busy", at(11, 30), at(11, 50)]], status="busy")])
    assert L["end"] == NOW
    assert L["start"] == NOW - 3 * H


def test_min_three_hours_with_minutes_in_now():
    now = at(12, 25)
    L = lanes([seg_sess("a", "busy", [["busy", at(11, 30), at(12, 0)]], status="busy")], now=now)
    assert L["end"] == now
    assert L["start"] <= now - 3 * H
    assert L["start"] >= at(9)  # on or after 09:00 (hour containing now-3h), sanity


def test_start_not_before_midnight_when_activity_started_yesterday():
    L = lanes([seg_sess("a", "busy", [["busy", at(22, day=17), at(3)]], status="busy")])
    assert L["start"] == DAY0


def test_window_at_00_10_is_three_hours():
    now = DAY0 + 600
    L = lanes([seg_sess("a", "busy", [["busy", DAY0 + 120, DAY0 + 500]], status="busy")], now=now)
    assert L["end"] == now
    assert L["end"] - L["start"] == 3 * H


def test_segments_clipped_to_window():
    L = lanes([seg_sess("a", "busy", [["busy", at(22, day=17), at(0, 30)],
                                      ["wait", at(11, 50), at(13)]], status="busy")])
    r = L["rows"][0]
    assert r["segments"], "segments expected"
    for _k, s, e in r["segments"]:
        assert L["start"] <= s < e <= L["end"]
    assert r["busy_s"] == 1800
    assert r["wait_s"] == 600


def test_stale_session_does_not_set_window_start():
    bg = seg_sess("bg", "stale", [["busy", at(2), at(3)]], kind="background")
    a = seg_sess("a", "busy", [["busy", at(10, 30), at(11)]], status="busy")
    L = lanes([bg, a])
    assert L["start"] == NOW - 3 * H


# ---------- acceptance 2: order and cap ----------

def test_order_matches_board_columns_and_stale_not_drawn():
    seg = [["busy", at(9), at(9, 10)]]
    loc = [
        seg_sess("i1", "idle", seg, idle_since=NOW - 300),
        seg_sess("b1", "busy", seg, status="busy", started_at=NOW - 1000),
        seg_sess("s1", "sub", seg, status="busy", started_at=NOW - 100, subagents_running=1),
        seg_sess("w1", "waiting", seg, idle_since=NOW - 500),
        seg_sess("bg", "stale", seg, kind="background", idle_since=NOW - 200000),
        seg_sess("e1", "ended", [["busy", at(9), at(10)]], live=False),
    ]
    sh = [seg_sess("w2", "waiting", seg, idle_since=NOW - 900)]
    b = build(loc, sh)
    got = rows_ids(b["lanes"])
    expect = [x["id"] for k in ("waiting", "busy", "idle", "ended_today") for x in b[k]]
    assert got == expect
    assert got[:2] == ["w2", "w1"]
    assert "bg" not in got
    assert got[-1] == "e1"


def test_stale_host_sessions_not_drawn():
    seg = [["busy", at(9), at(10)]]
    b = build([seg_sess("a", "busy", seg, status="busy")],
              [seg_sess("old", "waiting", seg)], sh_age=700)
    assert "old" not in rows_ids(b["lanes"])
    assert "a" in rows_ids(b["lanes"])


def waiting_many(n):
    return [seg_sess("w%02d" % i, "waiting", [["busy", at(9), at(10)]], idle_since=NOW - 1000 - i)
            for i in range(n)]


def test_cap_12_rows_exactly_twelve_no_more():
    L = lanes(waiting_many(12))
    assert len(L["rows"]) == 12 and L["more"] == 0


def test_cap_13_sessions_gives_more_one():
    L = lanes(waiting_many(13))
    assert len(L["rows"]) == 12 and L["more"] == 1


def test_cap_keeps_the_top_ranked_and_counts_rest():
    b = build(waiting_many(15))
    L = b["lanes"]
    assert L["more"] == 3
    assert rows_ids(L) == [x["id"] for x in b["waiting"]][:12]


# ---------- acceptance 3: commits and merges ----------

def test_commits_only_on_top_lane_of_project():
    seg = [["busy", at(9), at(10)]]
    top = seg_sess("w", "waiting", seg)
    low = seg_sess("b", "busy", seg, status="busy")
    L = lanes([low, top], projects={PROJ: {"last_commit": {"ts": at(10), "subject": "x"},
                                           "commits": [at(10), at(11)]}})
    by = {rid(r): r for r in L["rows"]}
    assert rows_ids(L).index("w") < rows_ids(L).index("b")
    assert sorted(stamps(by["w"]["commits"])) == [at(10), at(11)]
    assert by["b"]["commits"] == []


def test_commits_on_each_project_top_lane_separately():
    seg = [["busy", at(9), at(10)]]
    other = "/home/u/other"
    L = lanes([seg_sess("a", "waiting", seg), seg_sess("b", "busy", seg, status="busy"),
               seg_sess("c", "idle", seg, project=other)],
              projects={PROJ: {"commits": [at(10)]}, other: {"commits": [at(11)]}})
    by = {rid(r): r for r in L["rows"]}
    assert stamps(by["a"]["commits"]) == [at(10)]
    assert by["b"]["commits"] == []
    assert stamps(by["c"]["commits"]) == [at(11)]


def test_commits_limited_to_window():
    seg = [["busy", at(9), at(10)]]
    L = lanes([seg_sess("a", "waiting", seg)],
              projects={PROJ: {"commits": [at(5), at(9, 30), NOW + 500]}})
    assert stamps(L["rows"][0]["commits"]) == [at(9, 30)]


def tasks_with(merges):
    return {"source": "run", "label": "run", "path": "/p/state.json", "checkout": PROJ,
            "current": None, "items": [], "merges": merges}


def test_merges_only_on_owner_lane_even_if_owner_ranks_lower():
    seg = [["busy", at(9), at(10)]]
    non_owner = seg_sess("w", "waiting", seg, tasks=None, tasks_owner="own")
    owner = seg_sess("own", "idle", seg, tasks=tasks_with([["T1", at(9, 30)], ["T2", at(10, 30)]]))
    L = lanes([owner, non_owner])
    by = {rid(r): r for r in L["rows"]}
    assert rows_ids(L).index("w") < rows_ids(L).index("own")
    assert sorted(stamps(by["own"]["merges"])) == [at(9, 30), at(10, 30)]
    assert by["w"]["merges"] == []


def test_merges_limited_to_window():
    seg = [["busy", at(9), at(10)]]
    owner = seg_sess("own", "idle", seg, tasks=tasks_with([["T0", at(2)], ["T1", at(9, 30)],
                                                          ["T9", NOW + 900]]))
    L = lanes([owner])
    assert stamps(L["rows"][0]["merges"]) == [at(9, 30)]


def test_session_without_tasks_has_no_merges():
    L = lanes([seg_sess("a", "idle", [["busy", at(9), at(10)]])])
    assert L["rows"][0]["merges"] == []


# ---------- acceptance 4: totals ----------

def test_busy_and_wait_totals_exclude_sub_and_include_wait_now():
    segs = [["busy", at(9), at(9, 30)], ["sub", at(9, 30), at(9, 40)],
            ["wait", at(9, 40), at(9, 45)], ["wait_now", at(9, 45), at(9, 50)],
            ["busy", at(11), at(11, 10)]]
    L = lanes([seg_sess("a", "busy", segs, status="busy")])
    r = L["rows"][0]
    assert L["start"] == at(9)
    assert r["busy_s"] == 1800 + 600
    assert r["wait_s"] == 300 + 300


def test_totals_are_per_lane():
    L = lanes([seg_sess("a", "busy", [["busy", at(9), at(9, 10)]], status="busy"),
               seg_sess("b", "waiting", [["wait", at(9), at(9, 20)]])])
    by = {rid(r): r for r in L["rows"]}
    assert (by["a"]["busy_s"], by["a"]["wait_s"]) == (600, 0)
    assert (by["b"]["busy_s"], by["b"]["wait_s"]) == (0, 1200)


def test_totals_zero_when_no_segments():
    L = lanes([sess("a", "idle")])
    r = L["rows"][0]
    assert (r["busy_s"], r["wait_s"]) == (0, 0)
