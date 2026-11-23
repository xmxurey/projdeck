"""D7 along the agent's real collection path (T11): what act_live writes, carried through the
live cache to board.build and on to the web page and the terminal. A session whose record
cannot be read is never folded on a brief host; a session that only carries a task-list
warning folds as usual and is counted as a warning."""
import os
import re
import time
from html import unescape

import pytest

from projdeck import board, sync, ui, web
from tests.test_agent_live import fake_claude, iso, u

HOST = {"name": "em-host", "local": True, "brief": True}
CFG = {"hosts": [HOST], "ssh_timeout": 5}


def task_update(cwd, ts, task_id):
    return {"type": "assistant", "cwd": cwd, "timestamp": iso(ts),
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "TaskUpdate",
                 "input": {"taskId": task_id, "status": "completed"}}]}}


def agent_entry(i, cwd, sid, now):
    return {"id": "a%d" % i, "cwd": cwd, "kind": "interactive", "startedAt": (now - 100) * 1000,
            "sessionId": sid, "pid": 999990 + i, "status": "busy"}


@pytest.fixture
def chain(tmp_path, monkeypatch, make_session):
    """Two live sessions on brief host em-host, each in its own project: UNREAD (its record
    is a directory, reading it raises) and WARN (a TaskUpdate naming an unknown taskId).
    Collected by sync.sync_live, read back by sync.load_lives, built by board.build."""
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    now = int(time.time())
    unread, warn = str(home / "unread-proj"), str(home / "warn-proj")
    os.makedirs(unread)
    os.makedirs(warn)
    make_session(claude, warn, "WARN",
                 [u(warn, now - 100, "go"), task_update(warn, now - 90, "99")],
                 mtime=now - 10)
    os.makedirs(os.path.join(str(claude), "projects", unread.replace("/", "-"), "UNREAD.jsonl"))
    fake_claude(tmp_path, monkeypatch, [agent_entry(0, unread, "UNREAD", now),
                                        agent_entry(1, warn, "WARN", now)])
    assert sync.sync_live(HOST, CFG, now=now)["ok"] is True
    bd = board.build([], sync.load_lives(CFG), CFG, now)
    return bd, now, {"UNREAD": unread, "WARN": warn}


def by_id(bd):
    return {s["id"]: s for s in bd["busy"] + bd["idle"] + bd["waiting"]}


def test_state_error_survives_the_chain_and_decides_the_fold(chain):
    bd, _, _ = chain
    s = by_id(bd)
    assert set(s) == {"UNREAD", "WARN"}
    assert s["UNREAD"]["state"] == "busy" and s["WARN"]["state"] == "busy"
    assert s["UNREAD"].get("state_error") is True and "Is a directory" in s["UNREAD"]["errors"][0]
    assert s["WARN"]["state_error"] is False
    assert s["WARN"]["errors"] == ["TaskUpdate: unknown taskId 99"]
    assert board.folded(bd, s["UNREAD"]) is False
    assert board.folded(bd, s["WARN"]) is True
    [e] = bd["brief"]["hosts"]
    assert board.brief_label(e) == "em-host · 进行中 1（另有单独列出的 进行中 1：出错 1） · 告警 1"


def test_web_lists_the_unread_session_apart_and_folds_the_warned_one(chain):
    bd, now, _ = chain
    html = web.render(bd, "home", fragment=True, now=now)
    col = re.search(r'<div class="column col-busy">.*?(?=<div class="column |</section>)',
                    html, re.S).group(0)
    [block] = re.findall(r'<details class="brief-fold[^"]*" data-key="brief-em-host">.*?</details>',
                         col, re.S)
    summary = unescape(re.search(r"<summary>(.*?)</summary>", block, re.S).group(1))
    assert "告警 1" in summary and "出错 1" in summary
    assert "warn-proj" in block and "unread-proj" not in block
    apart = col.replace(block, "")
    assert "unread-proj" in apart and "warn-proj" not in apart
    assert "Is a directory" in unescape(apart)  # its read error shows on its own card


def test_terminal_marks_the_unread_session_and_folds_the_warned_one(chain):
    bd, now, paths = chain
    fields = [ln.split(ui.SEP, 2) for ln in ui.project_lines([], now, bd)]
    by_path = {ui.from_field(f[1]): ui.strip_ansi(f[2]) for f in fields if f[1] != "brief:"}
    assert by_path.get(paths["UNREAD"], "").endswith("⚠ 状态读取失败"), by_path
    assert paths["WARN"] not in by_path
    [summary] = [ui.strip_ansi(f[2]) for f in fields if f[1] == "brief:"]
    assert summary.startswith("◉ em-host · 进行中 1") and summary.endswith(" · 告警 1")
    preview = ui.strip_ansi(ui.brief_preview_text(bd, [], "em-host", now))
    assert "warn-proj" in preview and "⚠ TaskUpdate: unknown taskId 99" in preview
    assert "unread-proj" not in preview
