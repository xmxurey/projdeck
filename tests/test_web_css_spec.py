"""Independent spec tests for T01: web palette variables and CSS rules.

Expected values come from the task brief only. A small cascade resolver
(base.css first, app.css after) is exercised on synthetic CSS as well, so the
resolver itself is not a rubber stamp.
"""
import hashlib
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "projdeck", "vendor", "webkit", "base.css")
APP = os.path.join(ROOT, "projdeck", "web", "app.css")
SOURCE = os.path.join(ROOT, "projdeck", "vendor", "webkit", "SOURCE.json")

ALLOWED_HEX = {h.lower() for h in (
    "#2a78d6 #1baf7a #eb6834 #008300 #e34948 #2F5D8A #F6CFA6 "
    "#B4A2EA #8467D6 #5A3DB3 #3B2585 #E3E6EB").split()}


# ---------------------------------------------------------------- mini CSS

def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def parse_rules(css):
    """Flat list of (selector, {prop: (value, important)}), top level only.
    At-rule blocks (@media ...) are skipped entirely."""
    css = strip_comments(css)
    out, i, n = [], 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            break
        head = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            depth += {"{": 1, "}": -1}.get(css[k], 0)
            k += 1
        body = css[j + 1:k - 1]
        i = k
        if head.startswith("@"):
            continue
        decls = {}
        for d in body.split(";"):
            if ":" not in d:
                continue
            p, v = d.split(":", 1)
            v = v.strip()
            imp = bool(re.search(r"!\s*important\s*$", v))
            v = re.sub(r"\s*!\s*important\s*$", "", v).strip()
            decls[p.strip().lower()] = (v, imp)
        for sel in head.split(","):
            out.append((re.sub(r"\s+", " ", sel.strip()), decls))
    return out


def specificity(sel):
    ids = len(re.findall(r"#[\w-]+", sel))
    cls = len(re.findall(r"\.[\w-]+|\[[^\]]*\]|:(?!:)[\w-]+", sel))
    tags = len(re.findall(r"(?:^|[\s>+~])[a-zA-Z][\w-]*", sel))
    return (ids, cls, tags)


def matches(sel, tag, classes):
    """Only compound selectors (no combinators) are matched; others never."""
    if re.search(r"[\s>+~]", sel):
        return False
    m = re.match(r"^([a-zA-Z][\w-]*)?((?:\.[\w-]+)*)$", sel)
    if not m:
        return False
    if m.group(1) and m.group(1) != tag:
        return False
    need = set(c for c in m.group(2).split(".") if c)
    return need <= set(classes)


def cascade(rules, tag, classes):
    """rules: ordered list. Returns {prop: value} effective."""
    best = {}
    for order, (sel, decls) in enumerate(rules):
        if not matches(sel, tag, classes):
            continue
        for p, (v, imp) in decls.items():
            key = (imp, specificity(sel), order)
            if p not in best or key >= best[p][0]:
                best[p] = (key, v)
    return {p: v for p, (_, v) in best.items()}


def root_vars(rules):
    best = {}
    for order, (sel, decls) in enumerate(rules):
        if sel not in (":root", "html"):
            continue
        for p, (v, imp) in decls.items():
            if p.startswith("--"):
                key = (imp, specificity(sel), order)
                if p not in best or key >= best[p][0]:
                    best[p] = (key, v)
    return {p: v for p, (_, v) in best.items()}


def resolve(value, variables, depth=0):
    assert depth < 10, "var() cycle"
    if value is None:
        return None
    def sub(m):
        name = m.group(1)
        assert name in variables, f"undefined variable {name}"
        return resolve(variables[name], variables, depth + 1)
    return re.sub(r"var\(\s*(--[\w-]+)\s*\)", sub, value).strip()


def norm(v):
    return re.sub(r"\s+", " ", v.strip()).lower() if v is not None else None


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def sheet():
    rules = parse_rules(read(BASE)) + parse_rules(read(APP))
    return rules, root_vars(rules)


def eff(sheet, tag, classes, prop):
    rules, variables = sheet
    return norm(resolve(cascade(rules, tag, classes).get(prop), variables))


# ------------------------------------------------- resolver self-checks

def test_resolver_later_sheet_overrides_variable_and_rule():
    base = ":root{--busy:#111111;--x:var(--busy)} a{color:var(--busy)} .k{fill:red}"
    app = ":root{--busy:#222222} a{color:var(--link)} :root{--link:#333333}"
    rules = parse_rules(base) + parse_rules(app)
    v = root_vars(rules)
    assert resolve("var(--x)", v) == "#222222"
    assert norm(resolve(cascade(rules, "a", [])["color"], v)) == "#333333"


def test_resolver_specificity_beats_order():
    rules = parse_rules(".a.b{fill:blue} .a{fill:red}")
    assert cascade(rules, "rect", ["a", "b"])["fill"] == "blue"
    assert cascade(rules, "rect", ["a"])["fill"] == "red"


def test_resolver_detects_bad_data():
    rules = parse_rules(".heat-1{fill:#B4A2EA;fill-opacity:.5}")
    assert "fill-opacity" in cascade(rules, "rect", ["heat-1"])


# ------------------------------------------------------- acceptance 1

@pytest.mark.parametrize("name,value", [
    ("--busy", "#2a78d6"), ("--sub", "#1baf7a"), ("--wait", "#eb6834"),
    ("--done", "#008300"), ("--stuck", "#e34948"), ("--wait-now", "#f6cfa6"),
    ("--link", "#2f5d8a"),
    ("--heat-1", "#b4a2ea"), ("--heat-2", "#8467d6"),
    ("--heat-3", "#5a3db3"), ("--heat-4", "#3b2585"),
])
def test_variable_values_after_app_overrides_base(sheet, name, value):
    _, variables = sheet
    assert name in variables, f"{name} not defined"
    assert norm(resolve(variables[name], variables)) == value


def test_app_css_loaded_after_base_in_web_pages():
    from projdeck import web
    html = web.render({"hosts": [], "summary": {"waiting": 0, "busy": 0, "idle": 0,
                       "open_todos": 0, "todo_projects": 0, "zellij_exited": [],
                       "zellij_exited_n": 0, "no_backlog": []},
                       "waiting": [], "busy": [], "idle": [], "ended_today": [],
                       "stale": [], "stale_host_sessions": [], "top_todos": [],
                       "backlogs": [],
                       "lanes": {"start": 0, "end": 1, "rows": [], "more": 0}}, "home")
    assert html.index("/static/base.css") < html.index("/static/app.css")


# ------------------------------------------------------- acceptance 2

@pytest.mark.parametrize("n,color", [(1, "#b4a2ea"), (2, "#8467d6"),
                                     (3, "#5a3db3"), (4, "#3b2585")])
def test_heat_levels_fill_and_no_opacity(sheet, n, color):
    cls = [f"heat-{n}"]
    assert eff(sheet, "rect", cls, "fill") == color
    rules, _ = sheet
    assert "fill-opacity" not in cascade(rules, "rect", cls)
    assert "opacity" not in cascade(rules, "rect", cls)


def test_heat_zero_is_line_grey(sheet):
    assert eff(sheet, "rect", ["heat-0"], "fill") == "#e3e6eb"
    rules, _ = sheet
    assert "fill-opacity" not in cascade(rules, "rect", ["heat-0"])


def test_heat_empty_hollow_with_stroke(sheet):
    assert eff(sheet, "rect", ["heat-empty"], "fill") in (None, "none", "transparent")
    stroke = eff(sheet, "rect", ["heat-empty"], "stroke")
    assert stroke not in (None, "none", "transparent")


# ------------------------------------------------------- acceptance 3

def test_wait_now_fill_is_wait(sheet):
    assert eff(sheet, "rect", ["wait-now"], "fill") == "#eb6834"


@pytest.mark.parametrize("cls,color", [("task-done", "#008300"), ("task-doing", "#2a78d6"),
                                       ("task-stuck", "#e34948"), ("task-todo", "#e3e6eb")])
def test_task_cell_fills(sheet, cls, color):
    assert eff(sheet, "rect", ["task-cell", cls], "fill") == color


def compound_task_cell(sels, cls):
    """Selectors that are the compound .task-cell.<cls> (either order), not a bare .<cls>."""
    pat = re.compile(r"^(\.task-cell\.%s|\.%s\.task-cell)$" % (re.escape(cls), re.escape(cls)))
    return [s for s in sels if pat.match(s)]


def test_task_cell_rules_are_compound_selectors_present():
    sels = {s for s, _ in parse_rules(read(APP))}
    for cls in ("task-done", "task-doing", "task-stuck", "task-todo"):
        assert compound_task_cell(sels, cls), cls


def test_compound_check_ignores_a_bare_class_rule():
    sels = {s for s, _ in parse_rules(".task-done { color: red; } .task-doing .task-title {}")}
    assert compound_task_cell(sels, "task-done") == []
    assert compound_task_cell(sels, "task-doing") == []
    assert compound_task_cell({".task-done.task-cell"}, "task-done") == [".task-done.task-cell"]


def test_link_color(sheet):
    assert eff(sheet, "a", [], "color") == "#2f5d8a"


@pytest.mark.parametrize("cls", [".seg-hatch", ".hatch-line", ".svg-defs",
                                 ".mark-commit", ".mark-merge", ".wait-now"])
def test_interface_rules_exist(cls):
    sels = [s for s, _ in parse_rules(read(APP))]
    assert any(re.search(re.escape(cls) + r"(?![\w-])", s) for s in sels), cls


def test_brief_aging_summary_rule(sheet):
    rules, variables = sheet
    hits = [d for s, d in rules if s == ".brief-aging > summary"
            or s == ".brief-aging>summary"]
    assert hits, "no .brief-aging > summary rule"
    merged = {}
    for d in hits:
        for p, (v, _) in d.items():
            merged[p] = v
    r = lambda v: norm(resolve(v, variables))
    assert r(merged["color"]) == r("var(--text)")
    bg = merged.get("background-color", merged.get("background"))
    assert r(bg) == "#f6cfa6"
    assert r(merged["border-left"]) == "4px solid #eb6834"


# ------------------------------------------------------- acceptance 4

def test_no_url_or_remote_refs():
    css = strip_comments(read(APP)).lower()
    for bad in ("url(", "http://", "https://"):
        assert bad not in css
    # raw file too (comments included): keep it clean
    raw = read(APP).lower()
    for bad in ("url(", "http://", "https://"):
        assert bad not in raw


def hex_outside_plan(css):
    """(hex colors found, sorted ones outside ALLOWED_HEX); comments are not counted."""
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b", strip_comments(css))
    return found, sorted({h for h in found if h.lower() not in ALLOWED_HEX})


def test_hex_colors_only_from_allowed_set():
    found, bad = hex_outside_plan(read(APP))
    assert found, "expected palette definitions"
    assert not bad, f"colors outside the plan: {bad}"


def test_hex_guard_fires_on_bad_data():
    found, bad = hex_outside_plan("a{fill:#abcdef} b{fill:#2A78D6} /* #123456 */ c{fill:#F5F6F8}")
    assert found == ["#abcdef", "#2A78D6", "#F5F6F8"] and bad == ["#F5F6F8", "#abcdef"]


def test_base_css_hash_matches_source_json():
    with open(SOURCE, encoding="utf-8") as f:
        meta = json.load(f)
    with open(BASE, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == meta["files"]["base.css"]


def test_served_app_css_is_the_file():
    from projdeck import serve
    path, ctype = serve.STATIC["/static/app.css"]
    assert os.path.samefile(path, APP) and ctype == "text/css"
