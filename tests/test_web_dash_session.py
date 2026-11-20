"""Web card text when there is no attach command, and project_name's home handling."""
import re

from projdeck import web

NOW = 1_800_000_000


def card_for(**kw):
    s = {"id": "w", "host": "s-host", "project": "/home/u/dev/p", "name": "n", "title": "",
         "state": "waiting", "idle_since": NOW - 600, "question": "", "last_reply": "",
         "last_human": {"ts": 0, "text": ""}, "last_input": "human", "subagents_running": 0,
         "segments": [], "attach": "", "tasks": None, "tasks_owner": None, "errors": []}
    s.update(kw)
    b = {"hosts": [], "summary": {}, "waiting": [s], "busy": [], "idle": [], "ended_today": [],
         "stale": [], "stale_host_sessions": [], "top_todos": [],
         "lanes": {"start": NOW - 4 * 3600, "end": NOW, "rows": [], "more": 0}}
    return web.render_columns(b)


def test_empty_attach_mentions_dash_named_zellij_session():
    html = card_for(attach="")
    assert "不在 zellij / tmux 里" in html
    assert "会话名以 - 开头" in html and "手动接入" in html


def test_attach_command_still_shown_without_the_note():
    html = card_for(attach="zellij attach z-w")
    assert "<code>zellij attach z-w</code>" in html
    assert "会话名以 - 开头" not in html


def test_no_attach_note_goes_through_h(monkeypatch):
    monkeypatch.setattr(web, "NO_ATTACH", "<b>&")
    html = card_for(attach="")
    assert "&lt;b&gt;&amp;" in html and "<b>&" not in html


def test_project_name_home_cases(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    assert web.project_name("/home/u/dev/p") == "dev/p"
    assert web.project_name("/home/u") == "u"          # home itself: last component
    assert web.project_name("/home/ux") == "ux"        # prefix-alike is not under home
    assert web.project_name("/home/ux/dev/p") == "dev/p"  # via the generic /home/<x>/ rule
    assert web.project_name("/home/u/d\udcff/p") == "d\udcff/p"
