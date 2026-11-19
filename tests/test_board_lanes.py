import time

import pytest

from projdeck import board
from tests.test_board_sessions import cache, cfg, live, sess

H = 3600


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


DAY = 1_800_000_000 // 86400 * 86400
NOW = DAY + 15 * H + 1200  # 15:20


def lanes(sessions, now=NOW, projects=None, caches=()):
    lv = live("a", sessions, at=now - 5)
    lv["projects"] = projects or {}
    return board.build(list(caches), [lv], cfg("a"), now)["lanes"]


def test_window_starts_at_hour_of_first_activity():
    l = lanes([sess("x", "busy", segments=[["busy", DAY + 9 * H + 1800, DAY + 10 * H]])])
    assert (l["start"], l["end"]) == (DAY + 9 * H, NOW)


def test_window_not_before_midnight_and_ignores_yesterday():
    l = lanes([sess("x", "busy", segments=[["busy", DAY - 5 * H, DAY + 2 * H]])])
    assert l["start"] == DAY


def test_window_at_least_three_hours():
    l = lanes([sess("x", "busy", segments=[["busy", NOW - 600, NOW]])])
    assert l["end"] - l["start"] == 3 * H
    assert lanes([])["end"] - lanes([])["start"] == 3 * H


def test_window_three_hours_just_after_midnight():
    now = DAY + 600
    l = lanes([sess("x", "busy", segments=[["busy", DAY + 60, now]])], now=now)
    assert l["end"] - l["start"] == 3 * H


def test_order_matches_columns_and_overflow_counts_more():
    ss = [sess("i", "idle"), sess("b", "busy", started_at=NOW - 100), sess("w", "waiting")]
    ss += [sess("x%d" % n, "busy", started_at=NOW - 9000 - n) for n in range(11)]
    l = lanes(ss)
    assert len(l["rows"]) == 12 and l["more"] == 2
    assert [r["session_id"] for r in l["rows"][:2]] == ["w", "b"]
    assert l["rows"][0]["state"] == "waiting"


def test_ended_today_lane_comes_last():
    ended = sess("e", "ended", live=False, segments=[["busy", DAY + H, DAY + 2 * H]])
    l = lanes([ended, sess("i", "idle")])
    assert [(r["session_id"], r["state"]) for r in l["rows"]] == [("i", "idle"), ("e", "ended")]


def test_commits_only_on_top_lane_of_a_project():
    ss = [sess("w", "waiting", proj="p"), sess("b", "busy", proj="p"), sess("o", "busy", proj="q")]
    l = lanes(ss, projects={"/home/u/p": {"commits": [NOW - 100, NOW + 50, DAY - H]},
                            "/home/u/q": {"commits": [NOW - 200]}})
    by = {r["session_id"]: r["commits"] for r in l["rows"]}
    assert by == {"w": [NOW - 100], "b": [], "o": [NOW - 200]}


def test_commits_from_collect_cache_are_used():
    c = cache("a", [{"path": "/home/u/alpha", "git": {"commit_ts_14d": [NOW - 60]}}])
    l = lanes([sess("x", "busy")], caches=[c])
    assert l["rows"][0]["commits"] == [NOW - 60]


def test_merges_only_on_ledger_owner():
    tasks = {"source": "run", "merges": [["T1", NOW - 500], ["T0", DAY - H]]}
    ss = [sess("owner", "busy", tasks=tasks), sess("other", "busy", tasks=None, tasks_owner="owner")]
    by = {r["session_id"]: r["merges"] for r in lanes(ss)["rows"]}
    assert by == {"owner": [["T1", NOW - 500]], "other": []}


def test_merges_not_drawn_for_a_tasks_copy_owned_by_another_session():
    # F9 (T14 M-2): a non-owner that does carry a tasks dict still draws no merges, so the
    # tasks_owner condition is pinned (before, every non-owner had tasks None)
    tasks = {"source": "run", "merges": [["T1", NOW - 500]]}
    ss = [sess("owner", "busy", tasks=tasks), sess("other", "busy", tasks=dict(tasks), tasks_owner="owner")]
    by = {r["session_id"]: r["merges"] for r in lanes(ss)["rows"]}
    assert by == {"owner": [["T1", NOW - 500]], "other": []}


def test_totals_sum_clipped_segments_and_wait_now_counts_as_wait():
    start = DAY
    segs = [["busy", start - 100, start + 100], ["sub", start + 200, start + 300],
            ["wait", start + 400, start + 500], ["wait_now", NOW - 50, NOW + 10]]
    r = lanes([sess("x", "busy", segments=segs)])["rows"][0]
    assert r["busy_s"] == 100  # sub is drawn but not counted as busy
    assert r["wait_s"] == 100 + 50
    assert r["segments"][0] == ["busy", DAY, DAY + 100]
    assert r["segments"][-1] == ["wait_now", NOW - 50, NOW]
