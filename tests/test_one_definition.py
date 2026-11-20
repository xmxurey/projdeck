"""F8: one concept, one implementation (top mark, active tier, zellij scope, session title)."""
import json

import pytest

from projdeck import agent, board, cli, report, sync, web
from tests.test_agent_transcript import asst, human, line, text
from tests.test_ui_cli import _open

NOW = 1_800_000_000
DAY = 86400


def item(title, done=False, text=None):
    return {"title": title, "done": done, "section": None, "text": text or title}


def proj(path, **kw):
    p = {"path": path, "exists": True, "flag": False, "sessions": [], "last_mtime": 0,
         "git": None, "errors": []}
    p.update(kw)
    return p


def cache(projects):
    return {"schema": 1, "host": "s-host", "collected_at": NOW, "synced_at": NOW, "projects": projects}


# ---- the 最优先 mark: judged by the board, drawn by the web page

def test_board_marks_top_items_and_web_draws_what_the_board_says():
    p = proj("/h/a", backlog={"items": [item("最优先：x"), item("y", text="note 最优先"), item("z")],
                              "open": 3, "done": 0, "mtime": NOW})
    b = board.build([cache([p])], [], {"hosts": [{"name": "s-host", "local": True}]}, NOW)
    assert [i["top"] for i in b["backlogs"][0]["items"]] == [True, True, False]
    assert "bl-flag" in web._bl_item({"title": "plain", "done": False, "top": True})
    assert "bl-flag" not in web._bl_item({"title": "最优先 but not marked", "done": False})


# ---- tiles follow model.tier

def tiles(projects, active_days=14):
    cfg = {"hosts": [{"name": "s-host", "local": True}],
           "tiers_days": {"active": active_days, "stale": 90}}
    return {t["project"] for t in board.build([cache(projects)], [], cfg, NOW)["tiles"]}


def test_tiles_use_the_configured_active_tier():
    p = proj("/h/a", last_mtime=NOW - 5 * DAY)
    assert tiles([p]) == {"/h/a"}
    assert tiles([p], active_days=3) == set()  # not active under the user's tiers_days


def test_deleted_projects_get_no_tile():
    gone = proj("/h/gone", exists=False, last_mtime=NOW - DAY, flag=True,
                backlog={"items": [item("x")], "open": 1, "done": 0, "mtime": NOW})
    assert tiles([gone]) == set()


# ---- cli asks config.zellij_in_scope

def test_enter_follows_the_shared_zellij_scope(monkeypatch):
    monkeypatch.setattr(cli.config, "zellij_in_scope", lambda r: False)
    out, calls = _open(monkeypatch, [{"name": "research", "claude": 2}])
    assert calls["launch"] == ["/home/u/quant"] and calls["open"] == []


# ---- transcript_events and parse_session take the title the same way

def test_title_from_a_first_prompt_without_timestamp_matches_parse_session(tmp_path):
    first = json.dumps({"type": "user", "cwd": "/p", "entrypoint": "cli",
                        "message": {"role": "user", "content": "first prompt, no timestamp"}},
                       separators=(",", ":"))
    fp = tmp_path / "s.jsonl"
    fp.write_text("\n".join([first, human("second", NOW), asst([text("ok")], NOW + 1)]) + "\n")
    tx = agent.transcript_events(str(fp), NOW)
    assert tx["state"]["title"] == agent.parse_session(str(fp))["title"]
    assert tx["state"]["title"] == "first prompt, no timestamp"


def test_custom_and_ai_titles_rank_the_same_in_both(tmp_path):
    rows = [human("hello", NOW), line({"type": "ai-title", "aiTitle": "AI"}, NOW + 1),
            line({"type": "custom-title", "customTitle": "Mine"}, NOW + 2)]
    fp = tmp_path / "s.jsonl"
    fp.write_text("\n".join(rows) + "\n")
    assert agent.transcript_events(str(fp), NOW)["state"]["title"] == "Mine"
    assert agent.parse_session(str(fp))["title"] == "Mine"
    assert agent.session_title(None, "AI", "x") == "AI"
    assert agent.session_title(None, None, None) == "(untitled)"


@pytest.mark.parametrize("mod", [board, report, cli, sync], ids=lambda m: m.__name__)
def test_only_config_exposes_zellij_in_scope(mod):
    # a stub aimed at <mod>.zellij_in_scope must fail loudly (AttributeError), not sit idle;
    # every caller goes through the config module attribute
    assert not hasattr(mod, "zellij_in_scope")
