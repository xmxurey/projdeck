"""Spec-derived tests for agent.asks_you / count_subagents / session_state (T05)."""
import json
import os

import pytest

from projdeck import agent

NOW = 1800000000
H = 3600


def tx(**state):
    st = {"pending_tool": None, "ask_started": None, "ask_question": None,
          "last_reply": "", "last_c_ts": None}
    st.update(state)
    return st


def sess(**kw):
    s = {"kind": "interactive", "status": "idle", "mtime": NOW - 60,
         "updatedAt": (NOW - 60) * 1000, "statusUpdatedAt": NOW - 60,
         "startedAt": NOW - 5000}
    s.update(kw)
    return s


def st(session, t, sub=0, now=NOW):
    return agent.session_state(session, t, now, sub)


# ---- asks_you ---------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Shall we go on？",
    "Intro.\n\nDo you want A or B? **Tell me.**",
    "Intro.\n\nShould I proceed (yes?)",
    "Intro.\n\nReally?\n\n",
    "Do you want this?  ",
])
def test_asks_you_true(text):
    assert agent.asks_you(text) is True


@pytest.mark.parametrize("text", [
    "See this:\n\nhttps://x.example/?a=1",
    "Result:\n\n```\nwhat?\n```",
    "Result:\n\n```py\nx = a ? b : c\n```\n",
    "Use `a?b` here",
    "Is this ok?\n\nNo, it is done.",
    "",
    "All done.",
])
def test_asks_you_false(text):
    assert agent.asks_you(text) is False


def test_asks_you_orphan_fence_ignored():
    # truncated tail: lone closing fence at the start, question mark outside code
    assert agent.asks_you("```\n\nWant me to continue?") is True
    # lone fence with no question outside it
    assert agent.asks_you("```\n\nAll done.") is False


def test_asks_you_balanced_fence_still_stripped():
    assert agent.asks_you("```\nq?\n```\n\nDone.") is False


# ---- state table --------------------------------------------------------------

def test_background_stale():
    s = sess(kind="background", status=None, state="running",
             mtime=NOW - 25 * H, updatedAt=(NOW - 25 * H) * 1000)
    assert st(s, tx(last_reply="ok."))["state"] == "stale"


def test_background_recent_updatedAt_saves_from_stale():
    s = sess(kind="background", state="idle", mtime=NOW - 25 * H,
             updatedAt=(NOW - 60) * 1000)
    assert st(s, tx(last_reply="ok."))["state"] != "stale"


def test_background_23h_not_stale():
    s = sess(kind="background", state="idle", mtime=NOW - 23 * H,
             updatedAt=(NOW - 23 * H) * 1000)
    assert st(s, tx(last_reply="ok."))["state"] == "idle"


def test_interactive_never_stale_and_asks_you_waiting():
    s = sess(mtime=NOW - 25 * H, updatedAt=(NOW - 25 * H) * 1000,
             statusUpdatedAt=NOW - 25 * H)
    r = st(s, tx(last_reply="Intro\n\nShall I continue?"))
    assert r["state"] == "waiting"
    assert "continue?" in r["question"]


def test_interactive_25h_not_asking_is_idle_not_stale():
    s = sess(mtime=NOW - 25 * H, updatedAt=(NOW - 25 * H) * 1000)
    assert st(s, tx(last_reply="done."))["state"] == "idle"


def test_ask_user_question_waiting_even_when_busy():
    s = sess(status="busy")
    r = st(s, tx(pending_tool="AskUserQuestion", ask_question="Which db?",
                 ask_started=NOW - 63 * H, last_reply="whatever"))
    assert r["state"] == "waiting"
    assert r["question"] == "Which db?"
    assert r["idle_since"] == NOW - 63 * H


def test_ask_user_question_beats_subagents():
    r = st(sess(status="busy"), tx(pending_tool="AskUserQuestion",
           ask_question="Q?", ask_started=NOW - 500), sub=3)
    assert r["state"] == "waiting" and r["question"] == "Q?"


def test_idle_asks_you_with_subagents_is_waiting():
    r = st(sess(), tx(last_reply="Pick one?"), sub=2)
    assert r["state"] == "waiting"


def test_idle_pending_other_tool_waiting():
    r = st(sess(), tx(pending_tool="Bash", last_reply="Running it."))
    assert r["state"] == "waiting"


def test_busy_with_pending_other_tool_is_busy():
    r = st(sess(status="busy"), tx(pending_tool="Bash", last_reply="Running."))
    assert r["state"] == "busy"


def test_busy_asks_you_text_is_busy():
    r = st(sess(status="busy"), tx(last_reply="Maybe?"))
    assert r["state"] == "busy"


@pytest.mark.parametrize("bstate", ["blocked", "needs_input", "waiting"])
def test_background_blocked_waiting_question_from_needs(bstate):
    s = sess(kind="background", status=None, state=bstate,
             needs="Need API key", detail="d")
    r = st(s, tx(last_reply="done."))
    assert r["state"] == "waiting"
    assert r["question"] == "Need API key"


def test_background_question_falls_back_to_detail():
    s = sess(kind="background", status=None, state="blocked", detail="stuck on X")
    assert st(s, tx(last_reply="done."))["question"] == "stuck on X"


def test_background_running_is_busy():
    s = sess(kind="background", status=None, state="running")
    assert st(s, tx(last_reply="working"))["state"] == "busy"


def test_sub_when_subagents_running_and_not_asking():
    r = st(sess(), tx(last_reply="Delegated."), sub=1)
    assert r["state"] == "sub"


def test_busy():
    assert st(sess(status="busy"), tx(last_reply="x"))["state"] == "busy"


def test_busy_with_subagents_is_sub():
    assert st(sess(status="busy"), tx(last_reply="x"), sub=2)["state"] == "sub"


def test_idle_rest():
    assert st(sess(), tx(last_reply="All done."))["state"] == "idle"


def test_missing_status_is_idle():
    s = sess()
    del s["status"]
    assert st(s, tx(last_reply="All done."))["state"] == "idle"


# ---- question / idle_since --------------------------------------------------------

def test_question_falls_back_to_last_paragraph():
    r = st(sess(), tx(last_reply="First para.\n\nSecond: do you agree?"))
    assert r["state"] == "waiting"
    assert r["question"].strip() == "Second: do you agree?"


def test_ask_question_wins_over_needs_and_reply():
    s = sess(kind="background", status=None, state="blocked", needs="N")
    r = st(s, tx(pending_tool="AskUserQuestion", ask_question="AQ?",
                 ask_started=NOW - 10, last_reply="R?"))
    assert r["question"] == "AQ?"


def test_question_truncated_300():
    r = st(sess(), tx(pending_tool="AskUserQuestion", ask_question="q" * 1000,
                      ask_started=NOW - 10))
    assert len(r["question"]) <= 300
    r = st(sess(), tx(last_reply="a\n\n" + "w" * 900 + "?"))
    assert len(r["question"]) <= 300
    s = sess(kind="background", status=None, state="blocked", needs="n" * 900)
    assert len(st(s, tx())["question"]) <= 300


def test_idle_since_uses_last_c_ts():
    r = st(sess(statusUpdatedAt=NOW - 9), tx(last_reply="done.", last_c_ts=NOW - 700))
    assert r["idle_since"] == NOW - 700


def test_idle_since_ask_started_over_last_c_ts():
    r = st(sess(), tx(pending_tool="AskUserQuestion", ask_question="q",
                      ask_started=NOW - 900, last_c_ts=NOW - 100))
    assert r["idle_since"] == NOW - 900


def test_idle_since_falls_back_to_status_updated_at():
    r = st(sess(statusUpdatedAt=NOW - 321), tx(last_reply="done.", last_c_ts=None))
    assert r["idle_since"] == NOW - 321


def test_idle_since_fallback_ms_normalised_to_seconds():
    r = st(sess(statusUpdatedAt=(NOW - 321) * 1000), tx(last_reply="done."))
    assert r["idle_since"] == NOW - 321


def test_result_keys():
    r = st(sess(), tx(last_reply="done."))
    assert {"state", "question", "idle_since"} <= set(r)
    assert r["state"] in ("stale", "waiting", "sub", "busy", "idle")


def test_missing_fields_tolerated():
    r = agent.session_state({"kind": "interactive"}, {}, NOW, 0)
    assert r["state"] == "idle"


# ---- count_subagents -----------------------------------------------------------------

def mk_sub(d, name, finished=None, age=60):
    sd = os.path.join(str(d), "subagents")
    os.makedirs(sd, exist_ok=True)
    meta = {} if finished is None else {"finished": finished}
    with open(os.path.join(sd, name + ".meta.json"), "w") as f:
        json.dump(meta, f)
    jp = os.path.join(sd, name + ".jsonl")
    with open(jp, "w") as f:
        f.write("{}\n")
    os.utime(jp, (NOW - age, NOW - age))


def test_count_missing_dir(tmp_path):
    assert agent.count_subagents(str(tmp_path / "nope"), NOW) == 0


def test_count_empty_dir(tmp_path):
    assert agent.count_subagents(str(tmp_path), NOW) == 0


def test_count_finished_not_counted(tmp_path):
    mk_sub(tmp_path, "a", finished=True, age=30)
    assert agent.count_subagents(str(tmp_path), NOW) == 0


def test_count_stale_jsonl_not_counted(tmp_path):
    mk_sub(tmp_path, "a", finished=False, age=11 * 60)
    assert agent.count_subagents(str(tmp_path), NOW) == 0


def test_count_recent_counted(tmp_path):
    mk_sub(tmp_path, "a", finished=False, age=60)
    assert agent.count_subagents(str(tmp_path), NOW) == 1


def test_count_missing_finished_key_counts(tmp_path):
    mk_sub(tmp_path, "a", finished=None, age=60)
    assert agent.count_subagents(str(tmp_path), NOW) == 1


def test_count_mixed(tmp_path):
    mk_sub(tmp_path, "a", finished=False, age=10)
    mk_sub(tmp_path, "b", finished=False, age=120)
    mk_sub(tmp_path, "c", finished=True, age=10)
    mk_sub(tmp_path, "d", finished=False, age=3600)
    assert agent.count_subagents(str(tmp_path), NOW) == 2


def test_count_meta_without_jsonl_not_counted(tmp_path):
    sd = tmp_path / "subagents"
    sd.mkdir()
    (sd / "x.meta.json").write_text("{}")
    assert agent.count_subagents(str(tmp_path), NOW) == 0


# ---- review round 1: shared last-paragraph + ISO timestamps ---------------------------

@pytest.mark.parametrize("tail", [
    "```\nprint('x')\n```",
    "https://x.example/?a=1",
])
def test_question_before_fence_or_url_is_waiting(tail):
    r = st(sess(), tx(last_reply="Intro.\n\nAny questions?\n\n" + tail))
    assert r["state"] == "waiting"
    assert r["question"].strip() == "Any questions?"


def test_asks_you_question_then_url_true():
    assert agent.asks_you("Any questions?\n\nhttps://x.example/?a=1") is True


def test_question_keeps_inline_code():
    r = st(sess(), tx(last_reply="Intro.\n\nShall I run `make test`?"))
    assert r["state"] == "waiting"
    assert "`make test`" in r["question"]


def test_background_iso_updated_at_over_24h_is_stale():
    s = {"kind": "background", "state": "running", "updatedAt": "2026-08-20T18:30:22.824Z"}
    # NOW (2027-01) is months after the ISO timestamp
    assert st(s, tx(last_reply="ok."))["state"] == "stale"


def test_background_iso_updated_at_recent_not_stale():
    import time
    iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - 600))
    s = {"kind": "background", "state": "running", "updatedAt": iso}
    assert st(s, tx(last_reply="ok."))["state"] == "busy"


def test_background_iso_with_offset_over_24h_is_stale():
    s = {"kind": "background", "state": "blocked", "needs": "x",
         "updatedAt": "2026-08-20T20:30:22+02:00"}
    assert st(s, tx())["state"] == "stale"
