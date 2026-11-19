import re

from projdeck import web

EVIL = '"><script>alert(1)</script>'


def tile(**kw):
    t = {"host": "s-host", "project": "/home/u/dev/app", "state": "none", "todo": 3,
         "commits_14d": [0, 1, 2, 3, 9, 10, 29, 30, 99, 0, 0, 0, 0, 5], "tasks": [], "text": "hello"}
    t.update(kw)
    return t


def board(tiles, **kw):
    b = {"tiles": tiles, "lanes": {"start": 0, "end": 1000, "rows": []}}
    b.update(kw)
    return b


def one_tile(**kw):
    html = web.render(board([tile(**kw)]), "projects", True)
    return re.search(r"<article.*?</article>", html, re.S).group(0)


def test_tile_has_name_todo_state_and_14_cells_with_levels():
    art = one_tile()
    assert "dev/app" in art and "☐3" in art and "没有会话在跑" in art
    classes = re.findall(r'<rect class="heat (heat-\d)"', art)
    assert classes == ["heat-0", "heat-1", "heat-1", "heat-2", "heat-2", "heat-3", "heat-3", "heat-4",
                       "heat-4", "heat-0", "heat-0", "heat-0", "heat-0", "heat-2"]


def test_heat_levels_boundaries():
    got = [web._heat_level(n) for n in (0, 1, 2, 3, 9, 10, 29, 30, 99)]
    assert got == [0, 1, 1, 2, 2, 3, 3, 4, 4]


def test_top_edge_state_class():
    for st in ("wait", "busy", "none"):
        assert 'class="tile tile-%s"' % st in web.render(board([tile(state=st)]), "projects", True)


def test_none_commits_draws_empty_cells_not_zeros():
    art = one_tile(commits_14d=None, text="git 读取失败")
    assert art.count("heat-empty") == 14 and "heat-0" not in art
    assert "git 读取失败" in art


def test_injection_escaped_and_no_inline_style():
    b = board([tile(project=EVIL, text=EVIL, tasks=["todo"])])
    html = web.render(b, "projects")
    assert "<script>alert" not in html.replace('<script src="/static/poll.js">', "")
    assert 'style="' not in html


def test_page_and_fragment_shape():
    page = web.render(board([tile()]), "projects")
    assert 'data-poll="/fragment/projects"' in page
    assert 'href="/"' in page and 'href="/projects"' in page
    frag = web.render(board([tile()]), "projects", True)
    assert "<html" not in frag and "data-poll" not in frag


def test_home_anchors_and_classes():
    html = web.render(board([]), "home")
    for a in ("wait", "busy", "todo"):
        assert 'href="#%s"' % a in html and 'id="%s"' % a in html
    assert "col-top" in html and 'href="/projects"' in html


def test_mobile_css_hides_all_but_columns():
    css = open(web.__file__.replace("web.py", "web/app.css")).read()
    block = css[css.index("@media (max-width: 700px)"):]
    # F9: the selectors and what the rule does with them: hide (not merely mention) them
    selectors, _, decls = block.split("}")[0].partition("{")[2].partition("{")
    for sel in ("#timeline", ".col-ended", ".col-top"):
        assert sel in [x.strip() for x in selectors.split(",")]
    assert [d.strip() for d in decls.split(";") if d.strip()] == ["display: none"]


def test_backlog_data_key_ignores_list_position():
    def entry(p):
        return {"host": "s-host", "project": p, "open": 7, "done": 0, "updated": None,
                "items": [{"title": "t%d" % i, "done": False} for i in range(7)]}
    keys = lambda bl: sorted(re.findall(r'data-key="([^"]*)"', web.render_backlog(board([], backlogs=bl))))
    assert keys([entry("/a"), entry("/b")]) == keys([entry("/b"), entry("/a")])
