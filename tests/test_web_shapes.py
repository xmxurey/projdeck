import re

from projdeck import web

NOW = 1_800_000_000
OLD_TASK_COLORS = ("#2F7D5B", "#2F5D8A", "#D5D9E0", "#C0392B")


def _board(tasks_items=()):
    row = {"host": "s-host", "project": "/home/u/dev/app", "session_id": "a", "state": "busy",
           "segments": [["busy", NOW - 3600, NOW - 1800], ["sub", NOW - 1800, NOW - 1200],
                        ["wait", NOW - 1200, NOW - 600], ["wait_now", NOW - 600, NOW]],
           "commits": [NOW - 900], "merges": [["T07", NOW - 300]], "busy_s": 1, "wait_s": 1}
    sess = {"id": "a", "host": "s-host", "project": "/home/u/dev/app", "name": "n", "title": "",
            "state": "busy", "idle_since": NOW - 600, "question": "", "last_reply": "",
            "last_human": {"ts": 0, "text": ""}, "last_input": "human", "subagents_running": 0,
            "segments": [], "attach": "", "errors": [], "tasks_owner": None,
            "tasks": {"label": "x", "path": "p", "current": "T01", "merges": [], "items": list(tasks_items)}}
    return {"hosts": [], "summary": {}, "waiting": [], "busy": [sess], "idle": [], "ended_today": [],
            "stale": [], "stale_host_sessions": [], "top_todos": [], "tiles": [],
            "lanes": {"start": NOW - 4 * 3600, "end": NOW, "rows": [row], "more": 0}}


def _items():
    return [{"id": "T0%d" % i, "title": "t", "note": "", "status": st}
            for i, st in enumerate(("done", "doing", "stuck", "todo"), 1)]


def _pages():
    b = _board(_items())
    b["tiles"] = [{"host": "s-host", "project": "/home/u/dev/app", "state": "none", "todo": 1,
                   "commits_14d": [None, 0, 1], "tasks": ["done", "stuck", "todo"], "text": "x"}]
    return web.render_timeline(b) + web.render_columns(b, NOW), web.render(b, "projects", True)


def test_no_inline_fill_colors_or_old_task_colors():
    for html in _pages():
        assert 'fill="#' not in html
        for c in OLD_TASK_COLORS:  # the now-line keeps its neutral stroke, so look at fills only
            assert 'fill="%s"' % c not in html and 'fill="%s"' % c.lower() not in html


def test_each_wait_now_has_matching_hatch_and_one_pattern():
    html = _pages()[0]
    segs = re.findall(r'<rect class="seg seg-wait-now [^>]*?x="([^"]+)" y="([^"]+)" width="([^"]+)" '
                      r'height="([^"]+)"[^>]*>.*?</rect>\s*(<rect[^>]*>)', html)
    assert len(segs) == 1
    x, y, w, hh, nxt = segs[0]
    assert 'class="seg-hatch"' in nxt and 'fill="url(#hatch-wait-now)"' in nxt
    assert 'pointer-events="none"' in nxt
    for v in (x, y, w, hh):
        assert ('"%s"' % v) in nxt
    assert html.count('id="hatch-wait-now"') == 1
    assert '<line class="hatch-line"' in html
    assert not re.search(r'<line class="hatch-line"[^>]*(stroke|opacity)', html)
    assert html.startswith('<section id="timeline">\n<svg class="svg-defs"')
    # legend swatch for wait-now also carries the overlay
    assert re.search(r'<rect class="wait-now"[^>]*/><rect class="seg-hatch" fill="url\(#hatch-wait-now\)"', html)
    assert html.count('class="seg-hatch"') == 2


def test_marks_use_classes():
    html = _pages()[0]
    assert '<polygon class="mark mark-commit" points' in html
    assert '<polygon class="mark mark-merge" points' in html


def test_task_bar_geometry_stuck_sticks_out():
    for html in _pages():
        bars = re.findall(r'<svg class="task-bar" viewBox="0 0 300 12".*?</svg>', html, re.S)
        assert bars
        for bar in bars:
            for cls, y, ht in re.findall(r'<rect class="task-cell task-(\w+)" x="[^"]+" y="(\d+)" '
                                         r'width="[^"]+" height="(\d+)"', bar):
                assert (y, ht) == (("0", "12") if cls == "stuck" else ("2", "8"))
        assert any('task-stuck" x' in b for b in bars)


def test_heat_cells_none_zero_and_levels():
    html = _pages()[1]
    assert re.findall(r'<rect class="heat (heat-\S+)"', html)[:3] == ["heat-empty", "heat-0", "heat-1"]
