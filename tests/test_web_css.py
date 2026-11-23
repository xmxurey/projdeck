"""app.css must win over base.css and carry the D6 palette."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "projdeck"
APP = (ROOT / "web" / "app.css").read_text()
BASE = (ROOT / "vendor" / "webkit" / "base.css").read_text()


def var(name):
    for css in (APP, BASE):  # app.css loads after base.css, so it wins
        m = re.search(r"--%s:\s*(#[0-9A-Fa-f]+)" % re.escape(name), css)
        if m:
            return m.group(1)
    raise AssertionError(name)


def fill(sel):
    m = re.findall(r"(?m)^%s\s*\{([^}]*)\}" % re.escape(sel), APP)
    assert m, sel
    body = m[-1]
    assert "fill-opacity" not in body
    v = re.search(r"fill:\s*([^;]+);", body).group(1).strip()
    mv = re.fullmatch(r"var\(--([\w-]+)\)", v)
    return var(mv.group(1)) if mv else v


def test_palette_variables():
    want = {"busy": "#2a78d6", "sub": "#1baf7a", "wait": "#eb6834", "done": "#008300",
            "stuck": "#e34948", "wait-now": "#F6CFA6", "link": "#2F5D8A"}
    for k, v in want.items():
        assert var(k).lower() == v.lower(), k


def test_heat_and_fills():
    for i, c in enumerate(["#B4A2EA", "#8467D6", "#5A3DB3", "#3B2585"], 1):
        assert fill(".heat-%d" % i).lower() == c.lower()
    assert fill(".heat-0").lower() == "#e3e6eb"
    assert "fill: none" in re.search(r"\.heat-empty\s*\{([^}]*)\}", APP).group(1)
    assert "stroke: var(--line)" in APP
    assert fill(".wait-now") == var("wait")
    for cls, v in [("done", "done"), ("doing", "busy"), ("stuck", "stuck"), ("todo", "line")]:
        assert fill(".task-cell.task-" + cls) == var(v)
    assert re.search(r"(?m)^a\s*\{\s*color:\s*var\(--link\)", APP)


def test_brief_aging_and_overlay_rules_exist():
    assert re.search(r"\.brief-aging > summary\s*\{[^}]*var\(--text\)[^}]*var\(--wait-now\)[^}]*4px solid var\(--wait\)", APP)
    for sel in (".seg-hatch", ".hatch-line", ".svg-defs", ".mark-commit", ".mark-merge"):
        assert sel in APP


def test_no_bare_task_fill_rules():
    # the fill lives only in .task-cell.task-*; bare copies would mask a missing rule
    assert not re.search(r"(?m)^\.task-(todo|doing|stuck)\s*\{[^}]*fill", APP)


def rule_body(sel):
    m = re.findall(r"(?m)^%s\s*\{([^}]*)\}" % re.escape(sel), APP)
    assert m, sel
    return m[-1]


def test_hatch_overlay_lets_the_pointer_through_and_its_lines_are_translucent():
    # the <title> tooltip lives on the segment under the overlay; 0.55 is the reviewed mockup
    assert re.search(r"pointer-events:\s*none\s*;", rule_body(".seg-hatch"))
    assert re.search(r"stroke-opacity:\s*0\.55\s*;", rule_body(".hatch-line"))
