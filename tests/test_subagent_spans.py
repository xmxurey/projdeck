"""subagent_spans and the act_live chain: only periods a subagent was writing count as sub."""
import datetime
import json
import os

import pytest

from projdeck import agent

NOW = 1_800_000_000


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def rec(kind, cwd, ts, text, **extra):
    d = {"type": kind, "cwd": cwd, "timestamp": iso(ts),
         "message": {"role": kind, "content": text if kind == "user" else
                     [{"type": "text", "text": text}]}}
    d.update(extra)
    return d


def sub_file(sdir, name, lines, mtime):
    os.makedirs(sdir, exist_ok=True)
    fp = os.path.join(sdir, name)
    with open(fp, "w") as f:
        for ln in lines:
            f.write(ln if isinstance(ln, str) else json.dumps(ln, separators=(",", ":")))
            f.write("\n")
    os.utime(fp, (mtime, mtime))
    return fp


def test_span_is_first_timestamp_to_mtime(tmp_path):
    sdir = tmp_path / "sid" / "subagents"
    sub_file(str(sdir), "agent-a.jsonl", ['{"x":1}', {"timestamp": iso(NOW - 900)}], NOW - 100)
    assert agent.subagent_spans(str(tmp_path / "sid")) == [[NOW - 900, NOW - 100]]


def test_missing_dir_timestampless_and_inverted_are_skipped(tmp_path):
    assert agent.subagent_spans(str(tmp_path / "nope")) == []
    sdir = str(tmp_path / "sid" / "subagents")
    sub_file(sdir, "agent-a.jsonl", ['{"x":1}'], NOW)
    sub_file(sdir, "agent-b.jsonl", [{"timestamp": iso(NOW)}], NOW - 50)
    sub_file(sdir, "agent-c.meta.json", [{"timestamp": iso(NOW - 9)}], NOW)
    assert agent.subagent_spans(str(tmp_path / "sid")) == []


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can read files with mode 000")
def test_unreadable_file_is_skipped(tmp_path):
    sdir = str(tmp_path / "sid" / "subagents")
    fp = sub_file(sdir, "agent-a.jsonl", [{"timestamp": iso(NOW - 900)}], NOW - 100)
    os.chmod(fp, 0)
    assert agent.subagent_spans(str(tmp_path / "sid")) == []


def test_memo_reuses_and_follows_mtime(tmp_path, monkeypatch):
    sdir = str(tmp_path / "sid" / "subagents")
    fp = sub_file(sdir, "agent-a.jsonl", [{"timestamp": iso(NOW - 900)}], NOW - 100)
    memo, calls = {}, []
    real = agent._sub_span
    monkeypatch.setattr(agent, "_sub_span", lambda p: calls.append(p) or real(p))
    assert agent.subagent_spans(str(tmp_path / "sid"), memo) == [[NOW - 900, NOW - 100]]
    assert agent.subagent_spans(str(tmp_path / "sid"), memo) == [[NOW - 900, NOW - 100]]
    assert len(calls) == 1
    os.utime(fp, (NOW - 50, NOW - 50))
    assert agent.subagent_spans(str(tmp_path / "sid"), memo) == [[NOW - 900, NOW - 50]]
    assert len(calls) == 2


# end to end through act_live

@pytest.fixture
def world(tmp_path, monkeypatch, make_session):
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("PROJ_CONFIG", str(tmp_path / "config"))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text("#!/bin/sh\necho '[]'\n")
    (bindir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", "%s:%s" % (bindir, os.environ["PATH"]))
    cwd = str(home / "proj")
    os.makedirs(cwd)
    T = NOW - 40000
    fp = make_session(claude, cwd, "sid", [
        rec("user", cwd, T, "go"), rec("assistant", cwd, T + 60, "ok"),
        rec("user", cwd, T + 10800, "<agent-message>done</agent-message>"),
        rec("assistant", cwd, T + 10830, "thanks")], mtime=NOW - 20000)
    sdir = os.path.join(os.path.dirname(fp), "sid", "subagents")
    return claude, cwd, sdir, T


def run(claude, cwd, memo=None):
    req = {"action": "live", "claude_dir": str(claude), "paths": [cwd], "now": NOW,
           "since": NOW - 86400}
    out = agent.act_live(req, memo)
    assert out["errors"] == []
    (s,) = out["sessions"]
    return s["segments"]


def test_act_live_marks_sub_only_inside_span(world):
    claude, cwd, sdir, T = world
    sub_file(sdir, "agent-x.jsonl", [{"timestamp": iso(T + 100)}], T + 700)
    segs = run(claude, cwd)
    assert ["sub", T + 100, T + 700] in segs
    assert ["wait", T + 60, T + 100] not in segs  # 40s leftover is below GAP_WAIT
    assert ["wait", T + 700, T + 10800] in segs


def test_act_live_without_subagent_files_has_no_sub(world):
    claude, cwd, sdir, T = world
    segs = run(claude, cwd)
    assert not [g for g in segs if g[0] == "sub"]
    assert ["wait", T + 60, T + 10800] in segs


def test_act_live_timestampless_subagent_file_is_wait(world):
    claude, cwd, sdir, T = world
    sub_file(sdir, "agent-x.jsonl", ['{"no":"time"}'], T + 700)
    segs = run(claude, cwd)
    assert not [g for g in segs if g[0] == "sub"]
    assert ["wait", T + 60, T + 10800] in segs


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can read files with mode 000")
def test_act_live_unreadable_subagent_file_is_wait(world):
    claude, cwd, sdir, T = world
    fp = sub_file(sdir, "agent-x.jsonl", [{"timestamp": iso(T + 100)}], T + 700)
    os.chmod(fp, 0)
    segs = run(claude, cwd)
    assert not [g for g in segs if g[0] == "sub"]
    assert ["wait", T + 60, T + 10800] in segs


def test_act_live_memo_is_stable_follows_mtime_and_prunes(world):
    claude, cwd, sdir, T = world
    fp = sub_file(sdir, "agent-x.jsonl", [{"timestamp": iso(T + 100)}], T + 700)
    memo = {}
    first = run(claude, cwd, memo)
    assert run(claude, cwd, memo) == first
    os.utime(fp, (T + 900, T + 900))
    assert ["sub", T + 100, T + 900] in run(claude, cwd, memo)
    assert len([k for k in memo if k[0] == "sub"]) == 1  # old key pruned
    os.remove(fp)
    run(claude, cwd, memo)
    assert [k for k in memo if k[0] == "sub"] == []
