import json

from projdeck import agent

T0 = 1_800_000_000  # whole-second base


def iso(ts):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S.123Z", time.gmtime(ts))


def line(d, ts):
    d = dict(d)
    d["timestamp"] = iso(ts)
    return json.dumps(d, separators=(",", ":"))


def human(text, ts, cwd="/p"):
    return line({"type": "user", "cwd": cwd, "entrypoint": "cli",
                 "message": {"role": "user", "content": text}}, ts)


def peer(text, ts):
    return line({"type": "user", "origin": {"kind": "peer"},
                 "message": {"role": "user", "content": text}}, ts)


def meta(text, ts):
    return line({"type": "user", "isMeta": True,
                 "message": {"role": "user", "content": text}}, ts)


def asst(blocks, ts):
    return line({"type": "assistant", "message": {"role": "assistant", "content": blocks}}, ts)


def text(t):
    return {"type": "text", "text": t}


def use(id_, name, inp=None):
    return {"type": "tool_use", "id": id_, "name": name, "input": inp or {}}


def result(id_, ts, content="ok", obj=None):
    d = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": id_, "content": content}]}}
    if obj is not None:
        d["toolUseResult"] = obj
    return line(d, ts)


def write(tmp_path, lines):
    fp = tmp_path / "s.jsonl"
    fp.write_text("\n".join(lines) + "\n")
    return str(fp)


def test_state_fields(tmp_path):
    fp = write(tmp_path, [
        human("first question", T0, cwd="/a"),
        asst([text("one")], T0 + 5),
        human("second question", T0 + 100, cwd="/b"),
        peer("sub result", T0 + 200),
        asst([text("x" * 500)], T0 + 210),
        asst([use("t1", "Bash")], T0 + 220),
        result("t1", T0 + 225),
        line({"type": "custom-title", "customTitle": "My Title"}, T0),
    ])
    tx = agent.transcript_events(fp, T0)
    st = tx["state"]
    assert st["latest_cwd"] == "/b"
    assert st["entry"] == "cli"
    assert st["title"] == "My Title"
    assert st["last_human"] == {"ts": T0 + 100, "text": "second question"}
    assert st["last_reply"] == "x" * 300
    assert st["last_input_kind"] == "P" and st["last_input_ts"] == T0 + 200
    assert st["last_c_ts"] == T0 + 225
    assert st["pending_tool"] is None
    assert st["ask_question"] is None and st["ask_started"] is None


def test_pending_ask_older_than_window(tmp_path):
    started = T0 - 63 * 3600
    q = {"questions": [{"question": "Which one?"}]}
    fp = write(tmp_path, [human("go", started - 10),
                          asst([use("a1", "AskUserQuestion", q)], started)])
    tx = agent.transcript_events(fp, T0)
    st = tx["state"]
    assert st["pending_tool"] == "AskUserQuestion"
    assert st["ask_started"] == started
    assert st["ask_question"] == "Which one?"
    assert tx["events"] == [] and tx["asks"] == []


def test_ask_question_falls_back_to_input(tmp_path):
    fp = write(tmp_path, [human("go", T0),
                          asst([use("a1", "AskUserQuestion", {"q": "y" * 400})], T0 + 1)])
    st = agent.transcript_events(fp, T0)["state"]
    assert st["ask_question"].startswith('{"q":"yyy')
    assert len(st["ask_question"]) == 300


def test_orphan_before_later_input_is_not_pending(tmp_path):
    fp = write(tmp_path, [human("go", T0), asst([use("a1", "AskUserQuestion", {})], T0 + 1),
                          human("next", T0 + 50), asst([text("done")], T0 + 60)])
    st = agent.transcript_events(fp, T0)["state"]
    assert st["pending_tool"] is None


def test_parallel_result_after_ask_keeps_pending(tmp_path):
    fp = write(tmp_path, [human("go", T0),
                          asst([use("a1", "AskUserQuestion", {}), use("b1", "Bash")], T0 + 1),
                          result("b1", T0 + 5)])
    st = agent.transcript_events(fp, T0)["state"]
    assert st["pending_tool"] == "AskUserQuestion"
    assert st["last_c_ts"] == T0 + 5


def test_events_window_and_kinds(tmp_path):
    since = T0
    old = since - 86400 - 10
    fp = write(tmp_path, [
        human("old", old),
        human("h", since - 86400 + 10),
        peer("p", since),
        meta("m", since + 1),
        asst([text("a")], since + 2),
        result("zz", since + 3),
        line({"type": "system", "subtype": "turn_duration"}, since + 4),
        '{"type":"user","message":{"content":"no timestamp"}}',
        '{"type":"assistant","timestamp":"2026-01-01T00:00:0',
    ])
    ev = agent.transcript_events(fp, since)["events"]
    assert ev == [(since - 86400 + 10, "H"), (since, "P"), (since + 1, "M"),
                  (since + 2, "C"), (since + 3, "C"), (since + 4, "C")]


def test_answered_ask_in_asks(tmp_path):
    fp = write(tmp_path, [human("go", T0), asst([use("a1", "AskUserQuestion", {})], T0 + 10),
                          result("a1", T0 + 400), asst([use("b1", "Bash")], T0 + 401),
                          result("b1", T0 + 402)])
    tx = agent.transcript_events(fp, T0)
    assert tx["asks"] == [(T0 + 10, T0 + 400)]
    assert tx["state"]["pending_tool"] is None


def test_task_uses(tmp_path):
    fp = write(tmp_path, [
        human("go", T0),
        asst([use("c1", "TaskCreate", {"subject": "S"})], T0 + 1),
        result("c1", T0 + 2, "Task #1 created", {"task": {"id": "1"}}),
        asst([use("u1", "TaskUpdate", {"taskId": "1", "status": "completed"})], T0 + 3),
        result("u1", T0 + 4, [{"type": "text", "text": "Updated"}]),
        asst([use("l1", "TaskList")], T0 + 5),
        result("l1", T0 + 6),
        asst([use("w1", "TodoWrite", {"todos": []})], T0 + 7),
        result("w1", T0 + 8, "Todos modified"),
    ])
    tu = agent.transcript_events(fp, T0)["state"]["task_uses"]
    assert [(t["name"], t["ts"]) for t in tu] == [
        ("TaskCreate", T0 + 1), ("TaskUpdate", T0 + 3), ("TodoWrite", T0 + 7)]
    assert tu[0]["input"] == {"subject": "S"}
    assert tu[0]["result_text"] == "Task #1 created"
    assert tu[0]["result_obj"] == {"task": {"id": "1"}}
    assert tu[1]["result_text"] == "Updated"
    assert tu[2]["result_text"] == "Todos modified"


def test_last_reply_keeps_paragraphs_and_tail_question(tmp_path):
    body = ("para one. " * 45).strip() + "\n\nShall I also update the README?"
    fp = write(tmp_path, [human("go", T0), asst([text(body)], T0 + 1)])
    r = agent.transcript_events(fp, T0)["state"]["last_reply"]
    assert len(r) == 300
    assert r.rsplit("\n\n", 1)[1] == "Shall I also update the README?"


def test_ask_wins_over_other_call_on_same_line(tmp_path):
    q = {"questions": [{"question": "Which?"}]}
    for blocks in ([use("a1", "AskUserQuestion", q), use("b1", "Bash")],
                   [use("b1", "Bash"), use("a1", "AskUserQuestion", q)]):
        fp = write(tmp_path, [human("go", T0), asst(blocks, T0 + 1)])
        st = agent.transcript_events(fp, T0)["state"]
        assert st["pending_tool"] == "AskUserQuestion"
        assert st["ask_question"] == "Which?"
