"""Spec-derived tests for agent.segments / agent.effective_status (T04).

tx dicts are built directly in the shape of transcript_events' return value
({"state": {...}, "events": [(ts, kind)], "asks": [(start, end)]}); a few
tests go through a real jsonl + transcript_events as well.
"""
import json
import time

from projdeck import agent

T0 = 1800000000
NOW = T0 + 100000
SINCE = T0 - 50


def tx(events, asks=(), pending=None, ask_started=None, **state):
    st = {"pending_tool": pending, "ask_started": ask_started}
    st.update(state)
    return {"state": st, "events": [tuple(e) for e in events], "asks": [tuple(a) for a in asks]}


def seg(t, now=NOW, since=SINCE, live=False, status="idle", sub=0):
    out = agent.segments(t, now, since, live, status, sub)
    out = [list(s) for s in out]
    # invariants shared by every case
    prev_end = None
    for k, s, e in out:
        assert k in ("busy", "sub", "wait", "wait_now")
        assert isinstance(s, int) and isinstance(e, int)
        assert since <= s <= e <= now
        if prev_end is not None:
            assert s >= prev_end, "segments overlap or are unordered"
        prev_end = e
    return out


# ---- 1. gaps after a turn -------------------------------------------------

def test_h_gap_200_is_wait():
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 260, "H"), (T0 + 300, "C")])
    assert seg(t) == [["busy", T0, T0 + 60], ["wait", T0 + 60, T0 + 260],
                      ["busy", T0 + 260, T0 + 300]]


def test_h_gap_100_is_blank():
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 160, "H"), (T0 + 200, "C")])
    assert seg(t) == [["busy", T0, T0 + 60], ["busy", T0 + 160, T0 + 200]]


def test_h_gap_boundary_180_blank_181_wait():
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 240, "H"), (T0 + 250, "C")])
    assert seg(t) == [["busy", T0, T0 + 60], ["busy", T0 + 240, T0 + 250]]
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 241, "H"), (T0 + 250, "C")])
    assert seg(t) == [["busy", T0, T0 + 60], ["wait", T0 + 60, T0 + 241],
                      ["busy", T0 + 241, T0 + 250]]


def test_p_next_is_sub_any_length():
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 560, "P"), (T0 + 600, "C")])
    assert seg(t) == [["busy", T0, T0 + 60], ["sub", T0 + 60, T0 + 560],
                      ["busy", T0 + 560, T0 + 600]]
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 90, "P"), (T0 + 150, "C")])
    assert seg(t) == [["busy", T0, T0 + 60], ["sub", T0 + 60, T0 + 90],
                      ["busy", T0 + 90, T0 + 150]]


def test_m_next_is_blank_and_later_turn_is_busy():
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 60 + 10800, "M"), (T0 + 60 + 10800 + 40, "C")])
    assert seg(t) == [["busy", T0, T0 + 60], ["busy", T0 + 10860, T0 + 10900]]


# ---- 2. waits inside a turn ------------------------------------------------

def test_ask_inside_turn_cut_out_as_wait():
    t = tx([(T0, "H"), (T0 + 10, "C"), (T0 + 2010, "C"), (T0 + 2100, "C")],
           asks=[(T0 + 10, T0 + 2010)])
    assert seg(t) == [["busy", T0, T0 + 10], ["wait", T0 + 10, T0 + 2010],
                      ["busy", T0 + 2010, T0 + 2100]]


def test_short_ask_stays_busy():
    t = tx([(T0, "H"), (T0 + 10, "C"), (T0 + 190, "C"), (T0 + 200, "C")],
           asks=[(T0 + 10, T0 + 190)])  # exactly 180 s: not "over" 180
    assert seg(t) == [["busy", T0, T0 + 200]]


def test_non_ask_tools_stay_busy_even_when_long():
    for dur in (400, 600):
        t = tx([(T0, "H"), (T0 + 10, "C"), (T0 + 10 + dur, "C")])
        assert seg(t) == [["busy", T0, T0 + 10 + dur]]


def _jsonl(path, rows):
    with open(str(path), "w") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".000Z"


def _human(ts, text="go"):
    return {"type": "user", "timestamp": _iso(ts), "cwd": "/w/p", "entrypoint": "cli",
            "origin": {"kind": "human"}, "message": {"role": "user", "content": text}}


def _use(ts, tid, name, inp):
    return {"type": "assistant", "timestamp": _iso(ts), "cwd": "/w/p",
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _res(ts, tid, extra=None):
    d = {"type": "user", "timestamp": _iso(ts), "cwd": "/w/p",
         "message": {"role": "user",
                     "content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}
    d.update(extra or {})
    return d


def _text(ts, s="done"):
    return {"type": "assistant", "timestamp": _iso(ts), "cwd": "/w/p",
            "message": {"role": "assistant", "content": [{"type": "text", "text": s}]}}


def test_end_to_end_bash_permission_sources_stay_busy(tmp_path):
    for src, dur in (("user_temporary", 400), ("config", 600)):
        fp = tmp_path / (src + ".jsonl")
        _jsonl(fp, [_human(T0), _use(T0 + 10, "b1", "Bash", {"command": "ls"}),
                    _res(T0 + 10 + dur, "b1", {"permissionDecision": {"source": src}}),
                    _text(T0 + 20 + dur)])
        t = agent.transcript_events(str(fp), SINCE)
        assert seg(t) == [["busy", T0, T0 + 20 + dur]]


def test_end_to_end_ask_2000s_is_wait(tmp_path):
    fp = tmp_path / "a.jsonl"
    _jsonl(fp, [_human(T0), _use(T0 + 10, "q1", "AskUserQuestion",
                                  {"questions": [{"question": "which?"}]}),
                _res(T0 + 2010, "q1"), _text(T0 + 2020)])
    t = agent.transcript_events(str(fp), SINCE)
    assert seg(t) == [["busy", T0, T0 + 10], ["wait", T0 + 10, T0 + 2010],
                      ["busy", T0 + 2010, T0 + 2020]]


# ---- 3. open gap -----------------------------------------------------------

OPEN_EV = [(T0, "H"), (T0 + 60, "C")]


def test_open_pending_ask_busy_status_is_wait_now():
    t = tx(OPEN_EV, pending="AskUserQuestion", ask_started=T0 + 60)
    n = T0 + 1000
    assert seg(t, now=n, live=True, status="busy") == [["busy", T0, T0 + 60],
                                                       ["wait_now", T0 + 60, n]]


def test_open_pending_ask_beats_status_and_subagents():
    t = tx(OPEN_EV, pending="AskUserQuestion", ask_started=T0 + 60)
    n = T0 + 1000
    for status, sub in (("idle", 0), ("idle", 3), ("busy", 2)):
        assert seg(t, now=n, live=True, status=status, sub=sub) == [
            ["busy", T0, T0 + 60], ["wait_now", T0 + 60, n]]


def test_open_pending_ask_overlap_with_last_turn_not_overlapping():
    # ruling: busy is cut at ask_started, wait_now follows; no overlap
    t = tx(OPEN_EV, pending="AskUserQuestion", ask_started=T0 + 30)
    n = T0 + 1000
    assert seg(t, now=n, live=True, status="busy") == [["busy", T0, T0 + 30],
                                                       ["wait_now", T0 + 30, n]]


def test_open_busy_runs_to_now():
    n = T0 + 1000
    assert seg(tx(OPEN_EV), now=n, live=True, status="busy") == [["busy", T0, n]]


def test_open_subagents_running_is_sub():
    n = T0 + 1000
    assert seg(tx(OPEN_EV), now=n, live=True, status="idle", sub=2) == [
        ["busy", T0, T0 + 60], ["sub", T0 + 60, n]]


def test_open_busy_beats_sub():
    n = T0 + 1000
    assert seg(tx(OPEN_EV), now=n, live=True, status="busy", sub=2) == [["busy", T0, n]]


def test_open_idle_over_180_is_wait_now():
    n = T0 + 60 + 181
    assert seg(tx(OPEN_EV), now=n, live=True, status="idle") == [
        ["busy", T0, T0 + 60], ["wait_now", T0 + 60, n]]


def test_open_idle_up_to_180_is_blank():
    for gap in (100, 180):
        n = T0 + 60 + gap
        assert seg(tx(OPEN_EV), now=n, live=True, status="idle") == [["busy", T0, T0 + 60]]


def test_ended_session_has_no_open_segment():
    n = T0 + 5000
    assert seg(tx(OPEN_EV), now=n, live=False, status="busy") == [["busy", T0, T0 + 60]]
    t = tx(OPEN_EV, pending="AskUserQuestion", ask_started=T0 + 60)
    assert seg(t, now=n, live=False, status="busy") == [["busy", T0, T0 + 60]]
    assert seg(tx(OPEN_EV), now=n, live=False, status="idle", sub=2) == [["busy", T0, T0 + 60]]


def test_background_running_maps_busy_and_gap_600_is_busy():
    st = agent.effective_status({"kind": "background", "state": "running"})
    assert st == "busy"
    n = T0 + 60 + 600
    assert seg(tx(OPEN_EV), now=n, live=True, status=st) == [["busy", T0, n]]


def test_effective_status_cases():
    es = agent.effective_status
    assert es({"kind": "interactive", "status": "busy"}) == "busy"
    assert es({"kind": "interactive", "status": "idle"}) == "idle"
    assert es({"kind": "interactive"}) == "idle"
    assert es({}) == "idle"
    assert es({"kind": "background", "state": "running"}) == "busy"
    assert es({"kind": "background", "state": "working"}) == "busy"
    assert es({"kind": "background", "state": "blocked"}) == "idle"
    assert es({"kind": "background", "state": "needs_input"}) == "idle"
    assert es({"kind": "background"}) == "idle"
    # interactive sessions ignore a background-style state field
    assert es({"kind": "interactive", "status": "idle", "state": "running"}) == "idle"


# ---- 4. truncation ---------------------------------------------------------

def test_old_unanswered_ask_is_exactly_wait_now_since_now():
    since = T0
    started = since - 63 * 3600
    t = tx([], pending="AskUserQuestion", ask_started=started)
    assert seg(t, now=T0 + 5000, since=since, live=True, status="busy") == [
        ["wait_now", since, T0 + 5000]]


def test_turn_crossing_since_starts_at_since():
    since = T0 + 1000
    t = tx([(since - 100, "H"), (since + 500, "C")])
    assert seg(t, since=since, now=since + 9000) == [["busy", since, since + 500]]


def test_wait_crossing_since_is_truncated():
    since = T0 + 1000
    t = tx([(since - 3000, "H"), (since - 2900, "C"), (since + 1000, "H"), (since + 1010, "C")])
    assert seg(t, since=since, now=since + 9000) == [["wait", since, since + 1000],
                                                     ["busy", since + 1000, since + 1010]]


def test_segments_entirely_before_since_dropped():
    since = T0 + 10000
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 400, "H"), (T0 + 430, "C")])
    assert seg(t, since=since, now=since + 100) == []


def test_everything_clipped_to_now_and_since():
    since = T0 + 100
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 500, "H"), (T0 + 900, "C"),
            (T0 + 2000, "H"), (T0 + 2300, "C")])
    out = seg(t, since=since, now=T0 + 2100)  # helper asserts bounds
    assert out and out[-1][2] <= T0 + 2100


# ---- 5. merge and cap ------------------------------------------------------

def test_same_kind_gap_up_to_60_merges():
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 120, "H"), (T0 + 150, "C")])  # gap 60
    assert seg(t) == [["busy", T0, T0 + 150]]
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 110, "H"), (T0 + 150, "C")])  # gap 50
    assert seg(t) == [["busy", T0, T0 + 150]]


def test_same_kind_gap_61_does_not_merge():
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 121, "H"), (T0 + 150, "C")])
    assert seg(t) == [["busy", T0, T0 + 60], ["busy", T0 + 121, T0 + 150]]


def test_different_kinds_never_merge():
    t = tx([(T0, "H"), (T0 + 60, "C"), (T0 + 90, "P"), (T0 + 150, "C")])
    assert [s[0] for s in seg(t)] == ["busy", "sub", "busy"]


def _many(n):
    ev = []
    for i in range(n):
        ev += [(T0 + i * 200, "H"), (T0 + i * 200 + 100, "C")]
    return ev


def test_450_segments_keep_latest_400():
    t = tx(_many(450))
    out = seg(t, since=T0 - 10, now=T0 + 450 * 200 + 1000)
    assert out == [["busy", T0 + i * 200, T0 + i * 200 + 100] for i in range(50, 450)]


def test_400_segments_all_kept_and_401_drops_oldest():
    out = seg(tx(_many(400)), since=T0 - 10, now=T0 + 400 * 200 + 1000)
    assert len(out) == 400 and out[0][1] == T0
    out = seg(tx(_many(401)), since=T0 - 10, now=T0 + 401 * 200 + 1000)
    assert len(out) == 400 and out[0][1] == T0 + 200


# ---- empties ---------------------------------------------------------------

def test_empty_tx_gives_no_segments():
    assert seg(tx([]), live=False) == []
    assert seg(tx([]), live=True, status="idle") == []
