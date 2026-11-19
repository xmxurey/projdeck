import pytest

from projdeck import agent
from tests.conftest import user

CWD = "/w/p"


def row(content, **extra):
    d = {"type": "user", "cwd": CWD, "entrypoint": "cli",
         "message": {"role": "user", "content": content}}
    d.update(extra)
    return d


def blocks(text):
    return [{"type": "text", "text": text}]


@pytest.mark.parametrize("d,expected", [
    (row("hello", origin={"kind": "human"}), "human"),
    (row("hello", origin={"kind": "peer"}), "peer"),
    (row("done", origin={"kind": "task-notification"}), "peer"),
    (row("go on", origin={"kind": "auto-continuation"}), "meta"),
    (row("<local-command-caveat>x</local-command-caveat>", isMeta=True), "meta"),
    (row("Stop hook feedback: keep going", isMeta=True), "meta"),
    (row("<command-name>/model</command-name>"), "human"),
    (row("Another Claude session sent a message: hi"), "peer"),
    (row([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]), None),
    ({"type": "assistant", "cwd": CWD,
      "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}, None),
])
def test_input_kind_table(d, expected):
    assert agent.input_kind(d) == expected


def test_text_block_message_is_human():
    assert agent.input_kind(row(blocks("plain words"))) == "human"


def test_compact_summary_is_none():
    assert agent.input_kind(row("summary...", isCompactSummary=True)) is None


def test_unknown_origin_is_meta():
    assert agent.input_kind(row("x", origin={"kind": "something-new"})) == "meta"


def test_ismeta_other_is_meta_but_ismeta_false_is_human():
    assert agent.input_kind(row("skill body", isMeta=True)) == "meta"
    assert agent.input_kind(row("typed", isMeta=False)) == "human"


def test_agent_message_within_300_is_peer():
    assert agent.input_kind(row("a" * 280 + "<agent-message>")) == "peer"
    assert agent.input_kind(row("<agent-message from=x>hi")) == "peer"


def test_agent_message_after_300_is_not_peer():
    assert agent.input_kind(row("a" * 300 + "<agent-message>")) == "human"
    assert agent.input_kind(row("a" * 400 + "<agent-message>")) == "human"


def test_agent_message_in_text_block_form():
    assert agent.input_kind(row(blocks("<agent-message>hi"))) == "peer"


def test_parse_session_only_human_prompts(tmp_path, make_session):
    fp = make_session(tmp_path, CWD, "s1", [
        user(CWD, "Another Claude session sent a message: sub result"),
        row("Stop hook feedback: continue", isMeta=True),
        row("go on", origin={"kind": "auto-continuation"}),
        row("worker done", origin={"kind": "task-notification"}),
        user(CWD, "real first"),
        user(CWD, "<agent-message>from sub"),
        user(CWD, "real second"),
    ], mtime=5000)
    s = agent.parse_session(fp)
    assert s["prompts"] == ["real first", "real second"]
    assert s["recent"] == ["real first", "real second"]
    assert s["title"] == "real first"


def test_title_not_from_peer_when_no_human(tmp_path, make_session):
    fp = make_session(tmp_path, CWD, "s2", [
        user(CWD, "Another Claude session sent a message: x"),
        row("Stop hook feedback: y", isMeta=True),
    ], mtime=5000)
    s = agent.parse_session(fp)
    assert s["prompts"] == [] and s["recent"] == []
    assert "Another Claude" not in s["title"] and "Stop hook" not in s["title"]


def test_custom_title_still_wins(tmp_path, make_session):
    fp = make_session(tmp_path, CWD, "s3", [
        user(CWD, "Another Claude session sent a message: x"),
        user(CWD, "mine"),
        {"type": "custom-title", "customTitle": "Named", "sessionId": "s3"},
    ], mtime=5000)
    assert agent.parse_session(fp)["title"] == "Named"
