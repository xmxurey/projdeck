"""session_task_list (T08): replay TodoWrite / TaskCreate / TaskUpdate calls."""
from projdeck import agent

NOW = 1800000000
SINCE = NOW - 86400


def use(name, inp, ts=NOW - 100, text=None, obj=None):
    return {"name": name, "ts": ts, "input": inp, "result_text": text, "result_obj": obj}


def create(subject, **kw):
    return use("TaskCreate", {"subject": subject, "description": "d", "activeForm": subject + "ing"}, **kw)


def upd(tid, **kw):
    return use("TaskUpdate", dict(taskId=tid, **kw))


def run(uses, since=SINCE):
    return agent.session_task_list(uses, since, "/t.jsonl", "/co")


def test_three_id_sources_and_updates():
    r = run([create("a", obj={"task": {"id": "1"}}),
             create("b", text="Task #2 created successfully: b"),
             create("c"),
             upd("1", status="completed"), upd("2", status="in_progress")])
    assert [(i["id"], i["status"]) for i in r["items"]] == [("1", "done"), ("2", "doing"), ("3", "todo")]
    assert r["current"] == "2"
    assert r["items"][1]["note"] == "bing"
    assert r["source"] == "session" and r["label"] == "会话内任务清单"
    assert r["path"] == "/t.jsonl" and r["checkout"] == "/co" and r["errors"] == []


def test_delete_unknown_id_and_unknown_status():
    r = run([create("a"), create("b"), upd("1", status="deleted"),
             upd("9", status="completed"), upd("2", status="weird")])
    assert [i["id"] for i in r["items"]] == ["2"]
    assert r["items"][0]["status"] == "todo"
    assert len(r["errors"]) == 2


def test_todowrite_replaces_whole_list():
    tw = lambda *c: use("TodoWrite", {"todos": [{"content": x, "status": s, "activeForm": x + "ing"} for x, s in c]})
    r = run([tw(("a", "pending")), tw(("x", "completed"), ("y", "in_progress"))])
    assert [(i["id"], i["title"], i["status"]) for i in r["items"]] == [("1", "x", "done"), ("2", "y", "doing")]
    assert r["current"] == "2" and r["items"][1]["note"] == "ying"


def test_mixed_tools_last_set_wins():
    tw = use("TodoWrite", {"todos": [{"content": "w", "status": "pending"}]})
    r = run([tw, create("a")])
    assert [i["title"] for i in r["items"]] == ["a"]
    r = run([create("a"), tw])
    assert [i["title"] for i in r["items"]] == ["w"]


def test_bad_todos_skipped_with_error():
    r = run([use("TodoWrite", {"todos": [{"content": "w", "status": "pending"}]}),
             use("TodoWrite", {"todos": "nope"})])
    assert [i["title"] for i in r["items"]] == ["w"]
    assert len(r["errors"]) == 1


def test_freshness():
    old = NOW - 90000
    done = [create("a", ts=old), upd("1", status="completed")]
    done[1]["ts"] = old
    assert run(done) is None
    assert run(done, since=old - 1) is not None
    assert run([create("a", ts=old)]) is not None
    assert run([]) is None


def test_all_calls_skipped_keeps_errors():
    r = run([use("TaskCreate", "bad", ts=1), use("TodoWrite", {"todos": "nope"}, ts=1)])
    assert r is not None and r["items"] == [] and r["current"] is None
    assert r["source"] == "session" and len(r["errors"]) == 2


def test_interleaved_task_and_todowrite_keeps_task_items():
    tw = use("TodoWrite", {"todos": [{"content": "w", "status": "pending"}]})
    r = run([create("a"), create("b"), tw, create("c")])
    assert [i["title"] for i in r["items"]] == ["a", "b", "c"]
    r = run([create("a"), create("b"), tw, upd("1", status="completed")])
    assert r["items"][0]["status"] == "done" and r["errors"] == []
    assert [i["title"] for i in r["items"]] == ["a", "b"]


def test_bad_call_of_other_tool_does_not_hide_valid_list():
    tw = use("TodoWrite", {"todos": [{"content": "w", "status": "pending"}]})
    for bad in (use("TaskUpdate", "bad"), use("TaskCreate", "bad")):
        r = run([tw, bad])
        assert [i["title"] for i in r["items"]] == ["w"]
        assert len(r["errors"]) == 1
