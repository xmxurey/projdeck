"""F8: one concept, one implementation (top mark, active tier, zellij scope, session title,
G16 brief fold verdict)."""
import ast
import json
import pathlib
import re
from html import unescape

import pytest

from projdeck import agent, board, cli, report, sync, ui, web
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


# ---- G16: the fold verdict is board.folded alone; web cards, terminal rows and the
# terminal summary preview all follow board["brief"]["keys"] through it

BRIEF_HOME = "/home/u"
BRIEF_IDS = ("A", "B", "C", "X")  # sessions on brief hosts; E is on the plain s-host


def brief_sess(sid, state):
    path = "%s/p-%s" % (BRIEF_HOME, sid.lower())
    return {"id": sid, "project": path, "cwd": path, "kind": "interactive", "live": True,
            "name": "n-" + sid, "title": "t-" + sid, "state": state, "started_at": NOW - 3600,
            "idle_since": NOW - 600, "question": "", "last_reply": "",
            "segments": [["busy", NOW - 1800, NOW - 900]], "attach": "", "tasks": None,
            "tasks_owner": None, "errors": [], "state_error": False}


def brief_live(host, sessions):
    return {"schema": 1, "host": host, "collected_at": NOW - 10, "synced_at": NOW - 10,
            "stale_since": None, "last_error": None, "source": "claude agents",
            "sessions": sessions, "projects": {}, "errors": []}


def brief_board():
    """em-host (brief): busy A, B and idle C; x-host (brief): busy X; s-host: busy E."""
    cfg = {"hosts": [{"name": "s-host", "local": True},
                     {"name": "em-host", "ssh": "em-host", "brief": True},
                     {"name": "x-host", "ssh": "x-host", "brief": True}]}
    lives = [brief_live("s-host", [brief_sess("E", "busy")]),
             brief_live("em-host", [brief_sess("A", "busy"), brief_sess("B", "busy"),
                                    brief_sess("C", "idle")]),
             brief_live("x-host", [brief_sess("X", "busy")])]
    return board.build([], lives, cfg, NOW)


def only_b_folded():
    bd = brief_board()
    assert sorted(k[1] for k in bd["brief"]["keys"]) == ["A", "B", "C", "X"]  # as built
    bd["brief"]["keys"] = [["em-host", "B"]]
    return bd


def _busy_column(html):
    return re.search(r'<div class="column col-busy">.*?(?=<div class="column |</section>)',
                     html, re.S).group(0)


def _fold_blocks(html):
    return re.findall(r'<details class="brief-fold[^"]*" data-key="brief-[^"]*">.*?</details>',
                      html, re.S)


def _term_fields(bd):
    return [ln.split(ui.SEP, 2) for ln in ui.project_lines([], NOW, bd)]


def outlet_view(bd):
    """Per outlet, the brief-host sessions it shows folded and the ones it lists one by one."""
    col = _busy_column(web.render(bd, "home", fragment=True, now=NOW))
    blocks = "".join(_fold_blocks(col))
    plain = col
    for blk in _fold_blocks(col):
        plain = plain.replace(blk, "")
    listed_paths = {ui.from_field(f[1]) for f in _term_fields(bd) if f[1] != ui.BRIEF_FIELD}
    preview = "".join(ui.strip_ansi(ui.brief_preview_text(bd, [], host, NOW))
                      for host in ("em-host", "x-host"))
    path = {sid: brief_sess(sid, "busy")["project"] for sid in BRIEF_IDS}
    return {
        "web": {sid for sid in BRIEF_IDS if "n-" + sid in blocks},
        "web listed": {sid for sid in BRIEF_IDS if "n-" + sid in plain},
        "rows": {sid for sid in BRIEF_IDS if path[sid] not in listed_paths},
        "rows listed": {sid for sid in BRIEF_IDS if path[sid] in listed_paths},
        "preview": {sid for sid in BRIEF_IDS if "n-" + sid in preview},
    }


def check_only_b_folded(bd):
    v = outlet_view(bd)
    assert v["web"] == {"B"} and v["rows"] == {"B"} and v["preview"] == {"B"}, v
    assert v["web listed"] == {"A", "C", "X"} and v["rows listed"] == {"A", "C", "X"}, v


def summary_texts(bd):
    """(web summary texts, terminal summary-line texts, web 进行中 note, terminal header note)."""
    html = web.render(bd, "home", fragment=True, now=NOW)
    web_sum = [unescape(t) for t in re.findall(
        r'<details class="brief-fold[^"]*" data-key="brief-[^"]*"><summary>(.*?)</summary>',
        _busy_column(html), re.S)]
    term_sum = [ui.strip_ansi(f[2]) for f in _term_fields(bd) if f[1] == ui.BRIEF_FIELD]
    assert all(t.startswith("◉ ") for t in term_sum), term_sum
    web_note = re.search(r'class="counter counter-busy".*?<span class="counter-brief">(.*?)</span>',
                         html, re.S)
    term_note = re.search(r"进行中 \d+(（[^）]*）)", ui.strip_ansi(ui.header_lines(bd, NOW)[0]))
    return (web_sum, [t[2:] for t in term_sum], web_note and unescape(web_note.group(1)),
            term_note and term_note.group(1))


def check_same_words(bd):
    web_sum, term_sum, web_note, term_note = summary_texts(bd)
    assert web_sum == term_sum == ["em-host · 进行中 2 · 空闲 1", "x-host · 进行中 1"], (
        web_sum, term_sum)
    assert web_note == term_note == "（em-host 2 · x-host 1）", (web_note, term_note)


def test_g16_web_rows_and_preview_fold_exactly_the_keys_board_holds():
    check_only_b_folded(only_b_folded())


def test_g16_web_and_terminal_say_the_same_summary_and_header_note():
    check_same_words(brief_board())


def test_g16_every_outlet_asks_board_folded(monkeypatch):
    bd, plain = only_b_folded(), brief_board()
    monkeypatch.setattr(board, "folded", lambda bd, s: False)
    v = outlet_view(bd)
    assert v["web"] == v["rows"] == v["preview"] == set(), v
    assert v["web listed"] == v["rows listed"] == set(BRIEF_IDS), v
    with pytest.raises(AssertionError):
        check_only_b_folded(bd)
    with pytest.raises(AssertionError):
        check_same_words(plain)


# ---- D7: whether a brief host gets its summary, and the data age it shows, are asked of the
# board by web and terminal alike (board.brief_shown, board.brief_host_age)

def _web_summary_hosts(bd):
    html = web.render(bd, "home", fragment=True, now=NOW)
    return re.findall(r'<details class="brief-fold[^"]*" data-key="brief-([^"]*)">',
                      _busy_column(html))


def _term_summary(bd):
    return {f[0]: f[2] for f in _term_fields(bd) if f[1] == ui.BRIEF_FIELD}


def test_web_and_terminal_draw_the_summaries_board_brief_shown_allows(monkeypatch):
    bd = brief_board()
    assert _web_summary_hosts(bd) == ["em-host", "x-host"] and set(_term_summary(bd)) == {
        "em-host", "x-host"}
    monkeypatch.setattr(board, "brief_shown", lambda b, host: host == "x-host")
    assert _web_summary_hosts(bd) == ["x-host"]
    assert set(_term_summary(bd)) == {"x-host"}


def test_web_terminal_and_preview_show_the_age_board_brief_host_age_gives(monkeypatch):
    bd = brief_board()
    old = {"at": NOW - 400, "age": 400}
    monkeypatch.setattr(board, "brief_host_age",
                        lambda b, host, now: old if host == "em-host" else None)
    html = _busy_column(web.render(bd, "home", fragment=True, now=NOW))
    assert re.search(r'class="brief-fold brief-aging" data-key="brief-em-host"', html)
    assert re.search(r'class="brief-fold" data-key="brief-x-host"', html)
    term = _term_summary(bd)
    assert term["em-host"].startswith(ui.YELLOW) and "数据" in ui.strip_ansi(term["em-host"])
    assert ui.YELLOW not in term["x-host"]
    assert "⚠ live 数据" in ui.strip_ansi(ui.brief_preview_text(bd, [], "em-host", NOW))
    assert "⚠ live 数据" not in ui.strip_ansi(ui.brief_preview_text(bd, [], "x-host", NOW))


def test_a_brief_host_missing_from_board_hosts_fails_loudly_everywhere():
    bd = brief_board()
    bd["hosts"] = [h for h in bd["hosts"] if h["name"] != "em-host"]
    with pytest.raises(LookupError):
        web.render(bd, "home", fragment=True, now=NOW)
    with pytest.raises(LookupError):
        ui.project_lines([], NOW, bd)
    with pytest.raises(LookupError):
        ui.brief_preview_text(bd, [], "em-host", NOW)


BRIEF_FNS = {"folded", "session_error", "brief_label", "brief_note", "brief_age",
             "brief_shown", "brief_host_age"}


def names_imported_from_board(source):
    """The D7 board functions a module imports by name (and so a test replacing the board
    module's attribute would not reach)."""
    return sorted(a.name for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom)
                  and (n.module or "").split(".")[-1] == "board" for a in n.names
                  if a.name in BRIEF_FNS or a.name == "*")


def test_g16_outlets_ask_the_board_module_not_imported_names():
    root = pathlib.Path(board.__file__).parent
    for fn in ("web.py", "ui.py", "cli.py"):
        assert names_imported_from_board((root / fn).read_text(encoding="utf-8")) == [], fn


@pytest.mark.parametrize("src,bad", [
    ("from projdeck.board import brief_shown\n", ["brief_shown"]),
    ("from .board import folded, brief_host_age as age\n", ["brief_host_age", "folded"]),
    ("from projdeck.board import *\n", ["*"]),
    ("from projdeck import board\nfrom projdeck.board import build\n", []),
])
def test_g16_import_check_flags_names_imported_from_board(src, bad):
    assert names_imported_from_board(src) == bad
