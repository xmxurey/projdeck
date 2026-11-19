"""Independent spec tests for T22: web.render(board, "projects") and home-page anchors."""
import re
from html.parser import HTMLParser

from projdeck import web

NOW = 1_800_000_000
EVIL = '<script>alert(1)</script><img src=x onerror=1>'
EVIL2 = '"onerror=1>javascript:alert(1)'
P1 = "/home/someone/dev/deep/proj-x"
P2 = "/home/someone/dev/other"
P3 = "/home/someone/dev/third"
VOID = ("rect", "line", "path", "circle", "polygon", "br", "img", "input", "use", "ellipse", "meta", "link")


class Dom(HTMLParser):
    def __init__(self, s):
        super().__init__(convert_charrefs=True)
        self.nodes, self.stack = [], []
        self.feed(s)

    def handle_starttag(self, tag, attrs):
        n = {"tag": tag, "attrs": dict(attrs), "anc": list(self.stack), "text": ""}
        self.nodes.append(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.nodes.append({"tag": tag, "attrs": dict(attrs), "anc": list(self.stack), "text": ""})

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        for n in self.stack:
            n["text"] += data

    def find(self, tag):
        return [n for n in self.nodes if n["tag"] == tag]


def cls(n):
    return set((n["attrs"].get("class") or "").split())


def tile(project=P1, state="none", todo=0, c14=None, text="", host="s-host", tasks=()):
    return {"host": host, "project": project, "state": state, "todo": todo,
            "commits_14d": c14, "tasks": list(tasks), "text": text}


def sess(sid, state):
    return {"id": sid, "project": P1, "host": "s-host", "name": "n-" + sid, "title": "t-" + sid,
            "state": state, "status": "busy", "started_at": NOW - 7200, "idle_since": None,
            "question": "", "last_reply": "", "last_human": None, "last_input": "human",
            "subagents_running": 0, "segments": [], "attach": "zellij attach z", "tasks": None,
            "tasks_owner": None, "errors": []}


def item(title, done=False):
    return {"title": title, "done": done, "section": None, "text": title}


def bl(project, items, host="s-host", raw=None):
    return {"host": host, "project": project, "open": sum(1 for i in items if not i["done"]),
            "done": sum(1 for i in items if i["done"]), "updated": NOW - 3600, "items": items, "raw": raw}


def board(tiles=(), backlogs=(), waiting=1, busy=1):
    return {"hosts": [{"name": "s-host", "live_at": NOW - 30, "collect_at": NOW - 60,
                       "stale_since": None, "error": None, "source": "local"}],
            "summary": {"waiting": waiting, "busy": busy, "sub": 0, "idle": 0, "open_todos": 3,
                        "todo_projects": 1, "today": {"busy": 0, "sub": 0, "wait": 0},
                        "zellij_exited": [], "zellij_exited_n": 0, "no_backlog": []},
            "waiting": [sess("w%d" % i, "waiting") for i in range(waiting)],
            "busy": [sess("b%d" % i, "busy") for i in range(busy)],
            "idle": [], "ended_today": [], "stale": [], "stale_host_sessions": [],
            "lanes": {"start": NOW - 3 * 3600, "end": NOW, "rows": [], "more": 0},
            "top_todos": [], "backlogs": list(backlogs), "tiles": list(tiles)}


def tile_node(dom, status):
    out = [n for n in dom.nodes if ("tile-" + status) in cls(n)]
    return out


def heat_rects(dom, root):
    return [n for n in dom.nodes if n["tag"] == "rect" and any(a is root for a in n["anc"])]


def page(b, fragment=False):
    return web.render(b, "projects", fragment)


# ---------------------------------------------------------------- tile content

def test_one_tile_per_tile_with_status_class():
    b = board(tiles=[tile(P1, "wait"), tile(P2, "busy"), tile(P3, "none")])
    dom = Dom(page(b))
    for st in ("wait", "busy", "none"):
        assert len(tile_node(dom, st)) == 1, st
    # a tile carries exactly one of the three status classes
    for n in dom.nodes:
        assert len(cls(n) & {"tile-wait", "tile-busy", "tile-none"}) <= 1


def test_tile_has_name_todo_text_and_14_rects():
    c14 = [0] * 13 + [5]
    b = board(tiles=[tile(P1, "wait", todo=7, c14=c14, text="questionable status")])
    dom = Dom(page(b))
    (t,) = tile_node(dom, "wait")
    assert "dev/deep/proj-x" in t["text"]
    assert "/home/someone" not in t["text"]
    assert "☐7" in t["text"] or "☐ 7" in t["text"]
    assert "questionable status" in t["text"]
    assert len(heat_rects(dom, t)) == 14


def test_heat_levels_five_classes_by_threshold():
    vals = [0, 1, 2, 3, 9, 10, 29, 30, 31, 100, 0, 2, 9, 29]
    b = board(tiles=[tile(P1, "none", c14=vals)])
    dom = Dom(page(b))
    (t,) = tile_node(dom, "none")
    rects = heat_rects(dom, t)
    assert len(rects) == 14

    def bucket(v):
        return 0 if v == 0 else 1 if v < 3 else 2 if v < 10 else 3 if v < 30 else 4
    by_bucket = {}
    for v, r in zip(vals, rects):
        by_bucket.setdefault(bucket(v), set()).add(r["attrs"].get("class"))
    assert set(by_bucket) == {0, 1, 2, 3, 4}
    for k, s in by_bucket.items():
        assert len(s) == 1 and None not in s, (k, s)
    allc = [next(iter(s)) for s in by_bucket.values()]
    assert len(set(allc)) == 5


def test_heat_class_independent_of_position_and_tile():
    b = board(tiles=[tile(P1, "none", c14=[0] * 13 + [3]), tile(P2, "busy", c14=[3] + [0] * 13)])
    dom = Dom(page(b))
    (t1,), (t2,) = tile_node(dom, "none"), tile_node(dom, "busy")
    r1, r2 = heat_rects(dom, t1), heat_rects(dom, t2)
    assert r1[-1]["attrs"]["class"] == r2[0]["attrs"]["class"]
    assert r1[0]["attrs"]["class"] == r2[-1]["attrs"]["class"]
    assert r1[0]["attrs"]["class"] != r1[-1]["attrs"]["class"]


def test_commits_none_draws_empty_cells_not_zero_level():
    b = board(tiles=[tile(P1, "none", c14=None), tile(P2, "none", c14=[0] * 14)])
    dom = Dom(page(b))
    ts = tile_node(dom, "none")
    assert len(ts) == 2
    by_name = {("proj-x" in t["text"]): t for t in ts}
    none_rects = heat_rects(dom, by_name[True])
    zero_rects = heat_rects(dom, by_name[False])
    assert len(none_rects) == 14 and len(zero_rects) == 14
    assert none_rects[0]["attrs"].get("class") != zero_rects[0]["attrs"].get("class")
    assert len({r["attrs"].get("class") for r in none_rects}) == 1


def test_git_failure_text_shown_verbatim():
    b = board(tiles=[tile(P1, "none", c14=None, text="git 读取失败")])
    assert "git 读取失败" in page(b)


def test_tile_zero_todo_and_empty_tiles_page():
    assert "<main" in page(board(tiles=[]))
    dom = Dom(page(board(tiles=[tile(P1, "none", todo=0, c14=[0] * 14)])))
    (t,) = tile_node(dom, "none")
    assert "☐0" in t["text"] or "☐ 0" in t["text"]


def test_task_bar_rendered_with_rects_for_tiles_with_tasks():
    b = board(tiles=[tile(P1, "busy", c14=[0] * 14, tasks=["done", "doing", "todo", "stuck"])])
    dom = Dom(page(b))
    (t,) = tile_node(dom, "busy")
    assert len(heat_rects(dom, t)) >= 14 + 4


def test_tile_order_follows_board_order():
    b = board(tiles=[tile(P2, "wait"), tile(P1, "busy"), tile(P3, "none")])
    out = page(b)
    assert out.index("dev/other") < out.index("dev/deep/proj-x") < out.index("dev/third")


# ---------------------------------------------------------------- page shell

def test_main_poll_attribute_and_fragment():
    b = board(tiles=[tile(P1, "wait", c14=[0] * 14)])
    full = page(b)
    dom = Dom(full)
    mains = dom.find("main")
    assert len(mains) == 1 and mains[0]["attrs"].get("data-poll") == "/fragment/projects"
    frag = page(b, True)
    assert "<html" not in frag.lower() and "<head" not in frag.lower() and "<body" not in frag.lower()
    assert "dev/deep/proj-x" in frag
    assert "dev/deep/proj-x" in full


def test_navigation_links_on_both_pages():
    b = board(tiles=[tile(P1, "wait")])
    for out in (web.render(b, "home"), page(b)):
        hrefs = [n["attrs"].get("href") for n in Dom(out).find("a")]
        assert "/" in hrefs and "/projects" in hrefs


def test_home_main_poll_still_home():
    dom = Dom(web.render(board(), "home"))
    assert dom.find("main")[0]["attrs"].get("data-poll") == "/fragment/home"


# ---------------------------------------------------------------- mobile anchors

def test_home_header_count_anchors_and_targets():
    b = board(backlogs=[bl(P1, [item("a"), item("b")])])
    dom = Dom(web.render(b, "home"))
    hrefs = {n["attrs"].get("href") for n in dom.find("a")}
    ids = {n["attrs"].get("id") for n in dom.nodes}
    for a in ("wait", "busy", "todo"):
        assert "#" + a in hrefs, a
        assert a in ids, a


def test_home_structure_hooks_for_mobile_css():
    b = board(backlogs=[bl(P1, [item("a")])])
    dom = Dom(web.render(b, "home"))
    ids = {n["attrs"].get("id") for n in dom.nodes}
    classes = set().union(*[cls(n) for n in dom.nodes])
    assert "timeline" in ids
    assert "col-ended" in classes and "col-top" in classes


def test_col_top_and_ended_not_the_anchor_targets():
    dom = Dom(web.render(board(backlogs=[bl(P1, [item("a")])]), "home"))
    for n in dom.nodes:
        if n["attrs"].get("id") in ("wait", "busy", "todo"):
            assert not (cls(n) & {"col-top", "col-ended"})
            assert n["attrs"].get("id") != "timeline"


# ---------------------------------------------------------------- data-key stability

def _keys(b):
    return [d["attrs"].get("data-key") for d in Dom(web.render(b, "home")).find("details")]


def test_data_key_not_dependent_on_list_order():
    items = [item("t%d" % i) for i in range(8)]
    a, c = bl(P1, items), bl(P2, items)
    k1, k2 = _keys(board(backlogs=[a, c])), _keys(board(backlogs=[c, a]))
    assert k1 and None not in k1 and "" not in k1
    assert sorted(k1) == sorted(k2)
    assert len(set(k1)) == len(k1)


def test_data_key_survives_new_project_inserted_first():
    items = [item("t%d" % i) for i in range(8)]
    a, c, n = bl(P1, items), bl(P2, items), bl(P3, items)
    before = set(_keys(board(backlogs=[a, c])))
    after = set(_keys(board(backlogs=[n, a, c])))
    assert before and before <= after


def test_data_key_distinguishes_same_project_on_two_hosts_and_uses_only_host_project():
    items = [item("t%d" % i) for i in range(8)]
    sh, em = bl(P1, items, host="s-host"), bl(P1, items, host="em-host")
    k = _keys(board(backlogs=[sh, em]))
    assert len(set(k)) == len(k)
    # content changes (titles, counts) do not alter the keys
    items2 = [item("changed%d" % i) for i in range(9)]
    k2 = _keys(board(backlogs=[bl(P1, items2, host="s-host"), bl(P1, items2, host="em-host")]))
    assert set(k) <= set(k2)


# ---------------------------------------------------------------- security

def _assert_safe(out):
    dom = Dom(out)
    assert 'style="' not in out
    assert "<img" not in out.lower()
    assert "<script>alert" not in out
    assert not re.search(r"https?://", out)
    for n in dom.nodes:
        if n["tag"] == "script":
            assert n["attrs"].get("src") and not n["text"].strip()
        for k, v in n["attrs"].items():
            assert not k.startswith("on"), (n["tag"], k)
            if k in ("href", "src"):
                assert not (v or "").strip().lower().startswith("javascript:")


def test_injection_in_projects_page_escaped():
    b = board(tiles=[tile("/home/x/" + EVIL, "wait", todo=1, c14=[1] * 14, text=EVIL + EVIL2,
                          host=EVIL2),
                     tile("/home/x/" + EVIL2, "busy", c14=None, text=EVIL2)],
              backlogs=[bl("/home/x/" + EVIL, [item(EVIL), item(EVIL2)])])
    full, frag = page(b), page(b, True)
    for out in (full, frag):
        _assert_safe(out)
        assert "&lt;script&gt;" in out
    assert 'style="' not in web.render(b, "home")


def test_no_style_attribute_with_all_heat_levels_and_task_bar():
    b = board(tiles=[tile(P1, "busy", todo=2, c14=[0, 1, 3, 10, 30] * 2 + [0] * 4,
                          tasks=["done", "doing", "todo", "stuck"]), tile(P2, "none", c14=None)])
    out = page(b)
    assert 'style="' not in out
    assert not re.search(r"https?://", out)
