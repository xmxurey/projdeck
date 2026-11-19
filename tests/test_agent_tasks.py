import hashlib
import json
import os

from projdeck import agent

SINCE = 1000
PLAN = """# Plan

### Task 1: First thing
body
### Task 2: Second thing
### Task 3: Third thing
"""


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def make_run(root, tasks, log_lines=(), run_id="r1", current="T02"):
    runs = root / "docs" / "dev" / "runs"
    write(runs / ".active", run_id + "\n")
    write(runs / run_id / "state.json",
          json.dumps({"run_id": run_id, "current_task": current, "tasks": tasks}))
    write(runs / run_id / "log.jsonl", "\n".join(json.dumps(x) if not isinstance(x, str) else x
                                                  for x in log_lines))


def tree_hash(root):
    h = hashlib.sha256()
    for dp, dn, fn in sorted(os.walk(str(root))):
        dn.sort()
        for f in sorted(fn):
            p = os.path.join(dp, f)
            h.update(p.encode())
            h.update(open(p, "rb").read())
            h.update(str(os.stat(p).st_mtime_ns).encode())
    return h.hexdigest()


RUN_TASKS = [
    {"id": "T01", "title": "a", "status": "done", "round": 0},
    {"id": "T02", "title": "b", "status": "in_progress", "round": 0, "stage": "implement"},
    {"id": "T03", "title": "c", "status": "done", "round": 1},
    {"id": "T04", "title": "d", "status": "pending"},
    {"id": "T05", "title": "e", "status": "blocked"},
    {"id": "T06", "title": "f", "status": "review"},
]


def test_run_ledger(tmp_path):
    make_run(tmp_path, RUN_TASKS, [
        {"ts": "2026-11-18T15:05:35+00:00", "event": "decision", "task": "T01", "note": "merged"},
        {"ts": "2026-11-18T16:00:00+00:00", "event": "merged", "task": "T03"},
        {"ts": "2026-11-18T16:00:00+00:00", "event": "decision", "task": "T04", "note": "other"},
        "not json",
    ])
    r = agent.find_tasks([str(tmp_path)], SINCE, {"items": [{"id": "1"}]})
    assert r["source"] == "run"
    assert r["current"] == "T02"
    assert [i["status"] for i in r["items"]] == ["done", "doing", "done", "todo", "stuck", "doing"]
    assert [i["note"] for i in r["items"]][:3] == ["一次通过", "implement", "评审 2 轮"]
    assert r["merges"] == [["T01", 1795014335], ["T03", 1795017600]]
    assert all(type(m[1]) is int for m in r["merges"])
    assert r["errors"] == []
    assert r["checkout"] == str(tmp_path)


def test_bad_merge_entries_dropped_with_errors(tmp_path):
    make_run(tmp_path, RUN_TASKS, [
        {"ts": "garbage", "event": "merged", "task": "T01"},
        {"event": "merged", "task": "T02"},
        {"ts": "2026-11-18T16:00:00Z", "event": "merged"},
        {"ts": 1795017600, "event": "merged", "task": "T03"},
    ])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["merges"] == [["T03", 1795017600]]
    assert len(r["errors"]) == 3


def test_worktree_ledger_first(tmp_path):
    wt, root = tmp_path / "wt", tmp_path / "root"
    make_run(wt, RUN_TASKS, run_id="wtrun")
    make_run(root, [{"id": "X", "title": "x", "status": "pending"}], run_id="rootrun")
    r = agent.find_tasks([str(wt), str(root)], SINCE)
    assert r["checkout"] == str(wt)
    assert r["items"][0]["id"] == "T01"
    r = agent.find_tasks([str(root), str(wt)], SINCE)
    assert r["checkout"] == str(root)


def test_empty_ledger_tasks(tmp_path):
    make_run(tmp_path, [])
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "run" and r["items"] == [] and r["label"] == "还没有计划"


def test_corrupt_state_skipped_with_error(tmp_path):
    make_run(tmp_path, RUN_TASKS)
    write(tmp_path / "docs/dev/runs/r1/state.json", "{broken")
    write(tmp_path / "docs/superpowers/plans/p.md", PLAN)
    os.utime(str(tmp_path / "docs/superpowers/plans/p.md"), (2000, 2000))
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "plan"
    assert len(r["errors"]) == 1 and "state.json" in r["errors"][0]


def test_corrupt_state_nothing_else_is_none(tmp_path):
    make_run(tmp_path, RUN_TASKS)
    write(tmp_path / "docs/dev/runs/r1/state.json", "{broken")
    errs = []
    assert agent.find_tasks([str(tmp_path)], SINCE, errors=errs) is None
    assert len(errs) == 1 and "state.json" in errs[0]


def test_sdd_missing_plan_and_no_checkboxes_reports_error(tmp_path):
    prog = write(tmp_path / ".superpowers/sdd/progress.md", "计划: docs/nope.md\n")
    os.utime(str(prog), (2000, 2000))
    errs = []
    assert agent.find_tasks([str(tmp_path)], SINCE, errors=errs) is None
    assert len(errs) == 1 and "nope.md" in errs[0]


def test_session_errors_with_empty_items_reach_caller(tmp_path):
    errs = []
    st = {"items": [], "errors": ["todos is not a list"]}
    assert agent.find_tasks([str(tmp_path)], SINCE, st, errors=errs) is None
    assert errs == ["todos is not a list"]


def test_errors_out_param_also_filled_when_source_found(tmp_path):
    make_run(tmp_path, RUN_TASKS, [{"ts": "bad", "event": "merged", "task": "T01"}])
    errs = []
    r = agent.find_tasks([str(tmp_path)], SINCE, errors=errs)
    assert errs == r["errors"] and len(errs) == 1


def test_sdd_with_plan(tmp_path):
    write(tmp_path / "docs/superpowers/plans/p.md", PLAN)
    prog = write(tmp_path / ".superpowers/sdd/progress.md",
                 "计划: `docs/superpowers/plans/p.md`\n\nTask 1: complete\n")
    os.utime(str(prog), (2000, 2000))
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "sdd"
    assert [(i["id"], i["status"]) for i in r["items"]] == [("1", "done"), ("2", "doing"), ("3", "todo")]
    assert r["items"][1]["title"] == "Second thing"
    assert r["current"] == "2"


def test_sdd_checkboxes_only(tmp_path):
    prog = write(tmp_path / ".superpowers/sdd/progress.md", "- [x] alpha\n- [ ] beta\n")
    os.utime(str(prog), (2000, 2000))
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "sdd"
    assert [(i["title"], i["status"]) for i in r["items"]] == [("alpha", "done"), ("beta", "todo")]


def test_sdd_stale_is_not_a_source(tmp_path):
    prog = write(tmp_path / ".superpowers/sdd/progress.md", "- [x] alpha\n")
    os.utime(str(prog), (500, 500))
    assert agent.find_tasks([str(tmp_path)], SINCE) is None


def fresh_plan(tmp_path):
    p = write(tmp_path / "docs/superpowers/plans/p.md", PLAN)
    os.utime(str(p), (2000, 2000))


SESSION = {"source": "session", "label": "会话内任务清单", "path": "/t.jsonl", "checkout": "/x",
           "current": "1", "items": [{"id": "1", "title": "s", "status": "doing", "note": ""}],
           "merges": [], "errors": []}


def test_session_beats_plan(tmp_path):
    fresh_plan(tmp_path)
    r = agent.find_tasks([str(tmp_path)], SINCE, SESSION)
    assert r["source"] == "session" and r["items"][0]["title"] == "s"


def test_plan_only(tmp_path):
    fresh_plan(tmp_path)
    r = agent.find_tasks([str(tmp_path)], SINCE)
    assert r["source"] == "plan" and r["label"] == "计划（无进度记录）"
    assert [i["status"] for i in r["items"]] == ["todo"] * 3


def test_stale_plan_ignored(tmp_path):
    p = write(tmp_path / "docs/superpowers/plans/p.md", PLAN)
    os.utime(str(p), (500, 500))
    assert agent.find_tasks([str(tmp_path)], SINCE) is None


def test_run_beats_session(tmp_path):
    make_run(tmp_path, RUN_TASKS)
    assert agent.find_tasks([str(tmp_path)], SINCE, SESSION)["source"] == "run"


def test_session_alone_and_none(tmp_path):
    assert agent.find_tasks([str(tmp_path)], SINCE) is None
    assert agent.find_tasks([str(tmp_path)], SINCE, SESSION)["source"] == "session"


def test_read_only(tmp_path):
    make_run(tmp_path, RUN_TASKS, [{"ts": "2026-11-18T15:05:35+00:00", "event": "merged", "task": "T01"}])
    fresh_plan(tmp_path)
    before = tree_hash(tmp_path)
    agent.find_tasks([str(tmp_path)], SINCE, SESSION)
    assert tree_hash(tmp_path) == before
