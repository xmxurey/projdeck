"""Independent spec tests for T11: the single fold verdict (G16) and the agent -> board chain.

Expected values come from the T11 brief and the T08/T10 rulings only. Boards come from the
production board.build; pages from web.render, terminal text from ui.project_lines,
ui.brief_preview_text and ui.header_lines.
"""
import re
import time
from html import unescape

import pytest

from projdeck import agent, board, ui, web
from tests.test_agent_live_spec import (  # noqa: F401  (fixtures and helpers reused as-is)
    agent_entry, asst, by_id, env, human, simple_recs, tuse,
)

NOW = 1_800_000_000
HOME = "/home/u"
IDS = ("A", "B", "C", "D")


# ------------------------------------------------------------ hand-built board

def sess(i, state):
    p = "%s/q-%s" % (HOME, i.lower())
    return {"id": i, "project": p, "cwd": p, "latest_cwd": p, "kind": "interactive",
            "live": True, "name": "nm-" + i, "title": "tt-" + i, "state": state,
            "status": "busy" if state == "busy" else "idle", "started_at": NOW - 3600,
            "idle_since": NOW - 600, "question": "", "last_reply": "",
            "last_human": {"ts": 0, "text": ""}, "last_input": "human", "subagents_running": 0,
            "segments": [["busy", NOW - 1800, NOW - 900]], "attach": "", "tasks": None,
            "tasks_owner": None, "errors": [], "state_error": False}


def live(host, sessions, age=10):
    return {"schema": 1, "host": host, "collected_at": NOW - age, "synced_at": NOW - age,
            "stale_since": None, "last_error": None, "source": "claude agents",
            "sessions": sessions, "projects": {}, "errors": []}


def cfg():
    return {"hosts": [{"name": "s-host", "local": True},
                      {"name": "em-host", "ssh": "em-host", "brief": True},
                      {"name": "x-host", "ssh": "x-host", "brief": True}]}


def built():
    """em-host: A busy, B busy, C idle (all brief); x-host: D busy."""
    lives = [live("s-host", []),
             live("em-host", [sess("A", "busy"), sess("B", "busy"), sess("C", "idle")]),
             live("x-host", [sess("D", "busy")])]
    return board.build([], lives, cfg(), NOW)


def with_keys(keys):
    bd = built()
    bd["brief"]["keys"] = [list(k) for k in keys]
    return bd


# ------------------------------------------------------------ outlet readers

def busy_column(html):
    return re.search(r'<div class="column col-busy">.*?(?=<div class="column |</section>)',
                     html, re.S).group(0)


def fold_blocks(html):
    return re.findall(r'<details class="brief-fold[^"]*" data-key="brief-[^"]*">.*?</details>',
                      html, re.S)


def term_fields(bd):
    return [ln.split(ui.SEP, 2) for ln in ui.project_lines([], NOW, bd)]


def outlets(bd, ids=IDS):
    col = busy_column(web.render(bd, "home", fragment=True, now=NOW))
    blocks = fold_blocks(col)
    folded_html = "".join(blocks)
    rest = col
    for b in blocks:
        rest = rest.replace(b, "")
    listed = {ui.from_field(f[1]) for f in term_fields(bd) if f[1] != ui.BRIEF_FIELD}
    prev = "".join(ui.strip_ansi(ui.brief_preview_text(bd, [], h, NOW)) for h in ("em-host", "x-host"))
    path = {i: sess(i, "busy")["project"] for i in ids}
    return {
        "web folded": {i for i in ids if "nm-" + i in folded_html},
        "web listed": {i for i in ids if "nm-" + i in rest},
        "term folded": {i for i in ids if path[i] not in listed},
        "term listed": {i for i in ids if path[i] in listed},
        "preview": {i for i in ids if "nm-" + i in prev},
    }


# ------------------------------------------------------------ AC1

def test_only_b_in_keys_folds_only_b_everywhere():
    v = outlets(with_keys([("em-host", "B")]))
    assert v["web folded"] == v["term folded"] == v["preview"] == {"B"}, v
    assert v["web listed"] == v["term listed"] == {"A", "C", "D"}, v


def test_as_built_all_four_are_folded_everywhere():
    v = outlets(built())
    assert v["web folded"] == v["term folded"] == v["preview"] == set(IDS), v
    assert v["web listed"] == v["term listed"] == set(), v


def test_empty_keys_fold_nothing_everywhere():
    v = outlets(with_keys([]))
    assert v["web folded"] == v["term folded"] == v["preview"] == set(), v
    assert v["web listed"] == v["term listed"] == set(IDS), v


def test_swapping_keys_moves_the_fold_with_them():
    v = outlets(with_keys([("em-host", "A"), ("x-host", "D")]))
    assert v["web folded"] == v["term folded"] == v["preview"] == {"A", "D"}, v
    assert v["web listed"] == v["term listed"] == {"B", "C"}, v


def test_key_naming_a_different_host_does_not_fold_the_same_id():
    # (host, id) is the key: A on x-host does not exist, so em-host's A stays listed
    v = outlets(with_keys([("x-host", "A")]))
    assert v["web folded"] == v["term folded"] == v["preview"] == set(), v


# ------------------------------------------------------------ AC2: every outlet asks board.folded

def test_every_outlet_asks_board_folded(monkeypatch):
    bd = with_keys([("em-host", "B")])
    calls = []

    def never(b, s):
        calls.append(s.get("id"))
        return False

    monkeypatch.setattr(board, "folded", never)
    v = outlets(bd)
    assert v["web folded"] == v["term folded"] == v["preview"] == set(), v
    assert v["web listed"] == v["term listed"] == set(IDS), v
    assert calls, "the stub was never consulted"
    with pytest.raises(AssertionError):  # the AC1 test itself, run again, goes red under the stub
        test_only_b_in_keys_folds_only_b_everywhere()


@pytest.mark.parametrize("outlet", ["web", "rows", "preview"])
def test_each_outlet_alone_consults_the_stub(monkeypatch, outlet):
    bd = with_keys([("em-host", "B")])
    seen = []
    monkeypatch.setattr(board, "folded", lambda b, s: seen.append(1) or False)
    if outlet == "web":
        web.render(bd, "home", fragment=True, now=NOW)
    elif outlet == "rows":
        ui.project_lines([], NOW, bd)
    else:
        ui.brief_preview_text(bd, [], "em-host", NOW)
    assert seen, outlet + " did not go through board.folded"


def test_stub_forcing_true_folds_what_keys_do_not(monkeypatch):
    # the verdict follows the stub in the other direction too: not a cached copy of the keys
    bd = with_keys([])
    monkeypatch.setattr(board, "folded", lambda b, s: s.get("id") == "C")
    v = outlets(bd)
    assert v["web folded"] == v["term folded"] == v["preview"] == {"C"}, v


# ------------------------------------------------------------ AC4: same words

def web_summaries(bd):
    col = busy_column(web.render(bd, "home", fragment=True, now=NOW))
    return [unescape(t) for t in re.findall(
        r'<details class="brief-fold[^"]*" data-key="brief-[^"]*"><summary>(.*?)</summary>', col, re.S)]


def web_note(bd):
    html = web.render(bd, "home", fragment=True, now=NOW)
    m = re.search(r'class="counter counter-busy".*?<span class="counter-brief">(.*?)</span>', html, re.S)
    return m and unescape(m.group(1))


def term_summaries(bd):
    out = [ui.strip_ansi(f[2]) for f in term_fields(bd) if f[1] == ui.BRIEF_FIELD]
    assert all(t.startswith("◉ ") for t in out), out
    return [t[2:] for t in out]


def term_note(bd):
    m = re.search(r"进行中 \d+(（[^）]*）)", ui.strip_ansi(ui.header_lines(bd, NOW)[0]))
    return m and m.group(1)


def test_web_summary_text_equals_terminal_summary_line_and_label():
    bd = built()
    want = ["em-host · 进行中 2 · 空闲 1", "x-host · 进行中 1"]
    assert web_summaries(bd) == term_summaries(bd) == want


def test_summary_text_is_what_board_brief_label_says(monkeypatch):
    bd = built()
    labels = [board.brief_label(e) for e in bd["brief"]["hosts"]]
    assert len(labels) == 2 and web_summaries(bd) == term_summaries(bd) == labels
    monkeypatch.setattr(board, "brief_label", lambda e: "label-of-" + e["host"])
    assert web_summaries(bd) == term_summaries(bd) == ["label-of-em-host", "label-of-x-host"]


def test_header_note_same_on_both_and_spelled_per_spec():
    bd = built()
    assert web_note(bd) == term_note(bd) == "（em-host 2 · x-host 1）"


def test_same_words_follow_a_board_with_an_errored_session():
    # a realistic board (counts and keys built together), not a hand-edited one
    b = sess("B", "busy")
    b["state_error"] = True
    lives = [live("s-host", []), live("em-host", [sess("A", "busy"), b, sess("C", "idle")]),
             live("x-host", [sess("D", "busy")])]
    bd = board.build([], lives, cfg(), NOW)
    assert web_summaries(bd) == term_summaries(bd)
    assert web_note(bd) == term_note(bd)
    assert web_summaries(bd)[0].startswith("em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1")
    assert "出错 1" in web_summaries(bd)[0]


# ------------------------------------------------------------ AC3: real agent path -> board

def chain_board(env_, monkeypatch, bad, warn):
    n = env_.now
    out = env_.live([bad["repo"], warn["repo"]])
    ss = by_id(out)
    assert set(ss) >= {"bad", "warn"}
    now = int(n)
    lv = dict(out, host="em-host", collected_at=now, synced_at=now, stale_since=None,
              last_error=None)
    cf = {"hosts": [{"name": "s-host", "local": True},
                    {"name": "em-host", "ssh": "em-host", "brief": True}]}
    return board.build([], [live("s-host", []), lv], cf, now), now, ss


@pytest.fixture
def chain(env):
    n = env.now
    r_bad, r_warn = env.repo("p-bad"), env.repo("p-warn")
    env.session(r_bad, "bad", simple_recs(env, r_bad), mtime=n - 100)
    recs = [human(n - 300, "plan", r_warn),
            tuse(n - 290, "t1", "TaskUpdate", {"taskId": "99", "status": "completed"}, r_warn),
            asst(n - 280, "working", r_warn)]
    env.session(r_warn, "warn", recs, mtime=n - 60)
    env.agents([agent_entry("bad", r_bad, n - 500, status="busy", name="nm-bad"),
                agent_entry("warn", r_warn, n - 400, status="busy", name="nm-warn")])
    orig = agent.transcript_events

    def boom(path, *a, **k):
        if "bad" in path.rsplit("/", 1)[-1]:
            raise PermissionError("denied")
        return orig(path, *a, **k)

    env.mp.setattr(agent, "transcript_events", boom)
    return {"bad": {"repo": r_bad}, "warn": {"repo": r_warn}}


def test_chain_agent_flags_feed_the_board(env, chain, monkeypatch):
    bd, now, ss = chain_board(env, monkeypatch, chain["bad"], chain["warn"])
    assert ss["bad"]["state_error"] is True
    assert ss["warn"]["state_error"] is False and ss["warn"]["errors"]
    keys = {tuple(k) for k in bd["brief"]["keys"]}
    assert ("em-host", "warn") in keys
    assert ("em-host", "bad") not in keys


def test_chain_web_shows_errored_alone_and_folds_the_warned(env, chain, monkeypatch):
    bd, now, ss = chain_board(env, monkeypatch, chain["bad"], chain["warn"])
    col = busy_column(web.render(bd, "home", fragment=True, now=now))
    blocks = fold_blocks(col)
    assert len(blocks) == 1
    assert "nm-warn" in blocks[0] and "nm-bad" not in blocks[0]
    rest = col.replace(blocks[0], "")
    assert "nm-bad" in rest and "nm-warn" not in rest
    summ = web_summaries_at(bd, now)
    assert len(summ) == 1 and "告警 1" in summ[0], summ
    assert "出错 1" in summ[0], summ


def web_summaries_at(bd, now):
    col = busy_column(web.render(bd, "home", fragment=True, now=now))
    return [unescape(t) for t in re.findall(
        r'<details class="brief-fold[^"]*" data-key="brief-[^"]*"><summary>(.*?)</summary>', col, re.S)]


def test_chain_terminal_flags_errored_row_and_summarises_the_warned(env, chain, monkeypatch):
    bd, now, ss = chain_board(env, monkeypatch, chain["bad"], chain["warn"])
    fields = [ln.split(ui.SEP, 2) for ln in ui.project_lines([], now, bd)]
    rows = {ui.from_field(f[1]): ui.strip_ansi(f[2]) for f in fields if f[1] != ui.BRIEF_FIELD}
    assert chain["bad"]["repo"] in rows
    assert "⚠ 状态读取失败" in rows[chain["bad"]["repo"]]
    assert chain["warn"]["repo"] not in rows
    assert all("状态读取失败" not in d for p, d in rows.items() if p != chain["bad"]["repo"])
    summ = [ui.strip_ansi(f[2]) for f in fields if f[1] == ui.BRIEF_FIELD]
    assert len(summ) == 1 and "告警 1" in summ[0], summ


def test_chain_web_and_terminal_say_the_same_summary(env, chain, monkeypatch):
    bd, now, ss = chain_board(env, monkeypatch, chain["bad"], chain["warn"])
    fields = [ln.split(ui.SEP, 2) for ln in ui.project_lines([], now, bd)]
    term = [ui.strip_ansi(f[2])[2:] for f in fields if f[1] == ui.BRIEF_FIELD]
    assert web_summaries_at(bd, now) == term


def test_chain_without_any_failure_both_fold(env, monkeypatch):
    # good data stays quiet: no state_error, nothing shown separately, no 出错 in the label
    n = env.now
    r1, r2 = env.repo("p-one"), env.repo("p-two")
    env.session(r1, "one", simple_recs(env, r1), mtime=n - 100)
    env.session(r2, "two", simple_recs(env, r2), mtime=n - 100)
    env.agents([agent_entry("one", r1, n - 500, status="busy", name="nm-one"),
                agent_entry("two", r2, n - 400, status="busy", name="nm-two")])
    out = env.live([r1, r2])
    now = int(n)
    lv = dict(out, host="em-host", collected_at=now, synced_at=now, stale_since=None, last_error=None)
    cf = {"hosts": [{"name": "s-host", "local": True},
                    {"name": "em-host", "ssh": "em-host", "brief": True}]}
    bd = board.build([], [live("s-host", []), lv], cf, now)
    assert {tuple(k) for k in bd["brief"]["keys"]} == {("em-host", "one"), ("em-host", "two")}
    summ = web_summaries_at(bd, now)
    assert len(summ) == 1 and "出错" not in summ[0] and "告警" not in summ[0]
    assert "⚠ 状态读取失败" not in "".join(ui.project_lines([], now, bd))
