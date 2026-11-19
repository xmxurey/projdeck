import json
import os

import pytest

from projdeck import agent

NOW = 1_800_000_000
H = 3600


@pytest.mark.parametrize("text", [
    "Shall I go ahead？",
    "Done.\n\nDo you want **option A?**",
    "Done.\n\nShould I continue (yes?)",
    "Really?\n",
    "Earlier text\n```\ncode\n```\n\nWhat now?",
    "tail of a block\n```\n\nShould I merge it?",   # truncation left an orphan fence
])
def test_asks_you_true(text):
    assert agent.asks_you(text)


@pytest.mark.parametrize("text", [
    "See https://x.example/?a=1",
    "Result:\n\n```\nwhy?\n```",
    "Run `what?` now",
    "Is this fine?\n\nAll done, merged.",
    "",
])
def test_asks_you_false(text):
    assert not agent.asks_you(text)


def tx(**kw):
    base = {"last_reply": "", "pending_tool": None, "ask_question": None,
            "ask_started": None, "last_c_ts": NOW - 100}
    base.update(kw)
    return base


def inter(**kw):
    d = {"kind": "interactive", "status": "idle", "statusUpdatedAt": (NOW - 50) * 1000,
         "updatedAt": (NOW - 10) * 1000}
    d.update(kw)
    return d


def test_background_stale():
    s = inter(kind="background", state="running", updatedAt=(NOW - 25 * H) * 1000)
    assert agent.session_state(s, tx(), NOW, 0)["state"] == "stale"


def test_interactive_never_stale_and_question_waits():
    s = inter(updatedAt=(NOW - 25 * H) * 1000)
    r = agent.session_state(s, tx(last_reply="Intro\n\nOk to proceed?"), NOW, 0)
    assert r["state"] == "waiting" and r["question"] == "Ok to proceed?"


def test_pending_ask_waits_even_when_busy():
    r = agent.session_state(inter(status="busy"),
                            tx(pending_tool="AskUserQuestion", ask_question="Which db?",
                               ask_started=NOW - 500), NOW, 0)
    assert r == {"state": "waiting", "question": "Which db?", "idle_since": NOW - 500}


def test_idle_question_beats_running_subagents():
    r = agent.session_state(inter(), tx(last_reply="Want me to retry?"), NOW, 2)
    assert r["state"] == "waiting"


def test_idle_pending_tool_waits_for_approval():
    assert agent.session_state(inter(), tx(pending_tool="Bash"), NOW, 0)["state"] == "waiting"


def test_background_blocked_uses_needs():
    s = {"kind": "background", "state": "blocked", "needs": "approve deploy",
         "updatedAt": (NOW - 5) * 1000}
    r = agent.session_state(s, tx(), NOW, 0)
    assert r["state"] == "waiting" and r["question"] == "approve deploy"


def test_sub_busy_idle():
    assert agent.session_state(inter(), tx(last_reply="done."), NOW, 1)["state"] == "sub"
    assert agent.session_state(inter(status="busy"), tx(), NOW, 0)["state"] == "busy"
    assert agent.session_state(inter(), tx(last_reply="done."), NOW, 0)["state"] == "idle"


def test_question_capped_and_idle_since_fallback():
    r = agent.session_state(inter(), tx(last_reply="x" * 500 + "?", last_c_ts=None), NOW, 0)
    assert len(r["question"]) <= 300
    assert r["idle_since"] == NOW - 50
    assert agent.session_state(inter(), tx(), NOW, 0)["idle_since"] == NOW - 100


def _sub(d, name, finished, age):
    sub = d / "subagents"
    sub.mkdir(exist_ok=True)
    (sub / (name + ".meta.json")).write_text(json.dumps({"finished": finished}))
    j = sub / (name + ".jsonl")
    j.write_text("{}")
    os.utime(j, (NOW - age, NOW - age))


def test_count_subagents(tmp_path):
    assert agent.count_subagents(str(tmp_path / "nope"), NOW) == 0
    _sub(tmp_path, "a", True, 10)
    _sub(tmp_path, "b", False, 11 * 60)
    assert agent.count_subagents(str(tmp_path), NOW) == 0
    _sub(tmp_path, "c", False, 60)
    assert agent.count_subagents(str(tmp_path), NOW) == 1


def test_question_is_paragraph_that_triggered_waiting_code_tail():
    reply = "Shall I apply this patch?\n\n```diff\n-a\n\n+b\n```"
    r = agent.session_state(inter(), tx(last_reply=reply), NOW, 0)
    assert r["state"] == "waiting" and r["question"] == "Shall I apply this patch?"


def test_question_is_paragraph_that_triggered_waiting_url_tail():
    r = agent.session_state(inter(), tx(last_reply="Any questions?\n\nhttps://x.example/docs"),
                            NOW, 0)
    assert r["state"] == "waiting" and r["question"] == "Any questions?"


def test_question_keeps_inline_code():
    r = agent.session_state(inter(), tx(last_reply="Run `make test` now?"), NOW, 0)
    assert r["question"] == "Run `make test` now?"


def test_background_stale_with_iso_updated_at():
    old = "2026-08-20T18:30:22.824Z"
    s = {"kind": "background", "state": "running", "updatedAt": old}
    now = agent._secs(old) + 25 * H
    assert agent.session_state(s, tx(), now, 0)["state"] == "stale"
    s["updatedAt"] = "2026-08-20T18:30:22Z"
    assert agent._secs(s["updatedAt"]) == 1787250622
    assert agent.session_state(s, tx(), agent._secs(old) + H, 0)["state"] == "busy"
