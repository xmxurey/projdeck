from projdeck import agent

NOW = 1_000_000
SINCE = NOW - 86400


def tx(events, asks=(), **state):
    return {"state": state, "events": list(events), "asks": list(asks)}


def seg(t, live=False, status="idle", subs=0, now=NOW, since=SINCE):
    return agent.segments(t, now, since, live, status, subs)


def test_gap_before_human_over_threshold_is_wait():
    t = tx([(NOW - 5000, "H"), (NOW - 4900, "C"), (NOW - 4700, "H"), (NOW - 4600, "C")])
    assert seg(t) == [["busy", NOW - 5000, NOW - 4900], ["wait", NOW - 4900, NOW - 4700],
                      ["busy", NOW - 4700, NOW - 4600]]


def test_short_gap_before_human_not_recorded():
    t = tx([(1000 + SINCE, "H"), (1100 + SINCE, "C"), (1200 + SINCE, "H"), (1300 + SINCE, "C")])
    kinds = [g[0] for g in seg(t)]
    assert "wait" not in kinds


def test_gap_before_peer_is_sub():
    t = tx([(SINCE + 100, "H"), (SINCE + 200, "C"), (SINCE + 5000, "P"), (SINCE + 5100, "C")])
    assert ["sub", SINCE + 200, SINCE + 5000] in seg(t)


def test_gap_before_meta_not_recorded_later_turn_busy():
    a = SINCE + 100
    t = tx([(a, "H"), (a + 10, "C"), (a + 10800, "M"), (a + 10900, "C")])
    assert seg(t) == [["busy", a, a + 10], ["busy", a + 10800, a + 10900]]


def test_ask_user_question_cut_out_of_turn():
    a = SINCE + 100
    t = tx([(a, "H"), (a + 10, "C"), (a + 2010, "C"), (a + 2100, "C")],
           asks=[(a + 10, a + 2010)])
    assert seg(t) == [["busy", a, a + 10], ["wait", a + 10, a + 2010],
                      ["busy", a + 2010, a + 2100]]


def test_bash_approval_stays_busy():
    a = SINCE + 100
    for dur in (400, 600):
        t = tx([(a, "H"), (a + 10, "C"), (a + 10 + dur, "C")])  # no ask record for Bash
        assert seg(t) == [["busy", a, a + 10 + dur]]


def _open(**kw):
    a = NOW - 600
    return tx([(a - 10, "H"), (a, "C")], **kw)


def test_open_gap_pending_ask_busy_is_wait_now():
    t = _open(pending_tool="AskUserQuestion", ask_started=NOW - 500)
    assert seg(t, live=True, status="busy")[-1] == ["wait_now", NOW - 500, NOW]


def test_open_gap_busy():
    assert seg(_open(), live=True, status="busy")[-1] == ["busy", NOW - 610, NOW]


def test_open_gap_subagents_running():
    assert seg(_open(), live=True, subs=2)[-1] == ["sub", NOW - 600, NOW]


def test_open_gap_idle_over_threshold_is_wait_now():
    assert seg(_open(), live=True)[-1] == ["wait_now", NOW - 600, NOW]


def test_open_gap_idle_short_not_recorded():
    t = tx([(NOW - 110, "H"), (NOW - 100, "C")])
    assert seg(t, live=True) == [["busy", NOW - 110, NOW - 100]]


def test_open_gap_ended_session_not_recorded():
    assert seg(_open(), live=False, status="busy") == [["busy", NOW - 610, NOW - 600]]


def test_background_running_maps_to_busy():
    st = agent.effective_status({"kind": "background", "state": "running"})
    assert st == "busy"
    assert seg(_open(), live=True, status=st)[-1] == ["busy", NOW - 610, NOW]


def test_effective_status():
    assert agent.effective_status({"kind": "interactive", "status": "busy"}) == "busy"
    assert agent.effective_status({"kind": "interactive"}) == "idle"
    assert agent.effective_status({"kind": "background", "state": "working"}) == "busy"
    assert agent.effective_status({"kind": "background", "state": "blocked"}) == "idle"


def test_ask_before_since_without_result_is_wait_now_since_to_now():
    t = tx([], pending_tool="AskUserQuestion", ask_started=NOW - 63 * 3600)
    assert seg(t, live=True, status="busy") == [["wait_now", SINCE, NOW]]


def test_turn_crossing_since_starts_at_since():
    t = tx([(SINCE - 100, "H"), (SINCE + 50, "C")])
    assert seg(t) == [["busy", SINCE, SINCE + 50]]


def test_nothing_outside_window():
    t = tx([(SINCE - 5000, "H"), (SINCE - 4000, "C"), (NOW - 100, "H"), (NOW + 500, "C")],
           pending_tool="AskUserQuestion", ask_started=SINCE - 9000)
    for _, s, e in seg(t, live=True, status="busy"):
        assert SINCE <= s < e <= NOW


def test_integer_seconds():
    for _, s, e in seg(_open(), live=True, status="busy"):
        assert isinstance(s, int) and isinstance(e, int)


def test_close_same_kind_merge():
    a = SINCE + 100
    t = tx([(a, "H"), (a + 10, "C"), (a + 50, "H"), (a + 60, "C"), (a + 200, "H"), (a + 210, "C")])
    assert seg(t) == [["busy", a, a + 60], ["busy", a + 200, a + 210]]


def test_cap_keeps_newest_400():
    ev = []
    for i in range(450):
        ev += [(SINCE + 100 + i * 100, "H"), (SINCE + 110 + i * 100, "C")]
    out = seg(tx(ev))
    assert len(out) == 400
    assert out[-1] == ["busy", SINCE + 100 + 449 * 100, SINCE + 110 + 449 * 100]
    assert out[0][1] == SINCE + 100 + 50 * 100
