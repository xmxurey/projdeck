"""Spec-derived tests for agent.find_tasks (T07): ledger / sdd / session / plan sources."""
import hashlib
import json
import os
from datetime import datetime, timezone

import pytest

from projdeck import agent

NOW = 1800000000
SINCE = NOW - 86400
RUN_ID = "2026-11-18-demo"


def put(path, text):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(str(path), "w") as f:
        f.write(text)
    return str(path)


def touch(path, ts):
    os.utime(str(path), (ts, ts))


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def task(tid, status, rnd=0, stage="impl", title=None):
    return {"id": tid, "title": title or ("title " + tid), "status": status,
            "round": rnd, "stage": stage}


def make_ledger(root, tasks, log=None, state_text=None):
    runs = os.path.join(str(root), "docs", "dev", "runs")
    put(os.path.join(runs, ".active"), RUN_ID + "\n")
    put(os.path.join(runs, RUN_ID, "state.json"),
        state_text if state_text is not None else json.dumps(
            {"run_id": RUN_ID, "kind": "dev", "mode": None, "tasks": tasks}))
    if log is not None:
        put(os.path.join(runs, RUN_ID, "log.jsonl"),
            "".join(json.dumps(e) + "\n" for e in log))
    return runs


def make_plan(root, name="p.md", n=3, mtime=NOW - 100):
    body = "# Plan\n\n" + "".join("### Task %d: Title %d\n\nbody\n\n" % (i, i) for i in range(1, n + 1))
    p = put(os.path.join(str(root), "docs", "superpowers", "plans", name), body)
    touch(p, mtime)
    return p


def make_sdd(root, text, mtime=NOW - 100):
    p = put(os.path.join(str(root), ".superpowers", "sdd", "progress.md"), text)
    touch(p, mtime)
    return p


def snapshot(root):
    out = {}
    for d, _, fs in os.walk(str(root)):
        for f in fs:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[p] = (hashlib.sha256(fh.read()).hexdigest(), os.stat(p).st_mtime_ns)
    return out


def by_id(res):
    return {i["id"]: i for i in res["items"]}


SESSION = {"source": "session", "label": "会话内任务清单", "path": "/x.jsonl",
           "checkout": "/x", "current": "1",
           "items": [{"id": "1", "title": "a", "status": "doing", "note": "doing a"}],
           "merges": [], "errors": []}


# ---------- 1. ledger ----------

def test_ledger_source_status_mapping_and_notes(tmp_path):
    make_ledger(tmp_path, [
        task("T01", "done", 0), task("T02", "done", 1),
        task("T03", "in_progress", 0, stage="impl"),
        task("T04", "review", 0, stage="review"),
        task("T05", "pending"), task("T06", "blocked")],
        log=[{"ts": iso(NOW - 500), "event": "decision", "task": "T01", "note": "merged"}])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "run"
    assert [i["id"] for i in r["items"]] == ["T01", "T02", "T03", "T04", "T05", "T06"]
    m = by_id(r)
    assert m["T01"]["status"] == "done" and m["T02"]["status"] == "done"
    assert m["T03"]["status"] == "doing" and m["T04"]["status"] == "doing"
    assert m["T05"]["status"] == "todo"
    assert m["T06"]["status"] == "stuck"
    assert m["T01"]["note"] == "一次通过"
    assert m["T02"]["note"] == "评审 2 轮"
    assert m["T03"]["title"] == "title T03"
    assert r["checkout"] == str(tmp_path)
    assert isinstance(r["errors"], list)


def test_ledger_round_two_is_three_rounds(tmp_path):
    make_ledger(tmp_path, [task("T01", "done", 2)])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert by_id(r)["T01"]["note"] == "评审 3 轮"


def test_ledger_interrupted_is_stuck(tmp_path):
    make_ledger(tmp_path, [task("T01", "interrupted")])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert by_id(r)["T01"]["status"] == "stuck"


def test_ledger_in_progress_note_is_stage(tmp_path):
    make_ledger(tmp_path, [task("T01", "in_progress", 0, stage="tester")])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert by_id(r)["T01"]["note"] == "tester"


def test_ledger_merges_from_decision_event(tmp_path):
    ts = NOW - 777
    make_ledger(tmp_path, [task("T01", "done")],
                log=[{"ts": iso(ts), "event": "decision", "task": "T01", "note": "merged"}])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["merges"] == [["T01", ts]]
    assert type(r["merges"][0][1]) is int


def test_ledger_merges_from_merged_event(tmp_path):
    ts = NOW - 300
    make_ledger(tmp_path, [task("T01", "done"), task("T02", "done")],
                log=[{"ts": iso(ts), "event": "merged", "task": "T02"}])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["merges"] == [["T02", ts]]


def test_ledger_non_merge_events_ignored(tmp_path):
    make_ledger(tmp_path, [task("T01", "done")],
                log=[{"ts": iso(NOW - 9), "event": "phase", "phase": 1},
                     {"ts": iso(NOW - 8), "event": "decision", "task": "T01", "note": "other"}])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "run"
    assert r["merges"] == []


def test_ledger_merges_ts_is_int_and_bad_ts_dropped(tmp_path):
    good = NOW - 1000
    make_ledger(tmp_path, [task("T01", "done"), task("T02", "done"), task("T03", "done")],
                log=[{"ts": iso(good), "event": "merged", "task": "T01"},
                     {"ts": "not-a-time", "event": "merged", "task": "T02"},
                     {"event": "merged", "task": "T03"}])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "run"
    assert r["merges"] == [["T01", good]]
    for _id, ts in r["merges"]:
        assert type(ts) is int


def test_ledger_corrupt_log_lines_skipped(tmp_path):
    runs = make_ledger(tmp_path, [task("T01", "done")])
    ts = NOW - 50
    with open(os.path.join(runs, RUN_ID, "log.jsonl"), "w") as f:
        f.write("{broken\n")
        f.write(json.dumps({"ts": iso(ts), "event": "merged", "task": "T01"}) + "\n")
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "run"
    assert r["merges"] == [["T01", ts]]


def test_ledger_missing_log_gives_empty_merges(tmp_path):
    make_ledger(tmp_path, [task("T01", "pending")])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["merges"] == []


def test_ledger_current_follows_state_current_task(tmp_path):
    # ambiguity: spec does not define ledger `current`; read it as state.json current_task
    make_ledger(tmp_path, [], state_text=json.dumps({
        "run_id": RUN_ID, "current_task": "T02",
        "tasks": [task("T01", "done"), task("T02", "in_progress")]}))
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["current"] == "T02"


def test_ledger_path_points_into_run_dir(tmp_path):
    make_ledger(tmp_path, [task("T01", "pending")])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["path"].startswith(str(tmp_path))
    assert RUN_ID in r["path"]


def test_ledger_without_active_marker_is_not_a_source(tmp_path):
    runs = make_ledger(tmp_path, [task("T01", "pending")])
    os.remove(os.path.join(runs, ".active"))
    assert agent.find_tasks([str(tmp_path)], SINCE) is None


# ---------- 2. worktree / corruption ----------

def test_ledger_in_first_checkout_worktree(tmp_path):
    wt = tmp_path / "wt"
    root = tmp_path / "root"
    root.mkdir()
    make_ledger(wt, [task("W1", "pending")])
    r = agent.find_tasks([str(wt), str(root)], SINCE)
    assert r["source"] == "run"
    assert [i["id"] for i in r["items"]] == ["W1"]
    assert r["checkout"] == str(wt)


def test_ledger_found_in_second_checkout(tmp_path):
    wt = tmp_path / "wt"
    root = tmp_path / "root"
    wt.mkdir()
    make_ledger(root, [task("R1", "pending")])
    r = agent.find_tasks([str(wt), str(root)], SINCE)
    assert r["source"] == "run"
    assert r["checkout"] == str(root)


def test_first_checkout_ledger_wins_over_second(tmp_path):
    wt = tmp_path / "wt"
    root = tmp_path / "root"
    make_ledger(wt, [task("W1", "pending")])
    make_ledger(root, [task("R1", "pending")])
    r = agent.find_tasks([str(wt), str(root)], SINCE)
    assert [i["id"] for i in r["items"]] == ["W1"]


def test_corrupt_state_json_skips_and_records_error(tmp_path):
    make_ledger(tmp_path, [], state_text="{not json")
    make_plan(tmp_path)
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "plan"
    assert r["errors"], "corrupt state.json must leave an errors entry"


def test_corrupt_state_json_falls_through_to_sdd(tmp_path):
    make_ledger(tmp_path, [], state_text="[[[")
    make_sdd(tmp_path, "- [x] a\n- [ ] b\n")
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "sdd"
    assert len(r["errors"]) >= 1


def test_corrupt_state_json_alone_returns_none_and_reports_error(tmp_path):
    make_ledger(tmp_path, [], state_text="{not json")
    errs = []
    assert agent.find_tasks([str(tmp_path)], SINCE, errors=errs) is None
    assert len(errs) == 1
    assert "state.json" in str(errs[0]) or RUN_ID in str(errs[0])


def test_errors_out_param_untouched_for_clean_ledger(tmp_path):
    make_ledger(tmp_path, [task("T01", "pending")])
    errs = []
    assert agent.find_tasks([str(tmp_path)], SINCE, errors=errs)["source"] == "run"
    assert errs == []


def test_sdd_unreadable_plan_and_no_checkbox_reports_error(tmp_path):
    make_sdd(tmp_path, "# Progress\n\n计划: `docs/superpowers/plans/missing.md`\n\nTask 1: complete\n")
    errs = []
    assert agent.find_tasks([str(tmp_path)], SINCE, errors=errs) is None
    assert len(errs) >= 1


def test_session_empty_items_with_errors_reports_error(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    st = dict(SESSION, items=[], current=None, errors=["bad TodoWrite call"])
    errs = []
    assert agent.find_tasks([str(tmp_path)], SINCE, st, errors=errs) is None
    assert any("bad TodoWrite call" in str(e) for e in errs)


def test_good_ledger_has_no_errors(tmp_path):
    make_ledger(tmp_path, [task("T01", "pending")])
    assert agent.find_tasks([str(tmp_path)], SINCE)["errors"] == []


def test_state_json_without_tasks_list_does_not_crash(tmp_path):
    make_ledger(tmp_path, [], state_text=json.dumps({"run_id": RUN_ID}))
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r is None or r["source"] != "run" or r["items"] == []


# ---------- 3. sdd ----------

PLAN_TXT = ("# Plan\n\n### Task 1: Alpha\nx\n\n### Task 2: Beta\nx\n\n### Task 3: Gamma\nx\n")


def sdd_with_plan(tmp_path, progress_lines, mtime=NOW - 100, marker="计划:"):
    plan = put(tmp_path / "docs" / "superpowers" / "plans" / "big.md", PLAN_TXT)
    touch(plan, NOW - 100)
    make_sdd(tmp_path, "# Progress\n\n%s `docs/superpowers/plans/big.md`\n\n%s\n" % (
        marker, progress_lines), mtime=mtime)


def test_sdd_with_plan_complete_and_first_remaining_doing(tmp_path):
    sdd_with_plan(tmp_path, "Task 1: complete")
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "sdd"
    m = by_id(r)
    assert [i["title"] for i in r["items"]] == ["Alpha", "Beta", "Gamma"]
    assert m["1"]["status"] == "done"
    assert m["2"]["status"] == "doing"
    assert m["3"]["status"] == "todo"


def test_sdd_with_plan_no_progress_first_is_doing(tmp_path):
    sdd_with_plan(tmp_path, "nothing yet")
    r = agent.find_tasks([str(tmp_path)], SINCE)
    st = [i["status"] for i in r["items"]]
    assert st == ["doing", "todo", "todo"]


def test_sdd_with_plan_all_complete_no_doing(tmp_path):
    sdd_with_plan(tmp_path, "Task 1: complete\nTask 2: complete\nTask 3: complete")
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert [i["status"] for i in r["items"]] == ["done"] * 3


def test_sdd_gap_in_complete_marks_first_remaining_doing(tmp_path):
    sdd_with_plan(tmp_path, "Task 1: complete\nTask 3: complete")
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert [i["status"] for i in r["items"]] == ["done", "doing", "done"]


@pytest.mark.parametrize("marker", ["计划:", "计划：", "plan:", "Plan:"])
def test_sdd_plan_line_variants(tmp_path, marker):
    sdd_with_plan(tmp_path, "Task 1: complete", marker=marker)
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "sdd"
    assert len(r["items"]) == 3


def test_sdd_checkbox_only(tmp_path):
    make_sdd(tmp_path, "# Progress\n- [x] first thing\n- [ ] second thing\n- [ ] third\n")
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "sdd"
    assert [i["title"] for i in r["items"]] == ["first thing", "second thing", "third"]
    assert [i["status"] for i in r["items"]][0] == "done"
    assert all(i["status"] != "done" for i in r["items"][1:])


def test_sdd_stale_mtime_not_a_source(tmp_path):
    make_sdd(tmp_path, "- [x] a\n- [ ] b\n", mtime=SINCE - 10)
    assert agent.find_tasks([str(tmp_path)], SINCE) is None


def test_sdd_mtime_exactly_since_is_fresh(tmp_path):
    make_sdd(tmp_path, "- [x] a\n- [ ] b\n", mtime=SINCE)
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r is not None and r["source"] == "sdd"


def test_sdd_stale_with_plan_not_a_source(tmp_path):
    sdd_with_plan(tmp_path, "Task 1: complete", mtime=SINCE - 5)
    r = agent.find_tasks([str(tmp_path)], SINCE)
    # the plan file itself is fresh, so it may fall back to "plan", never "sdd"
    assert r is None or r["source"] == "plan"


def test_ledger_beats_sdd(tmp_path):
    make_ledger(tmp_path, [task("T01", "pending")])
    make_sdd(tmp_path, "- [x] a\n")
    assert agent.find_tasks([str(tmp_path)], SINCE)["source"] == "run"


def test_sdd_beats_session_and_plan(tmp_path):
    make_sdd(tmp_path, "- [x] a\n- [ ] b\n")
    make_plan(tmp_path)
    assert agent.find_tasks([str(tmp_path)], SINCE, dict(SESSION))["source"] == "sdd"


# ---------- 4. priority ----------

def test_session_beats_fresh_plan(tmp_path):
    make_plan(tmp_path)
    r = agent.find_tasks([str(tmp_path)], SINCE, dict(SESSION))
    assert r["source"] == "session"
    assert r["items"][0]["title"] == "a"


def test_fresh_plan_only(tmp_path):
    make_plan(tmp_path, n=3)
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "plan"
    assert r["label"] == "计划（无进度记录）"
    assert [i["title"] for i in r["items"]] == ["Title 1", "Title 2", "Title 3"]
    assert all(i["status"] == "todo" for i in r["items"])
    assert r["checkout"] == str(tmp_path)


def test_plan_stale_not_a_source(tmp_path):
    make_plan(tmp_path, mtime=SINCE - 60)
    assert agent.find_tasks([str(tmp_path)], SINCE) is None


def test_plan_picks_newest_fresh_file(tmp_path):
    make_plan(tmp_path, name="old.md", n=1, mtime=NOW - 5000)
    new = make_plan(tmp_path, name="new.md", n=2, mtime=NOW - 10)
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert len(r["items"]) == 2
    assert r["path"].endswith("new.md") or r["path"] == new


def test_plan_ignores_stale_when_newer_exists_only_if_fresh(tmp_path):
    make_plan(tmp_path, name="old.md", n=1, mtime=SINCE - 500)
    make_plan(tmp_path, name="new.md", n=2, mtime=NOW - 10)
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert len(r["items"]) == 2


def test_ledger_beats_session_tasks(tmp_path):
    make_ledger(tmp_path, [task("T01", "pending")])
    r = agent.find_tasks([str(tmp_path)], SINCE, dict(SESSION))
    assert r["source"] == "run"
    assert r["items"][0]["id"] == "T01"


def test_session_only_returns_session_tasks(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    r = agent.find_tasks([str(tmp_path)], SINCE, dict(SESSION))
    assert r["source"] == "session"
    assert r["label"] == "会话内任务清单"


def test_session_tasks_empty_dict_or_none_ignored(tmp_path):
    make_plan(tmp_path)
    assert agent.find_tasks([str(tmp_path)], SINCE, None)["source"] == "plan"


# ---------- 5. none / read-only ----------

def test_nothing_returns_none(tmp_path):
    assert agent.find_tasks([str(tmp_path)], SINCE) is None


def test_empty_checkouts_returns_none():
    assert agent.find_tasks([], SINCE) is None


def test_nonexistent_checkout_returns_none(tmp_path):
    assert agent.find_tasks([str(tmp_path / "nope")], SINCE) is None


def test_plan_without_task_headings_gives_no_source_or_empty(tmp_path):
    p = put(tmp_path / "docs" / "superpowers" / "plans" / "x.md", "# just prose\n")
    touch(p, NOW - 10)
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r is None or r["items"] == []


@pytest.mark.parametrize("setup", ["ledger", "sdd", "plan", "corrupt"])
def test_find_tasks_writes_nothing(tmp_path, setup):
    if setup == "ledger":
        make_ledger(tmp_path, [task("T01", "done")],
                    log=[{"ts": iso(NOW - 5), "event": "merged", "task": "T01"}])
    elif setup == "sdd":
        make_sdd(tmp_path, "- [x] a\n- [ ] b\n")
    elif setup == "plan":
        make_plan(tmp_path)
    else:
        make_ledger(tmp_path, [], state_text="{bad")
    before = snapshot(tmp_path)
    agent.find_tasks([str(tmp_path)], SINCE, dict(SESSION))
    assert snapshot(tmp_path) == before


def test_session_tasks_not_mutated(tmp_path):
    make_plan(tmp_path)
    s = dict(SESSION)
    import copy
    ref = copy.deepcopy(s)
    agent.find_tasks([str(tmp_path)], SINCE, s)
    assert s == ref
