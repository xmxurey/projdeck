"""Spec-derived tests for agent.session_task_list (T08): replay of in-session task tools."""
from projdeck import agent

SINCE = 1800000000 - 86400
T0 = SINCE + 1000
PATH = "/x/session.jsonl"
CO = "/x/checkout"


def call(name, inp, ts, text=None, obj=None):
    return {"name": name, "ts": ts, "input": inp, "result_text": text, "result_obj": obj}


def create(ts, subject="s", active=None, text=None, obj=None, **extra):
    inp = {"subject": subject, "description": "d"}
    if active:
        inp["activeForm"] = active
    inp.update(extra)
    return call("TaskCreate", inp, ts, text, obj)


def update(ts, task_id, **kw):
    inp = {"taskId": task_id}
    inp.update(kw)
    return call("TaskUpdate", inp, ts)


def run(uses, since=SINCE):
    return agent.session_task_list(uses, since, PATH, CO)


def statuses(r):
    return [i["status"] for i in r["items"]]


def ids(r):
    return [i["id"] for i in r["items"]]


def three_creates():
    return [
        create(T0, "A", obj={"task": {"id": "1"}}),
        create(T0 + 1, "B", active="Doing B", text="Task #2 created successfully: B"),
        create(T0 + 2, "C"),  # no id anywhere -> sequence number 3
    ]


# ---- criterion 1
def test_three_id_sources_and_updates():
    uses = three_creates() + [
        update(T0 + 3, "1", status="completed"),
        update(T0 + 4, "2", status="in_progress"),
    ]
    r = run(uses)
    assert statuses(r) == ["done", "doing", "todo"]
    assert ids(r) == ["1", "2", "3"]
    assert r["current"] == "2"
    assert r["items"][1]["note"] == "Doing B"
    assert [i["title"] for i in r["items"]] == ["A", "B", "C"]


def test_metadata_fields():
    r = run(three_creates() + [update(T0 + 3, "2", status="in_progress")])
    assert r["source"] == "session"
    assert r["label"] == "会话内任务清单"
    assert r["path"] == PATH
    assert r["checkout"] == CO
    assert isinstance(r["errors"], list)
    assert r["errors"] == []


def test_current_is_first_doing_and_none_without_doing():
    uses = three_creates() + [update(T0 + 3, "3", status="in_progress"),
                              update(T0 + 4, "2", status="in_progress")]
    assert run(uses)["current"] == "2"
    assert run(three_creates())["current"] is None


def test_id_from_text_when_no_obj_and_sequence_fallback_counts_creates():
    uses = [create(T0, "A", text="Task #7 created successfully: A"),
            create(T0 + 1, "B")]
    r = run(uses)
    assert ids(r)[0] == "7"
    assert ids(r)[1] == "2"  # second TaskCreate in session


def test_update_pending_maps_to_todo_and_subject_changes_title():
    uses = three_creates() + [update(T0 + 3, "1", status="in_progress"),
                              update(T0 + 4, "1", status="pending", subject="A2")]
    r = run(uses)
    assert r["items"][0]["status"] == "todo"
    assert r["items"][0]["title"] == "A2"


def test_title_falls_back_to_description_first_line_and_truncates():
    u = call("TaskCreate", {"description": "first line\nsecond"}, T0)
    r = run([u])
    assert r["items"][0]["title"] == "first line"
    r = run([create(T0, "x" * 300)])
    assert len(r["items"][0]["title"]) == 160


def test_non_doing_note_is_not_active_form():
    r = run([create(T0, "A", active="Doing A")])
    assert r["items"][0]["status"] == "todo"
    assert r["items"][0]["note"] != "Doing A"


# ---- criterion 2
def test_deleted_removes_item():
    uses = three_creates() + [update(T0 + 3, "2", status="deleted")]
    r = run(uses)
    assert ids(r) == ["1", "3"]
    assert r["errors"] == []


def test_update_after_delete_is_unknown_id():
    uses = three_creates() + [update(T0 + 3, "2", status="deleted"),
                              update(T0 + 4, "2", status="completed")]
    r = run(uses)
    assert ids(r) == ["1", "3"]
    assert len(r["errors"]) == 1


def test_update_unknown_task_id_ignored_with_error():
    uses = three_creates() + [update(T0 + 3, "99", status="completed")]
    r = run(uses)
    assert statuses(r) == ["todo", "todo", "todo"]
    assert len(r["errors"]) == 1


def test_update_unknown_status_keeps_status_with_error():
    uses = three_creates() + [update(T0 + 3, "1", status="in_progress"),
                              update(T0 + 4, "1", status="bogus")]
    r = run(uses)
    assert r["items"][0]["status"] == "doing"
    assert len(r["errors"]) == 1


def test_update_with_ignored_fields_is_quiet():
    uses = three_creates() + [update(T0 + 3, "1", addBlockedBy=["2"], status="completed")]
    r = run(uses)
    assert r["items"][0]["status"] == "done"
    assert r["errors"] == []


def test_update_numeric_task_id_matches():
    # taskId given as int should still address "1" (tolerant, ids are strings)
    uses = three_creates() + [update(T0 + 3, 1, status="completed")]
    r = run(uses)
    assert r["items"][0]["status"] == "done"


# ---- criterion 3
def todo_write(ts, todos):
    return call("TodoWrite", {"todos": todos}, ts)


def test_two_todowrites_only_second_counts():
    first = todo_write(T0, [{"content": "old1", "status": "pending", "activeForm": "o"},
                            {"content": "old2", "status": "pending", "activeForm": "o"},
                            {"content": "old3", "status": "pending", "activeForm": "o"}])
    second = todo_write(T0 + 5, [
        {"content": "n1", "status": "completed", "activeForm": "a1"},
        {"content": "n2", "status": "in_progress", "activeForm": "Doing n2"},
    ])
    r = run([first, second])
    assert ids(r) == ["1", "2"]
    assert [i["title"] for i in r["items"]] == ["n1", "n2"]
    assert statuses(r) == ["done", "doing"]
    assert r["current"] == "2"
    assert r["items"][1]["note"] == "Doing n2"


def test_todowrite_then_taskcreate_uses_task_replay():
    tw = todo_write(T0, [{"content": "tw", "status": "pending", "activeForm": "x"}])
    uses = [tw, create(T0 + 1, "T-only", obj={"task": {"id": "1"}})]
    r = run(uses)
    assert [i["title"] for i in r["items"]] == ["T-only"]


def test_taskcreate_then_todowrite_uses_todowrite():
    tw = todo_write(T0 + 5, [{"content": "tw", "status": "pending", "activeForm": "x"}])
    r = run(three_creates() + [tw])
    assert [i["title"] for i in r["items"]] == ["tw"]
    assert ids(r) == ["1"]


def test_todos_not_a_list_skipped_with_error():
    good = todo_write(T0, [{"content": "keep", "status": "pending", "activeForm": "x"}])
    bad = call("TodoWrite", {"todos": "oops"}, T0 + 5)
    r = run([good, bad])
    assert [i["title"] for i in r["items"]] == ["keep"]
    assert len(r["errors"]) == 1


def test_bad_todowrite_only_with_no_items_returns_empty_items_and_error():
    r = run([call("TodoWrite", {"todos": {"a": 1}}, T0)])
    assert r is not None
    assert r["items"] == []
    assert len(r["errors"]) == 1


def test_non_dict_taskcreate_input_ruling_returns_items_empty_with_errors():
    r = run([call("TaskCreate", "not a dict", T0)])
    assert r is not None
    assert r["items"] == []
    assert len(r["errors"]) >= 1
    assert r["source"] == "session"


def test_non_dict_input_skipped_among_good_calls():
    uses = [create(T0, "A", obj={"task": {"id": "1"}}),
            call("TaskUpdate", ["x"], T0 + 1)]
    r = run(uses)
    assert ids(r) == ["1"]
    assert len(r["errors"]) == 1


# ---- criterion 4
def test_all_done_and_older_than_since_returns_none():
    uses = [create(T0, "A", obj={"task": {"id": "1"}}),
            update(SINCE - 10, "1", status="completed")]
    uses[0]["ts"] = SINCE - 20
    assert run(uses) is None


def test_all_done_and_last_call_at_since_is_kept():
    uses = [create(SINCE - 20, "A", obj={"task": {"id": "1"}}),
            update(SINCE, "1", status="completed")]
    r = run(uses)
    assert r is not None and statuses(r) == ["done"]


def test_all_done_recent_is_kept():
    uses = [create(T0, "A", obj={"task": {"id": "1"}}),
            update(T0 + 1, "1", status="completed")]
    assert run(uses) is not None


def test_unfinished_item_kept_no_matter_how_old():
    uses = [create(SINCE - 10 * 86400, "A", obj={"task": {"id": "1"}}),
            create(SINCE - 10 * 86400 + 1, "B", obj={"task": {"id": "2"}}),
            update(SINCE - 10 * 86400 + 2, "1", status="completed")]
    r = run(uses)
    assert r is not None
    assert statuses(r) == ["done", "todo"]


def test_old_todowrite_all_done_is_none_and_old_unfinished_kept():
    done = [{"content": "a", "status": "completed", "activeForm": "a"}]
    todo = done + [{"content": "b", "status": "pending", "activeForm": "b"}]
    assert run([todo_write(SINCE - 5000, done)]) is None
    assert run([todo_write(SINCE - 5000, todo)]) is not None


def test_empty_task_uses_returns_none():
    assert run([]) is None


def test_empty_todowrite_list_returns_none():
    assert run([todo_write(T0, [])]) is None


def test_pure_function_does_not_mutate_input():
    import copy
    uses = three_creates() + [update(T0 + 3, "1", status="completed")]
    snap = copy.deepcopy(uses)
    run(uses)
    assert uses == snap


# ---- review round 1: the two tool sets are replayed independently
def tw_one(ts):
    return todo_write(ts, [{"content": "tw", "status": "pending", "activeForm": "x"}])


def test_todowrite_does_not_clear_task_replay_state():
    uses = [create(T0, "a", obj={"task": {"id": "1"}}),
            create(T0 + 1, "b", obj={"task": {"id": "2"}}),
            tw_one(T0 + 2),
            update(T0 + 3, "1", status="completed")]
    r = run(uses)
    assert [i["title"] for i in r["items"]] == ["a", "b"]
    assert r["items"][0]["status"] == "done"
    assert r["items"][1]["status"] == "todo"
    assert r["errors"] == []


def test_taskcreate_after_todowrite_keeps_earlier_creates():
    uses = [create(T0, "a", obj={"task": {"id": "1"}}),
            create(T0 + 1, "b", obj={"task": {"id": "2"}}),
            tw_one(T0 + 2),
            create(T0 + 3, "c", obj={"task": {"id": "3"}})]
    r = run(uses)
    assert [i["title"] for i in r["items"]] == ["a", "b", "c"]


def test_bad_taskupdate_after_todowrite_does_not_switch_set():
    uses = [tw_one(T0), call("TaskUpdate", "bad", T0 + 1)]
    r = run(uses)
    assert [i["title"] for i in r["items"]] == ["tw"]
    assert len(r["errors"]) == 1


def test_bad_taskcreate_after_todowrite_does_not_switch_set():
    uses = [tw_one(T0), call("TaskCreate", "bad", T0 + 1)]
    r = run(uses)
    assert [i["title"] for i in r["items"]] == ["tw"]
    assert len(r["errors"]) == 1


def test_unknown_taskid_update_after_todowrite_does_not_switch_set():
    uses = [create(T0, "a", obj={"task": {"id": "1"}}),
            tw_one(T0 + 1),
            update(T0 + 2, "99", status="completed")]
    r = run(uses)
    assert [i["title"] for i in r["items"]] == ["tw"]
    assert len(r["errors"]) == 1
