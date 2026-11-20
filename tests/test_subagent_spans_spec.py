"""Spec-derived tests for T08: only periods a subagent really wrote count as sub.

Spec: design.md 2.4. subagent_spans(session_dir, memo=None) -> [[start, end]] where start is the
first "timestamp" in <session_dir>/subagents/*.jsonl and end is the file mtime; unreadable or
timestamp-less files, and files with start > end, are skipped. segments(..., sub_spans) records
the part of a P-gap covered by the span union as sub, and the rest as wait when longer than 180s.
"""
import datetime
import json
import os

import pytest

from projdeck import agent

T = 1_800_000_000
NOW = T + 100_000
SINCE = T - 50


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def tx(events):
    return {"state": {"pending_tool": None, "ask_started": None},
            "events": [tuple(e) for e in events], "asks": []}


def seg(events, spans=()):
    return [list(s) for s in agent.segments(tx(events), NOW, SINCE, False, "idle", 0, spans)]


LATE_P = [(T, "H"), (T + 60, "C"), (T + 10800, "P"), (T + 10830, "C")]


# ---- segments level (AC1, AC2) ---------------------------------------------

def test_ac1_late_p_without_spans_is_wait_not_sub():
    out = seg(LATE_P)
    assert not [s for s in out if s[0] == "sub"]
    assert ["wait", T + 60, T + 10800] in out


def test_ac1_sub_spans_default_is_empty():
    t = tx(LATE_P)
    a = agent.segments(t, NOW, SINCE, False, "idle", 0)
    b = agent.segments(t, NOW, SINCE, False, "idle", 0, ())
    assert [list(s) for s in a] == [list(s) for s in b]


def test_ac2_span_at_start_gives_sub_then_wait():
    out = seg(LATE_P, [[T + 60, T + 600]])
    assert ["sub", T + 60, T + 600] in out
    assert ["wait", T + 600, T + 10800] in out
    assert ["busy", T, T + 60] in out


def test_span_in_middle_splits_wait_on_both_sides():
    out = seg(LATE_P, [[T + 1000, T + 2000]])
    assert ["wait", T + 60, T + 1000] in out
    assert ["sub", T + 1000, T + 2000] in out
    assert ["wait", T + 2000, T + 10800] in out


def test_span_covering_whole_gap_has_no_wait():
    out = seg(LATE_P, [[T, T + 20000]])
    assert ["sub", T + 60, T + 10800] in out
    assert not [s for s in out if s[0] == "wait"]


def test_span_outside_gap_is_ignored():
    out = seg(LATE_P, [[T + 20000, T + 30000]])
    assert not [s for s in out if s[0] == "sub"]
    assert ["wait", T + 60, T + 10800] in out


def test_span_clipped_to_gap():
    out = seg(LATE_P, [[T - 500, T + 300]])
    assert ["sub", T + 60, T + 300] in out
    assert ["wait", T + 300, T + 10800] in out


def test_two_disjoint_spans_both_sub():
    out = seg(LATE_P, [[T + 100, T + 1000], [T + 5000, T + 6000]])
    assert ["sub", T + 100, T + 1000] in out
    assert ["sub", T + 5000, T + 6000] in out
    assert ["wait", T + 1000, T + 5000] in out
    assert ["wait", T + 6000, T + 10800] in out


def test_overlapping_spans_are_unioned():
    out = seg(LATE_P, [[T + 100, T + 1000], [T + 800, T + 2000]])
    assert ["sub", T + 100, T + 2000] in out
    assert ["wait", T + 2000, T + 10800] in out


def test_spans_order_does_not_matter():
    a = seg(LATE_P, [[T + 100, T + 1000], [T + 5000, T + 6000]])
    b = seg(LATE_P, [[T + 5000, T + 6000], [T + 100, T + 1000]])
    assert a == b


def test_uncovered_remainder_boundary_180_not_wait_181_wait():
    out = seg(LATE_P, [[T + 60, T + 10800 - 180]])
    assert not [s for s in out if s[0] == "wait"]
    out = seg(LATE_P, [[T + 60, T + 10800 - 181]])
    assert ["wait", T + 10800 - 181, T + 10800] in out


def test_short_p_gap_without_spans_not_recorded():
    out = seg([(T, "H"), (T + 60, "C"), (T + 100, "P"), (T + 150, "C")])
    assert [s[0] for s in out] == ["busy", "busy"] or all(s[0] == "busy" for s in out)


def test_h_gap_rule_unchanged_by_spans():
    ev = [(T, "H"), (T + 60, "C"), (T + 10800, "H"), (T + 10830, "C")]
    a = seg(ev)
    b = seg(ev, [[T + 60, T + 600]])
    assert a == b
    assert not [s for s in a if s[0] == "sub"]


def test_m_gap_not_recorded_even_with_spans():
    ev = [(T, "H"), (T + 60, "C"), (T + 10800, "M"), (T + 10830, "C")]
    out = seg(ev, [[T + 60, T + 600]])
    assert not [s for s in out if s[0] in ("sub", "wait")]


# ---- subagent_spans (design 2.4) -------------------------------------------

def sub_file(session_dir, name, rows, mtime):
    d = os.path.join(str(session_dir), "subagents")
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, name)
    with open(fp, "w") as f:
        for r in rows:
            f.write(r if isinstance(r, str) else json.dumps(r, separators=(",", ":")))
            f.write("\n")
    os.utime(fp, (mtime, mtime))
    return fp


def row(ts, text="x"):
    return {"type": "user", "timestamp": iso(ts), "message": {"role": "user", "content": text}}


def test_spans_missing_dir_and_empty_dir(tmp_path):
    assert agent.subagent_spans(str(tmp_path / "nope")) == []
    os.makedirs(str(tmp_path / "s" / "subagents"))
    assert agent.subagent_spans(str(tmp_path / "s")) == []


def test_spans_start_first_timestamp_end_mtime(tmp_path):
    sub_file(tmp_path, "agent-x.jsonl", [row(T + 100), row(T + 400)], T + 900)
    assert agent.subagent_spans(str(tmp_path)) == [[T + 100, T + 900]]


def test_spans_start_is_first_row_with_timestamp(tmp_path):
    sub_file(tmp_path, "agent-x.jsonl", [{"type": "meta"}, row(T + 200)], T + 900)
    assert agent.subagent_spans(str(tmp_path)) == [[T + 200, T + 900]]


def test_spans_ignores_non_jsonl_files(tmp_path):
    sub_file(tmp_path, "agent-x.jsonl", [row(T + 100)], T + 900)
    sub_file(tmp_path, "agent-x.meta.json", [row(T + 100)], T + 900)
    sub_file(tmp_path, "notes.txt", [row(T + 100)], T + 900)
    assert agent.subagent_spans(str(tmp_path)) == [[T + 100, T + 900]]


def test_spans_two_files_two_spans(tmp_path):
    sub_file(tmp_path, "agent-a.jsonl", [row(T + 100)], T + 500)
    sub_file(tmp_path, "agent-b.jsonl", [row(T + 3000)], T + 4000)
    got = sorted(agent.subagent_spans(str(tmp_path)))
    assert got == [[T + 100, T + 500], [T + 3000, T + 4000]]


def test_spans_no_timestamp_skipped(tmp_path):
    sub_file(tmp_path, "agent-x.jsonl", [{"type": "user", "message": {"content": "hi"}}], T + 900)
    sub_file(tmp_path, "agent-y.jsonl", ["not json at all"], T + 900)
    sub_file(tmp_path, "agent-z.jsonl", [], T + 900)
    assert agent.subagent_spans(str(tmp_path)) == []


def test_spans_start_after_end_skipped(tmp_path):
    sub_file(tmp_path, "agent-x.jsonl", [row(T + 2000)], T + 1000)
    sub_file(tmp_path, "agent-ok.jsonl", [row(T + 100)], T + 1000)
    assert agent.subagent_spans(str(tmp_path)) == [[T + 100, T + 1000]]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read files with mode 000, so unreadable cannot be simulated")
def test_spans_unreadable_skipped(tmp_path):
    fp = sub_file(tmp_path, "agent-x.jsonl", [row(T + 100)], T + 900)
    sub_file(tmp_path, "agent-ok.jsonl", [row(T + 200)], T + 900)
    os.chmod(fp, 0)
    try:
        assert agent.subagent_spans(str(tmp_path)) == [[T + 200, T + 900]]
    finally:
        os.chmod(fp, 0o644)


def test_spans_directory_named_jsonl_skipped(tmp_path):
    os.makedirs(str(tmp_path / "subagents" / "agent-d.jsonl"))
    sub_file(tmp_path, "agent-ok.jsonl", [row(T + 200)], T + 900)
    assert agent.subagent_spans(str(tmp_path)) == [[T + 200, T + 900]]


def test_spans_memo_tracks_mtime_and_size(tmp_path):
    fp = sub_file(tmp_path, "agent-x.jsonl", [row(T + 100)], T + 900)
    memo = {}
    assert agent.subagent_spans(str(tmp_path), memo) == [[T + 100, T + 900]]
    assert agent.subagent_spans(str(tmp_path), memo) == [[T + 100, T + 900]]
    os.utime(fp, (T + 1500, T + 1500))
    assert agent.subagent_spans(str(tmp_path), memo) == [[T + 100, T + 1500]]
    # size change with same mtime also invalidates
    with open(fp, "w") as f:
        f.write(json.dumps(row(T + 300), separators=(",", ":")) + "\n"
                + json.dumps(row(T + 301), separators=(",", ":")) + "\n")
    os.utime(fp, (T + 1500, T + 1500))
    assert agent.subagent_spans(str(tmp_path), memo) == [[T + 300, T + 1500]]


def test_spans_memo_hit_does_not_reread(tmp_path, monkeypatch):
    sub_file(tmp_path, "agent-x.jsonl", [row(T + 100)], T + 900)
    memo = {}
    agent.subagent_spans(str(tmp_path), memo)
    assert memo, "memo should hold the per-file result"
    real_open = open
    opened = []

    def spy(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", spy, raising=False)
    assert agent.subagent_spans(str(tmp_path), memo) == [[T + 100, T + 900]]
    assert not [p for p in opened if p.endswith("agent-x.jsonl")]


def test_spans_deleted_file_disappears_with_memo(tmp_path):
    fp = sub_file(tmp_path, "agent-x.jsonl", [row(T + 100)], T + 900)
    memo = {}
    agent.subagent_spans(str(tmp_path), memo)
    os.remove(fp)
    assert agent.subagent_spans(str(tmp_path), memo) == []


# ---- end to end through act_live (AC3, AC4, AC5) ---------------------------

E2E_NOW = T + 40_000
SID = "sess1"


def u(cwd, ts, text, **extra):
    d = {"type": "user", "cwd": cwd, "entrypoint": "cli", "timestamp": iso(ts),
         "message": {"role": "user", "content": text}}
    d.update(extra)
    return d


def a(cwd, ts, text="ok"):
    return {"type": "assistant", "cwd": cwd, "timestamp": iso(ts),
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


@pytest.fixture
def world(tmp_path, monkeypatch, make_session):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    claude = home / ".claude"
    claude.mkdir()
    cwd = str(home / "proj")
    os.makedirs(cwd)

    class W:
        pass
    w = W()
    w.claude, w.cwd = claude, cwd

    def build(origin=None, p_text="worker done"):
        p = u(cwd, T + 10800, p_text, **({"origin": origin} if origin else {}))
        if origin is None:
            p.pop("entrypoint")
            p["origin"] = {"kind": "peer"}
        fp = make_session(claude, cwd, SID, [u(cwd, T, "go"), a(cwd, T + 60), p,
                                             a(cwd, T + 10830)], mtime=T + 10830)
        w.fp = fp
        w.sdir = os.path.join(os.path.dirname(fp), SID)
        return fp

    def live(memo=None):
        req = {"action": "live", "claude_dir": str(claude), "paths": [cwd],
               "now": E2E_NOW, "since": E2E_NOW - 86400}
        out = agent.act_live(req, memo)
        return {s["id"]: s for s in out["sessions"]}[SID]

    w.build, w.live = build, live
    return w


def kinds(s):
    return [list(x) for x in s["segments"]]


@pytest.mark.parametrize("origin", [{"kind": "peer"}, {"kind": "task-notification"}])
def test_e2e_no_subagent_files_late_p_is_wait(world, origin):
    world.build(origin)
    segs = kinds(world.live())
    assert not [x for x in segs if x[0] == "sub"]
    assert ["wait", T + 60, T + 10800] in segs


def test_e2e_subagent_file_gives_sub_start_to_mtime(world):
    world.build()
    S, E = T + 100, T + 700
    sub_file(world.sdir, "agent-x.jsonl", [row(S)], E)
    segs = kinds(world.live())
    assert ["sub", S, E] in segs
    assert ["wait", E, T + 10800] in segs
    assert not [x for x in segs if x[0] == "wait" and x[1] < S]  # 40s lead-in is under the wait threshold


def test_e2e_task_notification_with_partial_subagent(world):
    world.build({"kind": "task-notification"})
    sub_file(world.sdir, "agent-x.jsonl", [row(T + 5000)], T + 6000)
    segs = kinds(world.live())
    assert ["sub", T + 5000, T + 6000] in segs
    assert ["wait", T + 60, T + 5000] in segs
    assert ["wait", T + 6000, T + 10800] in segs
    assert not [x for x in segs if x[0] == "sub" and (x[1] < T + 5000 or x[2] > T + 6000)]


def test_e2e_no_timestamp_file_is_wait(world):
    world.build()
    sub_file(world.sdir, "agent-x.jsonl", [{"type": "user", "message": {"content": "hi"}}], T + 700)
    segs = kinds(world.live())
    assert not [x for x in segs if x[0] == "sub"]
    assert ["wait", T + 60, T + 10800] in segs


def test_e2e_empty_and_garbage_files_are_wait(world):
    world.build()
    sub_file(world.sdir, "agent-a.jsonl", [], T + 700)
    sub_file(world.sdir, "agent-b.jsonl", ["{{{ garbage"], T + 700)
    segs = kinds(world.live())
    assert not [x for x in segs if x[0] == "sub"]
    assert ["wait", T + 60, T + 10800] in segs


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read mode-000 files, so unreadable cannot be simulated")
def test_e2e_unreadable_file_is_wait(world):
    world.build()
    fp = sub_file(world.sdir, "agent-x.jsonl", [row(T + 100)], T + 700)
    os.chmod(fp, 0)
    try:
        segs = kinds(world.live())
    finally:
        os.chmod(fp, 0o644)
    assert not [x for x in segs if x[0] == "sub"]
    assert ["wait", T + 60, T + 10800] in segs


def test_e2e_same_memo_twice_same_result_then_mtime_change_moves_end(world):
    world.build()
    fp = sub_file(world.sdir, "agent-x.jsonl", [row(T + 100)], T + 700)
    memo = {}
    first = kinds(world.live(memo))
    second = kinds(world.live(memo))
    assert first == second
    assert ["sub", T + 100, T + 700] in first
    os.utime(fp, (T + 1200, T + 1200))
    third = kinds(world.live(memo))
    assert ["sub", T + 100, T + 1200] in third
    assert ["wait", T + 1200, T + 10800] in third
    assert not [x for x in third if x == ["sub", T + 100, T + 700]]


def test_e2e_memo_does_not_leak_after_subagent_file_removed(world):
    world.build()
    fp = sub_file(world.sdir, "agent-x.jsonl", [row(T + 100)], T + 700)
    memo = {}
    assert ["sub", T + 100, T + 700] in kinds(world.live(memo))
    os.remove(fp)
    segs = kinds(world.live(memo))
    assert not [x for x in segs if x[0] == "sub"]
    assert ["wait", T + 60, T + 10800] in segs
