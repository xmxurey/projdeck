"""D7 on the web: header note, folded block in the busy column, folded lanes block."""
import copy
import re
import time

import pytest

from projdeck import board as board_mod
from projdeck import web
from tests.test_web_home import NOW, board, lane, sess

EVIL = "<script>alert(1)</script>"


def host(name, brief, age=10):
    return {"name": name, "local": False, "brief": brief, "live_synced_at": NOW - age,
            "live_unknown": False}


def brief_board(age=10, brief=True, **kw):
    a, c = sess("a"), sess("c", state="idle")
    bad = sess("b", state_error=True, errors=["boom"])
    d = sess("d", state="waiting", question="?")
    b = board(hosts=[host("em-host", brief, age)], busy=[a, bad], idle=[c], waiting=[d],
              summary={"busy": 2, "idle": 1, "waiting": 1})
    for s in b["waiting"] + b["busy"] + b["idle"]:
        s["host"] = "em-host"
    b.update(kw)
    b["brief"] = board_mod.brief_fold(b)
    return b


def col(html, cls):
    return re.search(r'<div class="column %s">.*?(?=<div class="column |</section>)' % cls,
                     html, re.S).group(0)


def fold(html):
    m = re.search(r'<details class="brief-fold[^"]*" data-key="brief-em-host">.*?</details>',
                  html, re.S)
    return m.group(0) if m else ""


def test_folded_cards_are_inside_the_block_and_errored_stays_out():
    html = web.render(brief_board(), "home", fragment=True, now=NOW)
    inside = fold(html)
    assert "n-a" in inside and "n-c" in inside and "n-b" not in inside
    assert "em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1：出错 1）" in inside
    busy = col(html, "col-busy")
    assert inside and inside in busy  # the block sits in the 进行中 column
    assert "n-b" in busy.replace(inside, "") and "⚠" in busy.replace(inside, "")
    assert "n-d" in col(html, "col-wait")


def test_header_notes_and_column_count_match():
    html = web.render(brief_board(), "home", fragment=True, now=NOW)
    head = html[:html.index('<section id="timeline">')]
    assert re.search(r'counter-busy.*?进行中<span class="counter-brief">（em-host 2）</span>', head, re.S)
    assert re.search(r'空闲 1<span class="counter-brief">（em-host 1）</span>', head)
    assert re.search(r'<b>2</b>进行中', head) and re.search(r'进行中 <span class="count">2</span>', html)


def test_no_note_without_brief_hosts():
    html = web.render(brief_board(brief=False), "home", fragment=True, now=NOW)
    assert "counter-brief" not in html and "brief-fold" not in html


def test_lanes_go_to_the_block():
    b = brief_board()
    rows = [lane(session_id="a", host="em-host"), lane(session_id="c", host="em-host")]
    main = [lane(session_id="b", host="em-host")]
    b["lanes"] = {"start": NOW - 4 * 3600, "end": NOW, "rows": main, "more": 0,
                  "brief": [{"host": "em-host", "rows": rows, "n": 2, "more": 0}]}
    html = web.render_timeline(b)
    m = re.search(r'<details class="brief-lanes" data-key="brief-lanes-em-host"><summary>(.*?)</summary>'
                  r'(.*?)</details>', html, re.S)
    assert m.group(1) == "em-host 的 2 条泳道" and m.group(2).count('<g class="lane ') == 2
    assert html.replace(m.group(0), "").count('<g class="lane ') == 1
    assert "另有" not in html


def test_lanes_block_reports_overflow():
    b = brief_board()
    b["lanes"] = {"start": NOW - 4 * 3600, "end": NOW, "rows": [], "more": 0,
                  "brief": [{"host": "em-host", "rows": [lane() for _ in range(12)], "n": 13, "more": 1}]}
    html = web.render_timeline(b)
    assert "em-host 的 13 条泳道" in html and "另有 1 个会话" in html


def test_lanes_block_count_is_the_built_n_not_the_rows_drawn():
    # rows are cut at the lane limit: counting them would say "12 条泳道 · 另有 1 个"
    b = brief_board()
    blk = {"host": "em-host", "rows": [lane() for _ in range(12)], "more": 1}
    b["lanes"] = {"start": NOW - 4 * 3600, "end": NOW, "rows": [], "more": 0, "brief": [blk]}
    with pytest.raises(KeyError):
        web.render_timeline(b)


def test_freshness_aging_fresh_stale():
    clock = time.strftime("%H:%M", time.localtime(NOW - 400))
    aging = fold(web.render(brief_board(age=400), "home", fragment=True, now=NOW))
    assert "brief-aging" in aging and "数据 %s" % clock in aging
    assert "brief-aging" not in fold(web.render(brief_board(age=100), "home", fragment=True, now=NOW))
    b = brief_board(age=700)
    b["stale_host_sessions"] = [dict(s, age=700) for s in b["busy"] + b["idle"]]
    b["busy"], b["idle"] = [], []
    b["brief"] = board_mod.brief_fold(b)
    html = web.render(b, "home", fragment=True, now=NOW)
    assert "brief-fold" not in html and html.count("card-stale-host") == 3


def test_three_ways_of_no_brief_render_the_same():
    plain = board(hosts=[host("em-host", False)], busy=[sess("a")], idle=[sess("c", state="idle")])
    unset = copy.deepcopy(plain)
    for h_ in unset["hosts"]:
        del h_["brief"]
    nokey = copy.deepcopy(plain)
    plain["brief"] = board_mod.brief_fold(plain)
    unset["brief"] = board_mod.brief_fold(unset)
    pages = [web.render(x, "home", now=NOW) for x in (plain, unset, nokey)]
    assert pages[0] == pages[1] == pages[2]


def test_external_text_is_escaped():
    b = brief_board()
    b["busy"][0]["name"] = EVIL
    b["busy"][0]["project"] = "/home/u/" + EVIL
    html = web.render(b, "home", fragment=True, now=NOW)
    assert EVIL not in html and "&lt;script&gt;" in html
    # the host name itself reaches the summary, data-key and lanes block: rename it everywhere
    b["hosts"][0]["name"] = EVIL
    for s in b["waiting"] + b["busy"] + b["idle"]:
        s["host"] = EVIL
    b["brief"] = board_mod.brief_fold(b)
    b["lanes"] = {"start": NOW - 4 * 3600, "end": NOW, "rows": [], "more": 0,
                  "brief": [{"host": EVIL, "rows": [lane(host=EVIL)], "n": 1, "more": 0}]}
    html = web.render(b, "home", fragment=True, now=NOW)
    assert board_mod.brief_label(b["brief"]["hosts"][0]).startswith(EVIL)
    assert EVIL not in html
    esc = "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert 'data-key="brief-%s"' % esc in html and 'data-key="brief-lanes-%s"' % esc in html
    assert "<summary>%s · 进行中 1" % esc in html and "<summary>%s 的 1 条泳道" % esc in html


def test_folded_verdict_comes_from_board_module(monkeypatch):
    b = brief_board()
    monkeypatch.setattr(web.board, "folded", lambda bd, s: False)
    assert "brief-fold" not in web.render(b, "home", fragment=True, now=NOW)
