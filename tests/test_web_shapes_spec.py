"""Independent spec tests for T02: class-based SVG colours, wait-now hatch overlay, stuck-cell shape."""
import re
from html.parser import HTMLParser

import pytest

from projdeck import web

NOW = 1_800_000_000
HOME = "/home/u"
VOID = ("rect", "line", "path", "circle", "polygon", "br", "img", "input", "use", "ellipse", "meta", "link")


@pytest.fixture(autouse=True)
def fixed_home(monkeypatch):
    monkeypatch.setenv("HOME", HOME)


class Dom(HTMLParser):
    def __init__(self, s):
        super().__init__(convert_charrefs=True)
        self.nodes, self.stack = [], []
        self.feed(s)

    def _add(self, tag, attrs):
        n = {"tag": tag, "attrs": dict(attrs), "anc": list(self.stack), "i": len(self.nodes)}
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


def cl(n):
    return set((n["attrs"].get("class") or "").split())


def sess(sid, state="busy", **kw):
    s = {"id": sid, "project": HOME + "/dev/projdeck", "host": "s-host", "name": "n-" + sid,
         "title": "t-" + sid, "state": state, "status": "busy", "started_at": NOW - 7200,
         "idle_since": None, "question": "", "last_reply": "", "last_human": None,
         "last_input": "human", "subagents_running": 0, "segments": [], "attach": "zellij attach z",
         "tasks": None, "tasks_owner": None, "errors": []}
    s.update(kw)
    return s


def tasks(statuses, current=None):
    items = [{"id": "T%02d" % (i + 1), "title": "task %d" % (i + 1), "status": st, "note": ""}
             for i, st in enumerate(statuses)]
    return {"source": "run", "label": "run", "path": "x.md", "checkout": "/x",
            "current": current, "items": items, "merges": []}


def lane(sid="a", segs=None, **kw):
    segs = segs if segs is not None else [
        ["busy", NOW - 3600, NOW - 2400], ["sub", NOW - 2400, NOW - 1800],
        ["wait", NOW - 1800, NOW - 1200], ["wait_now", NOW - 1200, NOW]]
    r = {"host": "s-host", "project": HOME + "/dev/projdeck", "session_id": sid, "state": "busy",
         "segments": segs, "commits": [NOW - 900], "merges": [["T07", NOW - 300]],
         "busy_s": 1800, "wait_s": 1200}
    r.update(kw)
    return r


def mkboard(busy=(), rows=(), tiles=()):
    return {"hosts": [], "summary": {}, "waiting": [], "busy": list(busy), "idle": [],
            "ended_today": [], "stale": [], "stale_host_sessions": [],
            "lanes": {"start": NOW - 4 * 3600, "end": NOW, "rows": list(rows), "more": 0},
            "top_todos": [], "backlogs": [], "tiles": list(tiles)}


def tile(project=HOME + "/dev/app", c14=None, tk=()):
    return {"host": "s-host", "project": project, "state": "busy", "todo": 1,
            "commits_14d": c14, "tasks": list(tk), "text": "t"}


def full_board():
    s = sess("b1", tasks=tasks(["done", "doing", "stuck", "todo"], current="T02"))
    return mkboard(busy=[s], rows=[lane()],
                   tiles=[tile(c14=[0, 1, 5, 12, 40] + [0] * 9, tk=["done", "doing", "stuck", "todo"])])


def pages():
    b = full_board()
    return {"home": web.render(b, "home"), "projects": web.render(b, "projects")}


@pytest.mark.parametrize("which", ["home", "projects"])
def test_no_hardcoded_fill_hex(which):
    out = pages()[which]
    assert 'fill="#' not in out
    assert "fill='#" not in out


def test_old_task_fill_values_gone():
    old = getattr(web, "TASK_FILL", None)
    if not old:
        return  # constant removed: nothing left to leak
    vals = old.values() if isinstance(old, dict) else old
    out = pages()["home"] + pages()["projects"]
    for v in vals:
        assert str(v).lower() not in out.lower()


def test_home_has_all_four_segments_and_marks():
    out = pages()["home"]
    for k in ("seg-busy", "seg-sub", "seg-wait", "seg-wait-now"):
        assert k in out
    dom = Dom(out)
    assert any("mark-commit" in cl(n) for n in dom.nodes)
    assert any("mark-merge" in cl(n) for n in dom.nodes)


GEOM = ("x", "y", "width", "height")


def wait_now_rects(dom):
    """Timeline segments (seg-wait-now) plus the legend swatch for 'still idle waiting' (wait-now)."""
    return [n for n in dom.nodes if n["tag"] == "rect" and
            ("seg-wait-now" in cl(n) or ("wait-now" in cl(n) and any("legend" in cl(a) for a in n["anc"])))]


def test_every_wait_now_rect_followed_by_matching_hatch():
    dom = Dom(pages()["home"])
    wn = wait_now_rects(dom)
    assert len(wn) >= 2, "one timeline segment plus one legend swatch expected"
    for n in wn:
        nxt = None
        for m in dom.nodes[n["i"] + 1:]:
            if m["tag"] == "title":
                continue
            nxt = m
            break
        assert nxt is not None and nxt["tag"] == "rect" and "seg-hatch" in cl(nxt)
        assert nxt["attrs"].get("fill") == "url(#hatch-wait-now)"
        assert nxt["attrs"].get("pointer-events") == "none"
        for k in GEOM:
            assert nxt["attrs"].get(k) == n["attrs"].get(k), k


def test_hatch_count_equals_wait_now_count():
    dom = Dom(pages()["home"])
    wn = wait_now_rects(dom)
    hs = [n for n in dom.nodes if n["tag"] == "rect" and "seg-hatch" in cl(n)]
    assert len(hs) == len(wn)


def test_two_wait_now_segments_two_hatches():
    segs = [["busy", NOW - 3600, NOW - 3000], ["wait_now", NOW - 3000, NOW - 2000],
            ["busy", NOW - 2000, NOW - 1500], ["wait_now", NOW - 1500, NOW]]
    out = web.render_timeline(mkboard(rows=[lane(segs=segs)]))
    dom = Dom(out)
    wn = wait_now_rects(dom)
    hs = [n for n in dom.nodes if n["tag"] == "rect" and "seg-hatch" in cl(n)]
    assert len(wn) == 3 and len(hs) == 3  # two segments + legend swatch


def test_no_wait_now_segment_only_legend_hatch():
    segs = [["busy", NOW - 3600, NOW - 600]]
    dom = Dom(web.render_timeline(mkboard(rows=[lane(segs=segs)])))
    wn = wait_now_rects(dom)
    hs = [n for n in dom.nodes if n["tag"] == "rect" and "seg-hatch" in cl(n)]
    assert len(wn) == 1 and len(hs) == 1  # legend only


def test_exactly_one_pattern_definition_with_svg_defs():
    out = web.render_timeline(mkboard(rows=[lane(), lane("b")]))
    assert out.count('id="hatch-wait-now"') == 1
    dom = Dom(out)
    pats = [n for n in dom.nodes if n["tag"] == "pattern"]
    assert len(pats) == 1 and pats[0]["attrs"].get("id") == "hatch-wait-now"
    defs = [n for n in dom.nodes if n["tag"] == "svg" and "svg-defs" in cl(n)]
    assert len(defs) == 1
    assert any(a is defs[0] for a in pats[0]["anc"])
    # defs svg appears at the start of the section, before any lane rect
    first_rect = next(n for n in dom.nodes if n["tag"] == "rect")
    assert defs[0]["i"] < first_rect["i"]


def test_hatch_defined_once_on_full_home_page():
    out = pages()["home"]
    assert out.count('id="hatch-wait-now"') == 1


def test_empty_timeline_still_valid():
    out = web.render_timeline(mkboard())
    # the legend's wait-now swatch still fills with url(#hatch-wait-now): the pattern must be there
    assert 'url(#hatch-wait-now)' in out and out.count('id="hatch-wait-now"') == 1
    assert 'fill="#' not in out


def task_rects(dom, root_filter=lambda n: True):
    return [n for n in dom.nodes if n["tag"] == "rect" and "task-cell" in cl(n) and root_filter(n)]


def test_card_task_bar_geometry_and_classes():
    s = sess("b1", tasks=tasks(["done", "doing", "stuck", "todo", "done"], current="T02"))
    dom = Dom(web.render_columns(mkboard(busy=[s])))
    rs = task_rects(dom)
    assert len(rs) == 5
    kinds = []
    for n in rs:
        k = [c for c in cl(n) if c.startswith("task-") and c != "task-cell"]
        assert len(k) == 1 and k[0] in ("task-done", "task-doing", "task-stuck", "task-todo")
        kinds.append(k[0])
        if k[0] == "task-stuck":
            assert n["attrs"]["y"] == "0" and n["attrs"]["height"] == "12"
        else:
            assert n["attrs"]["y"] == "2" and n["attrs"]["height"] == "8"
    assert kinds.count("task-stuck") == 1
    svg = rs[0]["anc"][-1]
    assert svg["tag"] == "svg" and svg["attrs"].get("viewbox") == "0 0 300 12"


def test_card_without_stuck_has_no_tall_cell():
    s = sess("b1", tasks=tasks(["done", "doing", "todo"], current="T02"))
    dom = Dom(web.render_columns(mkboard(busy=[s])))
    rs = task_rects(dom)
    assert rs and all(n["attrs"]["y"] == "2" and n["attrs"]["height"] == "8" for n in rs)


def test_card_all_stuck():
    s = sess("b1", tasks=tasks(["stuck", "stuck"], current="T01"))
    rs = task_rects(Dom(web.render_columns(mkboard(busy=[s]))))
    assert len(rs) == 2 and all(n["attrs"]["y"] == "0" and n["attrs"]["height"] == "12" for n in rs)


def test_tiles_task_bar_same_geometry():
    b = mkboard(tiles=[tile(tk=["done", "stuck", "todo", "doing"])])
    dom = Dom(web.render(b, "projects", True))
    rs = task_rects(dom)
    assert len(rs) == 4
    assert [n["attrs"]["y"] for n in rs if "task-stuck" in cl(n)] == ["0"]
    assert [n["attrs"]["height"] for n in rs if "task-stuck" in cl(n)] == ["12"]
    for n in rs:
        if "task-stuck" not in cl(n):
            assert (n["attrs"]["y"], n["attrs"]["height"]) == ("2", "8")
    assert rs[0]["anc"][-1]["attrs"].get("viewbox") == "0 0 300 12"


def test_heat_classes_none_zero_levels():
    html = web.render(mkboard(tiles=[tile(c14=[0, 1, 2, 3, 9, 10, 29, 30, 99, 0, 0, 0, 0, 5])]),
                      "projects", True)
    dom = Dom(re.search(r"<article.*?</article>", html, re.S).group(0))
    got = [[c for c in cl(n) if c.startswith("heat-")][0] for n in dom.nodes
           if n["tag"] == "rect" and any(c.startswith("heat-") for c in cl(n))]
    assert got == ["heat-0", "heat-1", "heat-1", "heat-2", "heat-2", "heat-3", "heat-3", "heat-4",
                   "heat-4", "heat-0", "heat-0", "heat-0", "heat-0", "heat-2"]


def test_heat_none_is_empty():
    html = web.render(mkboard(tiles=[tile(c14=None)]), "projects", True)
    out = re.search(r"<article.*?</article>", html, re.S).group(0)
    assert out.count("heat-empty") == 14 and "heat-0" not in out
    assert 'fill="#' not in html
