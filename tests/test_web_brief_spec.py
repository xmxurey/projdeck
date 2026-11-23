"""Independent spec tests for T09: the web home page folds brief hosts' sessions.

Boards come from the production board.build; pages from web.render(board, "home", now=...).
Expected values come from the task brief and the T08 rulings only."""
import calendar
import copy
import json
import re
import time
from html.parser import HTMLParser

import pytest

from projdeck import board, web

H = 3600
NOW = calendar.timegm((2026, 11, 18, 12, 0, 0))
EVIL = '<script>alert(1)</script><img src=x onerror=1>'
VOID = ("rect", "line", "path", "circle", "polygon", "br", "img", "input", "use", "ellipse",
        "meta", "link", "hr", "stop")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("HOME", "/home/u")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


class Dom(HTMLParser):
    def __init__(self, s):
        super().__init__(convert_charrefs=True)
        self.nodes, self.stack = [], []
        self.feed(s)

    def _add(self, tag, attrs):
        n = {"tag": tag, "attrs": dict(attrs), "anc": list(self.stack), "text": "",
             "id": len(self.nodes)}
        self.nodes.append(n)
        return n

    def handle_starttag(self, tag, attrs):
        n = self._add(tag, attrs)
        if tag not in VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self._add(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        for n in self.stack:
            n["text"] += data

    def cls(self, n):
        return set((n["attrs"].get("class") or "").split())

    def find(self, tag, cls=None, key=None):
        return [n for n in self.nodes if n["tag"] == tag
                and (cls is None or cls in self.cls(n))
                and (key is None or n["attrs"].get("data-key") == key)]

    def inside(self, n, a):
        return any(x["id"] == a["id"] for x in n["anc"])

    def cards(self):
        return [n for n in self.nodes if n["tag"] == "article" and "card" in self.cls(n)]

    def card(self, token):
        got = [c for c in self.cards() if token in c["text"]]
        assert len(got) == 1, (token, len(got))
        return got[0]


# ---------------------------------------------------------------- fixtures

def cfg(brief=True):
    em = {"name": "em-host", "ssh": "em-host"}
    if brief is not None:
        em["brief"] = brief
    return {"hosts": [{"name": "s-host", "local": True}, em]}


def proj(c):
    return "/home/u/proj-" + c


def sess(i, state, project=None, **kw):
    project = project or proj(i.lower())
    d = {"id": i, "project": project, "cwd": project, "latest_cwd": project,
         "kind": "interactive", "live": True, "name": "n-%s" % i, "title": "",
         "started_at": NOW - 3 * H, "status": "busy" if state in ("busy", "sub") else "idle",
         "state": state, "idle_since": NOW - 100, "question": "", "last_reply": "",
         "last_human": {"ts": 0, "text": ""}, "last_input": "human", "subagents_running": 0,
         "segments": [["busy", NOW - 2 * H, NOW - H]], "attach": "", "tasks": None,
         "tasks_owner": None, "errors": [], "state_error": False}
    d.update(kw)
    return d


def live(host, sessions, age):
    return {"schema": 1, "host": host, "collected_at": NOW - age, "synced_at": NOW - age,
            "stale_since": None, "last_error": None, "source": "claude agents",
            "since": NOW - age - 86400, "sessions": list(sessions), "projects": {}, "errors": []}


def cache(host):
    return {"schema": 1, "host": host, "collected_at": NOW - 60, "synced_at": NOW - 60,
            "stale_since": None, "last_error": None, "projects": []}


def base_em():
    """A busy clean, B busy state_error, C idle clean, D waiting."""
    return [sess("A", "busy"), sess("B", "busy", state_error=True, errors=["boom"]),
            sess("C", "idle"), sess("D", "waiting")]


def build(em=None, config=None, em_age=30):
    em = base_em() if em is None else em
    s = [sess("E", "busy")]
    return board.build([cache("s-host"), cache("em-host")],
                       [live("s-host", s, 10), live("em-host", em, em_age)],
                       cfg() if config is None else config, NOW)


def page(b, now=NOW, fragment=False):
    return web.render(b, "home", fragment, now=now)


def fold_of(dom, host="em-host"):
    got = dom.find("details", "brief-fold", "brief-" + host)
    assert len(got) == 1, len(got)
    return got[0]


def summary_of(dom, d):
    got = [n for n in dom.find("summary") if n["anc"] and n["anc"][-1]["id"] == d["id"]]
    assert len(got) == 1
    return " ".join(got[0]["text"].split())


def timeline(html):
    m = re.search(r'<section id="timeline".*?</section>', html, re.S)
    assert m
    return m.group(0)


# ---------------------------------------------------------------- 1. cards

def test_folded_cards_inside_block_others_outside():
    dom = Dom(page(build()))
    fold = fold_of(dom)
    [busy_col] = dom.find("div", "col-busy")
    assert dom.inside(fold, busy_col), "the fold block belongs in the 进行中 column"
    assert dom.inside(dom.card("proj-a"), fold)
    assert dom.inside(dom.card("proj-c"), fold)
    b = dom.card("proj-b")
    assert not dom.inside(b, fold) and "⚠" in b["text"]
    assert not dom.inside(dom.card("proj-e"), fold)
    d = dom.card("proj-d")
    assert "card-wait" in dom.cls(d) and not dom.inside(d, fold)
    assert len(dom.find("details", "brief-fold")) == 1


def test_summary_text_is_the_spec_sentence():
    dom = Dom(page(build()))
    s = summary_of(dom, fold_of(dom))
    assert "em-host · 进行中 1 · 空闲 1（另有单独列出的 进行中 1：出错 1）" in s
    b = build()
    e = [x for x in b["brief"]["hosts"] if x["host"] == "em-host"][0]
    assert board.brief_label(e) in s


def test_unkeyed_session_stays_outside_and_label_says_so():
    em = [sess("A", "busy"), sess("", "busy", project=proj("z"))]
    dom = Dom(page(build(em=em)))
    fold = fold_of(dom)
    assert dom.inside(dom.card("proj-a"), fold)
    assert not dom.inside(dom.card("proj-z"), fold)
    assert "无会话 id 1" in summary_of(dom, fold)


def test_all_errored_host_draws_no_block_but_keeps_header_note():
    em = [sess("B", "busy", state_error=True, errors=["boom"])]
    html = page(build(em=em))
    dom = Dom(html)
    assert not dom.find("details", "brief-fold") and not dom.find("details", "brief-lanes")
    assert "⚠" in dom.card("proj-b")["text"]
    assert "（em-host 1）" in html


# ---------------------------------------------------------------- 2. header

def test_header_notes_and_column_total():
    html = page(build())
    dom = Dom(html)
    notes = dom.find("span", "counter-brief")
    assert [" ".join(n["text"].split()) for n in notes] == ["（em-host 2）", "（em-host 1）"]
    blk = " ".join(notes[0]["anc"][-1]["text"].split())
    # total still includes the brief host: A, B on em-host plus E on s-host
    assert "3 进行中（em-host 2）" in blk or "3进行中（em-host 2）" in blk
    assert "空闲 1（em-host 1）" in blk
    col = re.findall(r'进行中 <span class="count">(\d+)</span>', html)
    assert col and set(col) == {"3"}


def test_no_brief_host_no_header_note():
    dom = Dom(page(build(config=cfg(brief=False))))
    assert not dom.find("span", "counter-brief")


# ---------------------------------------------------------------- 3. timeline

def test_timeline_main_and_brief_lanes_split():
    html = page(build())
    tl = timeline(html)
    inner = re.findall(r'<details class="brief-lanes"[^>]*>.*?</details>', tl, re.S)
    assert len(inner) == 1
    outer = tl.replace(inner[0], "")
    for t in ("proj-a", "proj-c"):
        assert t not in outer and t in inner[0]
    for t in ("proj-b", "proj-d", "proj-e"):
        assert t in outer and t not in inner[0]
    dom = Dom(tl)
    d = dom.find("details", "brief-lanes", "brief-lanes-em-host")
    assert len(d) == 1
    assert summary_of(dom, d[0]) == "em-host 的 2 条泳道"
    assert [n for n in dom.nodes if n["tag"] == "svg" and dom.inside(n, d[0])]


def test_thirteen_folded_lanes_draw_twelve_and_note_one_more():
    em = [sess("f%02d" % i, "busy", project=proj("f%02d" % i), started_at=NOW - 3 * H + i)
          for i in range(13)]
    tl = timeline(page(build(em=em)))
    inner = re.findall(r'<details class="brief-lanes"[^>]*>.*?</details>', tl, re.S)[0]
    assert len(set(re.findall(r"proj-f\d\d", inner))) == 12
    assert "另有 1 个会话" in inner
    assert "另有" not in tl.replace(inner, "")
    assert "13 条泳道" in inner


def test_twelve_folded_lanes_no_more_note():
    em = [sess("f%02d" % i, "busy", project=proj("f%02d" % i)) for i in range(12)]
    tl = timeline(page(build(em=em)))
    inner = re.findall(r'<details class="brief-lanes"[^>]*>.*?</details>', tl, re.S)[0]
    assert len(set(re.findall(r"proj-f\d\d", inner))) == 12 and "另有" not in inner


def test_no_brief_host_no_brief_lanes_block():
    assert "brief-lanes" not in page(build(config=cfg(brief=False)))


# ---------------------------------------------------------------- 4. freshness

def hhmm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def test_aging_marks_block_and_shows_local_clock_time():
    dom = Dom(page(build(em_age=400)))
    fold = fold_of(dom)
    assert "brief-aging" in dom.cls(fold)
    s = summary_of(dom, fold)
    assert "数据 %s" % hhmm(NOW - 400) in s and "前" in s


def test_aging_time_follows_render_now_not_build_now():
    b = build(em_age=30)       # fresh when built
    dom = Dom(page(b, now=NOW + 400))   # 430 s old at request time
    fold = fold_of(dom)
    assert "brief-aging" in dom.cls(fold)
    assert "数据 %s" % hhmm(NOW - 30) in summary_of(dom, fold)
    assert "数据" not in summary_of(Dom(page(b)), fold_of(Dom(page(b))))


def test_fresh_block_is_not_aging():
    dom = Dom(page(build(em_age=100)))
    fold = fold_of(dom)
    assert "brief-aging" not in dom.cls(fold)
    assert "数据 " not in summary_of(dom, fold)


def test_stale_host_has_no_block_and_cards_listed_greyed():
    dom = Dom(page(build(em_age=700)))
    assert not dom.find("details", "brief-fold") and not dom.find("details", "brief-lanes")
    for t in ("proj-a", "proj-c"):
        assert "card-stale-host" in dom.cls(dom.card(t))


# ---------------------------------------------------------------- 5. consistency / escaping

def test_absent_false_and_deleted_brief_render_identically():
    absent = page(build(config=cfg(brief=None)))
    false = page(build(config=cfg(brief=False)))
    b = build(config=cfg(brief=False))
    del b["brief"]
    deleted = page(b)
    assert absent == false == deleted
    assert "brief-fold" not in absent and "counter-brief" not in absent
    assert page(build()) != absent   # control: the feature does show up when switched on


def evil_board(age=30):
    b = build(em=[sess("A", "busy", name=EVIL, project="/home/u/" + EVIL),
                  sess("C", "idle"), sess("B", "busy", state_error=True, name=EVIL,
                                          errors=[EVIL])], em_age=age)
    return b


@pytest.mark.parametrize("age", [30, 400])
def test_external_text_escaped(age):
    html = page(evil_board(age), fragment=True)
    assert "&lt;script&gt;" in html
    for bad in ("<script", "<img", "onerror=1>", 'style="'):
        assert bad not in html


def test_host_name_is_escaped_in_every_place():
    b = evil_board()
    host = '"><script>x</script>'
    b2 = json.loads(json.dumps(b).replace("em-host", host.replace('"', '\\"')))
    html = page(b2, fragment=True)
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    assert 'data-key="brief-%s' % host not in html
