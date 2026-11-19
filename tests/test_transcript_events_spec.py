"""Spec-derived tests for agent.transcript_events (T03)."""
import json
import time

from projdeck import agent

T0 = 1800000000  # arbitrary explicit epoch base
H = 3600
CWD = "/w/p"


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".000Z"


def urow(ts, content, cwd=CWD, **extra):
    d = {"type": "user", "timestamp": iso(ts), "cwd": cwd, "entrypoint": "cli",
         "message": {"role": "user", "content": content}}
    d.update(extra)
    return d


def human(ts, text, cwd=CWD):
    return urow(ts, text, cwd=cwd, origin={"kind": "human"})


def peer(ts, text="Another Claude session sent a message: hi"):
    return urow(ts, text, origin={"kind": "peer"})


def meta(ts, text="Stop hook feedback: go on"):
    return urow(ts, text, isMeta=True)


def asst(ts, blocks, cwd=CWD):
    if isinstance(blocks, str):
        blocks = [{"type": "text", "text": blocks}]
    return {"type": "assistant", "timestamp": iso(ts), "cwd": cwd,
            "message": {"role": "assistant", "content": blocks}}


def tuse(ts, tid, name, inp=None):
    return asst(ts, [{"type": "tool_use", "id": tid, "name": name, "input": inp or {}}])


def tres(ts, tid, text="ok", **extra):
    return urow(ts, [{"type": "tool_result", "tool_use_id": tid, "content": text}], **extra)


def turn(ts):
    return {"type": "system", "subtype": "turn_duration", "timestamp": iso(ts),
            "durationMs": 1000}


ASK_INPUT = {"questions": [{"question": "Which one?", "options": []}]}


def run(make_session, tmp_path, records, since):
    fp = make_session(tmp_path, CWD, "s1", records)
    return agent.transcript_events(fp, since)


def kinds(res):
    return [(ts, k) for ts, k in res["events"]]


# ---- AC1 -------------------------------------------------------------

def test_state_fields_from_fixture(make_session, tmp_path):
    long_reply = "r" * 500
    recs = [
        human(T0 + 10, "first", cwd="/a"),
        asst(T0 + 20, "early reply", cwd="/a"),
        human(T0 + 30, "second question", cwd="/b"),
        asst(T0 + 40, long_reply, cwd="/b"),
        peer(T0 + 50),
        asst(T0 + 60, [{"type": "text", "text": "final words"},
                       {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}], cwd="/c"),
        tres(T0 + 70, "t1", cwd="/c"),
    ]
    res = run(make_session, tmp_path, recs, T0)
    st = res["state"]
    assert st["latest_cwd"] == "/c"
    assert st["entry"] == "cli"
    assert st["last_human"]["ts"] == T0 + 30
    assert st["last_human"]["text"] == "second question"
    assert st["last_reply"] == "final words"
    assert st["last_input_kind"] in ("P", "peer")
    assert st["last_input_ts"] == T0 + 50
    assert st["last_c_ts"] == T0 + 70
    assert st["pending_tool"] is None


def test_last_reply_truncated_to_300(make_session, tmp_path):
    recs = [human(T0 + 1, "q"), asst(T0 + 2, "r" * 900)]
    st = run(make_session, tmp_path, recs, T0)["state"]
    assert 0 < len(st["last_reply"]) <= 300
    assert set(st["last_reply"]) == {"r"}


def test_last_human_text_truncated_to_300(make_session, tmp_path):
    st = run(make_session, tmp_path, [human(T0 + 1, "q" * 900)], T0)["state"]
    assert 0 < len(st["last_human"]["text"]) <= 300


def test_last_human_ignores_peer_and_meta(make_session, tmp_path):
    recs = [human(T0 + 1, "mine"), peer(T0 + 2), meta(T0 + 3)]
    st = run(make_session, tmp_path, recs, T0)["state"]
    assert st["last_human"]["text"] == "mine"
    assert st["last_human"]["ts"] == T0 + 1
    assert st["last_input_kind"] in ("M", "meta")
    assert st["last_input_ts"] == T0 + 3


def test_last_input_kind_human(make_session, tmp_path):
    recs = [peer(T0 + 1), human(T0 + 2, "hi")]
    st = run(make_session, tmp_path, recs, T0)["state"]
    assert st["last_input_kind"] in ("H", "human")
    assert st["last_input_ts"] == T0 + 2


def test_last_c_ts_includes_turn_duration(make_session, tmp_path):
    recs = [human(T0 + 1, "q"), asst(T0 + 2, "a"), turn(T0 + 9), human(T0 + 20, "q2")]
    st = run(make_session, tmp_path, recs, T0)["state"]
    assert st["last_c_ts"] == T0 + 9


def test_state_not_limited_by_since(make_session, tmp_path):
    recs = [human(T0 + 1, "old q", cwd="/old"), asst(T0 + 2, "old a", cwd="/old")]
    res = run(make_session, tmp_path, recs, T0 + 10 * 86400)
    assert res["state"]["latest_cwd"] == "/old"
    assert res["state"]["last_human"]["text"] == "old q"
    assert res["state"]["last_reply"] == "old a"
    assert res["events"] == []


# ---- AC2 -------------------------------------------------------------

def test_old_pending_ask(make_session, tmp_path):
    since = T0 + 100 * H
    ask_ts = since - 63 * H
    recs = [human(ask_ts - 5, "go"), tuse(ask_ts, "q1", "AskUserQuestion", ASK_INPUT)]
    res = run(make_session, tmp_path, recs, since)
    st = res["state"]
    assert st["pending_tool"] == "AskUserQuestion"
    assert st["ask_started"] == ask_ts
    assert st["ask_question"] == "Which one?"
    assert res["asks"] == []


def test_ask_question_fallback_one_line_300(make_session, tmp_path):
    inp = {"prompt": "line1\nline2\n" + "z" * 600}
    recs = [human(T0 + 1, "go"), tuse(T0 + 2, "q1", "AskUserQuestion", inp)]
    st = run(make_session, tmp_path, recs, T0)["state"]
    q = st["ask_question"]
    assert "\n" not in q
    assert 0 < len(q) <= 300
    assert "prompt" in q


def test_ask_question_fallback_empty_questions(make_session, tmp_path):
    recs = [human(T0 + 1, "go"),
            tuse(T0 + 2, "q1", "AskUserQuestion", {"questions": [], "note": "NOTEX"})]
    st = run(make_session, tmp_path, recs, T0)["state"]
    assert "NOTEX" in st["ask_question"]


def test_no_ask_fields_when_not_pending(make_session, tmp_path):
    recs = [human(T0 + 1, "go"), asst(T0 + 2, "done")]
    st = run(make_session, tmp_path, recs, T0)["state"]
    assert st["pending_tool"] is None
    assert not st.get("ask_question")
    assert not st.get("ask_started")


# ---- AC3 -------------------------------------------------------------

def test_orphan_ask_followed_by_new_turn_not_pending(make_session, tmp_path):
    recs = [human(T0 + 1, "go"), tuse(T0 + 2, "q1", "AskUserQuestion", ASK_INPUT),
            human(T0 + 100, "never mind"), asst(T0 + 110, "ok done"), turn(T0 + 111)]
    st = run(make_session, tmp_path, recs, T0)["state"]
    assert st["pending_tool"] is None


def test_orphan_non_ask_tool_not_pending(make_session, tmp_path):
    recs = [human(T0 + 1, "go"), tuse(T0 + 2, "b1", "Bash"),
            human(T0 + 50, "again"), asst(T0 + 60, "hi")]
    assert run(make_session, tmp_path, recs, T0)["state"]["pending_tool"] is None


def test_parallel_other_result_after_ask_keeps_pending(make_session, tmp_path):
    recs = [human(T0 + 1, "go"),
            asst(T0 + 2, [{"type": "tool_use", "id": "q1", "name": "AskUserQuestion",
                           "input": ASK_INPUT},
                          {"type": "tool_use", "id": "b1", "name": "Bash", "input": {}}]),
            tres(T0 + 5, "b1"),
            asst(T0 + 6, "waiting for you")]
    st = run(make_session, tmp_path, recs, T0)["state"]
    assert st["pending_tool"] == "AskUserQuestion"
    assert st["ask_started"] == T0 + 2


def test_pending_non_ask_tool(make_session, tmp_path):
    recs = [human(T0 + 1, "go"), tuse(T0 + 2, "b1", "Bash")]
    assert run(make_session, tmp_path, recs, T0)["state"]["pending_tool"] == "Bash"


def test_all_paired_no_pending(make_session, tmp_path):
    recs = [human(T0 + 1, "go"), tuse(T0 + 2, "b1", "Bash"), tres(T0 + 3, "b1"),
            tuse(T0 + 4, "q1", "AskUserQuestion", ASK_INPUT), tres(T0 + 9, "q1", "A")]
    assert run(make_session, tmp_path, recs, T0)["state"]["pending_tool"] is None


def test_latest_pending_wins(make_session, tmp_path):
    recs = [human(T0 + 1, "go"), tuse(T0 + 2, "q1", "AskUserQuestion", ASK_INPUT),
            tuse(T0 + 5, "b1", "Bash")]
    assert run(make_session, tmp_path, recs, T0)["state"]["pending_tool"] == "Bash"
    recs = [human(T0 + 1, "go"), tuse(T0 + 2, "b1", "Bash"),
            tuse(T0 + 5, "q1", "AskUserQuestion", ASK_INPUT)]
    assert run(make_session, tmp_path, recs, T0)["state"]["pending_tool"] == "AskUserQuestion"


def test_peer_or_meta_input_also_closes_window(make_session, tmp_path):
    recs = [human(T0 + 1, "go"), tuse(T0 + 2, "b1", "Bash"), peer(T0 + 10)]
    assert run(make_session, tmp_path, recs, T0)["state"]["pending_tool"] is None
    recs = [human(T0 + 1, "go"), tuse(T0 + 2, "b1", "Bash"), meta(T0 + 10)]
    assert run(make_session, tmp_path, recs, T0)["state"]["pending_tool"] is None


def test_tool_result_without_tool_use_ignored(make_session, tmp_path):
    recs = [human(T0 + 1, "go"), tres(T0 + 2, "ghost"), asst(T0 + 3, "x")]
    res = run(make_session, tmp_path, recs, T0)
    assert res["state"]["pending_tool"] is None
    assert res["asks"] == []


# ---- AC4 -------------------------------------------------------------

def test_events_kinds_and_filter(make_session, tmp_path):
    since = T0 + 3 * 86400
    lo = since - 86400
    recs = [
        human(lo - 100, "too old"),
        asst(lo - 50, "too old reply"),
        human(lo + 10, "h"),
        peer(lo + 20),
        meta(lo + 30),
        asst(lo + 40, [{"type": "tool_use", "id": "b1", "name": "Bash", "input": {}}]),
        tres(lo + 50, "b1"),
        turn(lo + 60),
        urow(lo + 70, "<command-name>/model</command-name>"),
    ]
    res = run(make_session, tmp_path, recs, since)
    assert kinds(res) == [(lo + 10, "H"), (lo + 20, "P"), (lo + 30, "M"),
                          (lo + 40, "C"), (lo + 50, "C"), (lo + 60, "C"), (lo + 70, "H")]


def test_events_keep_day_before_since(make_session, tmp_path):
    since = T0 + 2 * 86400
    recs = [human(since - 86400 + 5, "a"), human(since - 86400 - 5, "b")]
    recs = sorted(recs, key=lambda r: r["timestamp"])
    res = run(make_session, tmp_path, recs, since)
    assert kinds(res) == [(since - 86400 + 5, "H")]


def test_malformed_and_timestampless_lines(make_session, tmp_path):
    good = human(T0 + 10, "hello")
    nots = {"type": "user", "cwd": CWD, "message": {"role": "user", "content": "x"},
            "origin": {"kind": "human"}}
    nots_asst = {"type": "assistant", "message": {"role": "assistant",
                                                  "content": [{"type": "text", "text": "q"}]}}
    recs = [good, nots, nots_asst, "not json at all {", asst(T0 + 20, "reply"),
            '{"type":"user","timestamp":"' + iso(T0 + 30) + '","message":{"role":"us']
    res = run(make_session, tmp_path, recs, T0)
    assert kinds(res) == [(T0 + 10, "H"), (T0 + 20, "C")]
    assert res["state"]["last_human"]["text"] == "hello"


def test_empty_file(make_session, tmp_path):
    res = run(make_session, tmp_path, [], T0)
    assert res["events"] == [] and res["asks"] == []
    assert res["state"]["pending_tool"] is None


def test_answered_asks(make_session, tmp_path):
    recs = [human(T0 + 1, "go"),
            tuse(T0 + 10, "q1", "AskUserQuestion", ASK_INPUT), tres(T0 + 70, "q1", "A"),
            tuse(T0 + 80, "b1", "Bash"), tres(T0 + 90, "b1"),
            tuse(T0 + 100, "q2", "AskUserQuestion", ASK_INPUT), tres(T0 + 400, "q2", "B"),
            tuse(T0 + 410, "q3", "AskUserQuestion", ASK_INPUT)]
    res = run(make_session, tmp_path, recs, T0)
    assert [tuple(a) for a in res["asks"]] == [(T0 + 10, T0 + 70), (T0 + 100, T0 + 400)]
    assert res["state"]["pending_tool"] == "AskUserQuestion"
    assert res["state"]["ask_started"] == T0 + 410


def test_asks_before_window_excluded(make_session, tmp_path):
    since = T0 + 5 * 86400
    old = since - 86400 - 1000
    recs = [human(old - 5, "go"), tuse(old, "q1", "AskUserQuestion", ASK_INPUT),
            tres(old + 30, "q1", "A"),
            tuse(since + 10, "q2", "AskUserQuestion", ASK_INPUT), tres(since + 40, "q2", "B")]
    res = run(make_session, tmp_path, recs, since)
    assert [tuple(a) for a in res["asks"]] == [(since + 10, since + 40)]


# ---- AC5 -------------------------------------------------------------

def test_task_uses(make_session, tmp_path):
    obj = {"task": {"id": "7"}}
    recs = [
        human(T0 + 1, "plan it"),
        tuse(T0 + 2, "t1", "TaskCreate", {"subject": "S1", "description": "d"}),
        tres(T0 + 3, "t1", "Task #7 created", toolUseResult=obj),
        tuse(T0 + 4, "t2", "TaskUpdate", {"taskId": "7", "status": "in_progress"}),
        tres(T0 + 5, "t2", "Updated task #7"),
        tuse(T0 + 6, "t3", "TaskList", {}),
        tres(T0 + 7, "t3", "listing"),
        tuse(T0 + 8, "t4", "TodoWrite", {"todos": [{"content": "c", "status": "pending",
                                                    "activeForm": "doing c"}]}),
        tres(T0 + 9, "t4", "Todos modified"),
        tuse(T0 + 10, "t5", "Bash"), tres(T0 + 11, "t5"),
    ]
    uses = run(make_session, tmp_path, recs, T0)["state"]["task_uses"]
    assert [u["name"] for u in uses] == ["TaskCreate", "TaskUpdate", "TodoWrite"]
    assert [u["ts"] for u in uses] == [T0 + 2, T0 + 4, T0 + 8]
    assert uses[0]["input"] == {"subject": "S1", "description": "d"}
    assert uses[1]["input"] == {"taskId": "7", "status": "in_progress"}
    assert uses[2]["input"]["todos"][0]["content"] == "c"
    assert uses[0]["result_text"] == "Task #7 created"
    assert uses[1]["result_text"] == "Updated task #7"
    assert uses[2]["result_text"] == "Todos modified"
    assert uses[0]["result_obj"] == obj
    for u in uses:
        assert set(u) >= {"name", "ts", "input", "result_text", "result_obj"}


def test_task_uses_without_result_and_empty(make_session, tmp_path):
    recs = [human(T0 + 1, "x"), tuse(T0 + 2, "t1", "TaskCreate", {"subject": "S"})]
    uses = run(make_session, tmp_path, recs, T0)["state"]["task_uses"]
    assert len(uses) == 1 and uses[0]["name"] == "TaskCreate"
    assert not uses[0]["result_text"]
    assert not uses[0]["result_obj"]
    st = run(make_session, tmp_path, [human(T0 + 1, "x")], T0)["state"]
    assert st["task_uses"] == []


def test_task_uses_not_limited_by_since(make_session, tmp_path):
    recs = [human(T0 + 1, "x"), tuse(T0 + 2, "t1", "TaskCreate", {"subject": "S"}),
            tres(T0 + 3, "t1", "Task #1 created")]
    uses = run(make_session, tmp_path, recs, T0 + 30 * 86400)["state"]["task_uses"]
    assert len(uses) == 1


# ---- review round 1 rulings ------------------------------------------

def test_last_reply_long_multiparagraph_keeps_trailing_question(make_session, tmp_path):
    question = "Shall I proceed with option B?"
    text = "\n\n".join(["intro " + "x" * 200, "middle " + "y" * 200, question])
    assert len(text) > 300
    recs = [human(T0 + 1, "go"), asst(T0 + 2, text)]
    reply = run(make_session, tmp_path, recs, T0)["state"]["last_reply"]
    assert len(reply) <= 300
    assert question in reply
    assert reply.endswith(question)
    assert reply.rsplit("\n\n", 1)[-1] == question
    assert text.endswith(reply)


def test_last_reply_short_multiline_keeps_newlines(make_session, tmp_path):
    recs = [human(T0 + 1, "go"), asst(T0 + 2, "para one\n\nWhich one?")]
    assert run(make_session, tmp_path, recs, T0)["state"]["last_reply"] == "para one\n\nWhich one?"


def test_same_row_parallel_ask_wins_either_order(make_session, tmp_path):
    ask = {"type": "tool_use", "id": "q1", "name": "AskUserQuestion", "input": ASK_INPUT}
    bash = {"type": "tool_use", "id": "b1", "name": "Bash", "input": {}}
    for blocks in ([ask, bash], [bash, ask]):
        recs = [human(T0 + 1, "go"), asst(T0 + 2, blocks)]
        st = run(make_session, tmp_path, recs, T0)["state"]
        assert st["pending_tool"] == "AskUserQuestion"
        assert st["ask_question"] == "Which one?"
        assert st["ask_started"] == T0 + 2
