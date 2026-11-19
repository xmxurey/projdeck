import pytest

from projdeck import agent
from tests.conftest import user


def u(text, **extra):
    d = {"type": "user", "message": {"role": "user", "content": text}}
    d.update(extra)
    return d


CASES = [
    (u("hi", origin={"kind": "human"}), "human"),
    (u("hi", origin={"kind": "peer"}), "peer"),
    (u("hi", origin={"kind": "task-notification"}), "peer"),
    (u("hi", origin={"kind": "auto-continuation"}), "meta"),
    (u("<local-command-caveat>x", isMeta=True), "meta"),
    (u("Stop hook feedback: keep going", isMeta=True), "meta"),
    (u("<command-name>/model</command-name>"), "human"),
    (u("Another Claude session sent a message: hello"), "peer"),
    ({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}, None),
    ({"type": "assistant", "message": {"role": "assistant", "content": "hi"}}, None),
]


@pytest.mark.parametrize("d,expected", CASES)
def test_input_kind(d, expected):
    assert agent.input_kind(d) == expected


def test_agent_message_marker_only_in_first_300_chars():
    assert agent.input_kind(u("x" * 100 + "<agent-message from=a>")) == "peer"
    assert agent.input_kind(u("x" * 300 + "<agent-message from=a>")) == "human"


def test_compact_summary_is_not_input():
    assert agent.input_kind(u("summary", isCompactSummary=True)) is None


def test_text_block_content_is_classified():
    d = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}}
    assert agent.input_kind(d) == "human"


def test_parse_session_keeps_only_human_prompts(tmp_path, make_session):
    cwd = "/w/p"
    fp = make_session(tmp_path, cwd, "s1", [
        user(cwd, "Another Claude session sent a message: report"),
        u("Stop hook feedback: continue", isMeta=True, cwd=cwd),
        u("done", origin={"kind": "task-notification"}, cwd=cwd),
        user(cwd, "real one"),
        user(cwd, "real two"),
        user(cwd, "y" * 10 + "<agent-message>"),
    ], mtime=5000)
    s = agent.parse_session(fp)
    assert s["prompts"] == ["real one", "real two"]
    assert s["recent"] == ["real one", "real two"]
    assert s["title"] == "real one"


def test_title_skips_peer_message(tmp_path, make_session):
    cwd = "/w/p"
    fp = make_session(tmp_path, cwd, "s2", [
        user(cwd, "Another Claude session sent a message: report"),
        user(cwd, "my question"),
    ])
    assert agent.parse_session(fp)["title"] == "my question"
