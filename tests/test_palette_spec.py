"""Independent spec tests for T05 (G15 palette guard): the colour maths is re-implemented here from the
brief's algorithm spec and pinned to the external validator's output; the real palettes (app.css cascade,
ui constants, rendered web/terminal output) are then judged by it, and old/bad palettes must fail."""
import math
import re
from pathlib import Path

import pytest

from projdeck import ui, web

ROOT = Path(__file__).resolve().parent.parent
BASE_CSS = ROOT / "projdeck" / "vendor" / "webkit" / "base.css"
APP_CSS = ROOT / "projdeck" / "web" / "app.css"
NOW = 1_800_000_000
HOME = "/home/u"

# ------------------------------------------------------------------ colour maths (brief "算法规格")

PROTAN = [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]]
DEUTAN = [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]]
TRITAN = [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]]


def rgb_of(c):
    if isinstance(c, tuple):
        return c
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def lin(v):
    c = v / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lin3(c):
    return [lin(v) for v in rgb_of(c)]


def luminance(c):
    r, g, b = lin3(c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def cbrt(x):
    return math.copysign(abs(x) ** (1 / 3), x)


def oklab_lin(rgb):
    r, g, b = rgb
    l = cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def oklch(c):
    L, a, b = oklab_lin(lin3(c))
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def sim(c, mat):
    v = lin3(c)
    return [min(1.0, max(0.0, sum(mat[i][j] * v[j] for j in range(3)))) for i in range(3)]


def de(c1, c2, mat=None):
    p, q = (lin3(c1), lin3(c2)) if mat is None else (sim(c1, mat), sim(c2, mat))
    return 100 * math.dist(oklab_lin(p), oklab_lin(q))


def de4(c1, c2):
    return tuple(de(c1, c2, m) for m in (None, PROTAN, DEUTAN, TRITAN))


# ------------------------------------------------------------------ the judge (properties of the brief)

LIGHT_BAND, DARK_BAND = (0.43, 0.77), (0.48, 0.67)
KNOWN_WARN = {"#1baf7a": 2.82}


def judge_categorical(colors, mode):
    """Return {check_name: [problems]}; empty lists mean pass."""
    band = LIGHT_BAND if mode == "light" else DARK_BAND
    surface = "#FFFFFF" if mode == "light" else "#15181D"
    out = {"band": [], "chroma": [], "normal": [], "cvd": [], "contrast": []}
    for c in colors:
        L, C, _ = oklch(c)
        if not band[0] <= L <= band[1]:
            out["band"].append((c, round(L, 3)))
        if C < 0.1:
            out["chroma"].append((c, round(C, 3)))
        k = contrast(c, surface)
        if k < 3:
            out["contrast"].append((c.lower(), round(k, 2)))
    for i, a in enumerate(colors):
        for b in colors[i + 1:]:
            n, p, d, _t = de4(a, b)
            if n < 15:
                out["normal"].append((a, b, round(n, 2)))
            if min(p, d) < 6:
                out["cvd"].append((a, b, round(min(p, d), 2)))
    return out


def failed(res, ignore_known=True):
    bad = {}
    for k, v in res.items():
        if k == "contrast" and ignore_known:
            v = [x for x in v if KNOWN_WARN.get(x[0]) != x[1]]
        if v:
            bad[k] = v
    return bad


def judge_ordinal(ramp, lightest_vs=("#FFFFFF",), first_gap_from=None):
    """ramp: light to dark. Returns list of failure names."""
    fails = []
    Ls = [oklch(c)[0] for c in ramp]
    seq = ([oklch(first_gap_from)[0]] if first_gap_from else []) + Ls
    if any(b >= a for a, b in zip(seq, seq[1:])):
        fails.append("monotone")
    if any(a - b < 0.06 for a, b in zip(seq, seq[1:])):
        fails.append("delta_l")
    if contrast(ramp[0], lightest_vs[0]) < 2.0:
        fails.append("light_end_contrast")
    hs = [oklch(c)[2] for c in ramp]
    if max(hs) - min(hs) > 10.0 + 1e-9 or max(hs) - min(hs) < 8.0 - 1e-9:
        fails.append("hue_span")
    return fails


# ------------------------------------------------------------------ css cascade

def root_vars(text):
    out = {}
    for m in re.finditer(r":root\s*\{([^}]*)\}", text):
        for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)):
            out[k] = v.strip()
    return out


def cascade():
    v = root_vars(BASE_CSS.read_text())
    v.update(root_vars(APP_CSS.read_text()))  # app.css loads after base.css and wins
    return v


def css_hex(name):
    val = cascade()[name]
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", val), (name, val)
    return val


def hexs(rgb):
    return "#%02x%02x%02x" % rgb


# ------------------------------------------------------------------ anchors (external validator output)

ANCHORS_LC = {
    "#6f9bcb": (0.6765, 0.0862), "#a9c6e4": (0.8151, 0.0528), "#2a78d6": (0.5753, 0.1626),
    "#1baf7a": (0.6690, 0.1408), "#eb6834": (0.6708, 0.1750), "#3987e5": (0.6221, 0.1612),
    "#199e70": (0.6212, 0.1283), "#d95926": (0.6221, 0.1726), "#008300": (0.5285, 0.1798),
    "#e34948": (0.6226, 0.1909), "#0ca30c": (0.6210, 0.2073), "#e66767": (0.6693, 0.1586),
    "#E3E6EB": (0.9241, 0.0075), "#DCD3F5": (0.8846, 0.0472),
}
ANCHOR_HUE = {"#6f9bcb": 251.28, "#2a78d6": 255.53, "#1baf7a": 162.11, "#eb6834": 40.64,
              "#008300": 142.50, "#e34948": 24.91, "#DCD3F5": 297.54}

ANCHOR_PAIRS = [
    ("#6f9bcb", "#a9c6e4", (14.25, 13.52, 14.61, 14.45)),
    ("#2a78d6", "#1baf7a", (24.04, 23.06, 23.16, 9.62)),
    ("#2a78d6", "#eb6834", (33.59, 24.74, 31.66, 32.68)),
    ("#1baf7a", "#eb6834", (27.60, 12.59, 9.23, 33.27)),
    ("#3987e5", "#199e70", (20.94, 19.74, 19.56, 3.97)),
    ("#3987e5", "#d95926", (31.84, 26.81, 28.58, 32.38)),
    ("#199e70", "#d95926", (26.53, 12.63, 9.45, 32.36)),
    ("#008300", "#e34948", (33.08, 7.19, 10.64, 34.89)),
    ("#008300", "#2a78d6", (28.95, 26.51, 26.67, 7.55)),
    ("#2a78d6", "#e34948", (32.33, 21.63, 27.32, 34.51)),
    ("#0ca30c", "#e66767", (32.19, 11.95, 7.12, 31.78)),
    ("#0ca30c", "#3987e5", (30.75, 27.96, 27.89, 5.12)),
    ("#3987e5", "#e66767", (29.04, 19.17, 24.94, 31.38)),
    ("#E3E6EB", "#DCD3F5", (5.72, 5.68, 5.33, 4.15)),
]
ANCHOR_CONTRAST = [
    ("#1baf7a", "#FFFFFF", 2.82), ("#2a78d6", "#FFFFFF", 4.42), ("#eb6834", "#FFFFFF", 3.20),
    ("#008300", "#FFFFFF", 4.95), ("#e34948", "#FFFFFF", 3.95), ("#DCD3F5", "#FFFFFF", 1.43),
    ("#3987e5", "#15181D", 4.89), ("#199e70", "#15181D", 5.22), ("#d95926", "#15181D", 4.58),
    ("#0ca30c", "#15181D", 5.30), ("#e66767", "#15181D", 5.51),
]


@pytest.mark.parametrize("c", sorted(ANCHORS_LC))
def test_lc_matches_external(c):
    L, C, _ = oklch(c)
    eL, eC = ANCHORS_LC[c]
    assert abs(L - eL) <= 0.0005 and abs(C - eC) <= 0.0005, (c, L, C)


@pytest.mark.parametrize("c", sorted(ANCHOR_HUE))
def test_hue_matches_external(c):
    assert abs(oklch(c)[2] - ANCHOR_HUE[c]) <= 0.02


@pytest.mark.parametrize("a,b,exp", ANCHOR_PAIRS)
def test_delta_e_matches_external(a, b, exp):
    got = de4(a, b)
    for g, e, name in zip(got, exp, ("normal", "protan", "deutan", "tritan")):
        assert abs(g - e) <= 0.02, (a, b, name, g, e)


@pytest.mark.parametrize("c,surface,exp", ANCHOR_CONTRAST)
def test_contrast_matches_external(c, surface, exp):
    assert abs(contrast(c, surface) - exp) <= 0.01


# ------------------------------------------------------------------ the real palettes

def web_light():
    v = {k: css_hex("--" + k) for k in ("busy", "sub", "wait", "done", "stuck")}
    return v


def term_dark():
    return {"busy": hexs(ui.CELL_RGB["busy"]), "sub": hexs(ui.CELL_RGB["sub"]),
            "wait": hexs(ui.CELL_RGB["wait"]), "done": hexs(ui.DONE_RGB),
            "doing": hexs(ui.DOING_RGB), "stuck": hexs(ui.STUCK_RGB)}


def test_spec_colour_values_in_css():
    v = cascade()
    exp = {"--busy": "#2a78d6", "--sub": "#1baf7a", "--wait": "#eb6834", "--done": "#008300",
           "--stuck": "#e34948", "--link": "#2F5D8A", "--wait-now": "#F6CFA6",
           "--heat-1": "#B4A2EA", "--heat-2": "#8467D6", "--heat-3": "#5A3DB3", "--heat-4": "#3B2585"}
    for k, e in exp.items():
        assert v[k].lower() == e.lower(), k


def test_spec_terminal_constants():
    assert ui.CELL_RGB["busy"] == (57, 135, 229)
    assert ui.CELL_RGB["sub"] == (25, 158, 112)
    assert ui.CELL_RGB["wait"] == (217, 89, 38) == ui.CELL_RGB["wait_now"] == ui.WAIT_RGB
    assert ui.DOING_RGB == (57, 135, 229)
    assert ui.DONE_RGB == (12, 163, 12)
    assert ui.STUCK_RGB == (230, 103, 103)


def test_web_light_timeline_passes():
    w = web_light()
    assert failed(judge_categorical([w["busy"], w["sub"], w["wait"]], "light")) == {}


def test_web_light_taskbar_passes():
    w = web_light()
    assert failed(judge_categorical([w["done"], w["busy"], w["stuck"]], "light")) == {}


def test_term_dark_timeline_passes():
    t = term_dark()
    assert failed(judge_categorical([t["busy"], t["sub"], t["wait"]], "dark")) == {}


def test_term_dark_taskbar_passes():
    t = term_dark()
    assert failed(judge_categorical([t["done"], t["doing"], t["stuck"]], "dark")) == {}


def test_only_known_warn_is_sub_light():
    w = web_light()
    res = judge_categorical([w["busy"], w["sub"], w["wait"]], "light")
    assert [(c, k) for c, k in res["contrast"]] == [("#1baf7a", 2.82)]
    res = judge_categorical([w["done"], w["busy"], w["stuck"]], "light")
    assert res["contrast"] == []
    for pal in ([term_dark()[k] for k in ("busy", "sub", "wait")],
                [term_dark()[k] for k in ("done", "doing", "stuck")]):
        assert judge_categorical(pal, "dark")["contrast"] == []


def test_known_warn_exception_is_exact():
    # the exception must not swallow a different value for the same hex or any other colour
    assert failed({"contrast": [("#1baf7a", 2.82)], "band": []}) == {}
    assert failed({"contrast": [("#1baf7a", 2.5)]}) != {}
    assert failed({"contrast": [("#abcdef", 2.82)]}) != {}


def test_cvd_worst_pairs_match_validator():
    w = web_light()
    n, p, d, t = de4(w["sub"], w["wait"])
    assert abs(min(p, d) - 9.23) <= 0.02 and abs(t - 33.27) <= 0.02
    t_ = term_dark()
    p_, d_ = de4(t_["done"], t_["stuck"])[1:3]
    assert min(p_, d_) >= 6


# ------------------------------------------------------------------ heat ramp

def heat_ramp():
    return [css_hex("--heat-%d" % i) for i in (1, 2, 3, 4)]


def test_heat_ramp_a1_passes():
    ramp = heat_ramp()
    assert [r.lower() for r in ramp] == ["#b4a2ea", "#8467d6", "#5a3db3", "#3b2585"]
    assert judge_ordinal(ramp, first_gap_from=css_hex("--line")) == []


def test_heat_numbers():
    ramp = heat_ramp()
    Ls = [oklch(c)[0] for c in ramp]
    zero = oklch(css_hex("--line"))[0]
    assert all(a > b for a, b in zip([zero] + Ls, Ls))
    assert all(a - b >= 0.06 for a, b in zip([zero] + Ls, Ls))
    assert contrast(ramp[0], "#FFFFFF") >= 2.0
    assert abs(contrast(ramp[0], "#FFFFFF") - 2.26) <= 0.01
    hs = [oklch(c)[2] for c in ramp]
    # hues are taken mod 360, so a band straddling 0 would show here as a spread near 351
    assert abs((max(hs) - min(hs)) - 9) <= 1


def test_heat_zero_is_line_and_classes_kept():
    css = APP_CSS.read_text()
    assert re.search(r"\.heat-0\s*\{\s*fill:\s*var\(--line\)", css)
    for i in (1, 2, 3, 4):
        assert re.search(r"\.heat-%d\s*\{\s*fill:\s*var\(--heat-%d\)" % (i, i), css)
    assert re.search(r"\.heat-empty\s*\{[^}]*fill:\s*none", css)
    assert css_hex("--line").lower() == "#e3e6eb"


# ------------------------------------------------------------------ guards must bite (bad data)

def test_old_terminal_timeline_fails_three_checks():
    res = judge_categorical(["#6f9bcb", "#a9c6e4", "#f0a35e"], "dark")
    assert res["band"] and res["chroma"] and res["normal"]
    assert {"band", "chroma", "normal"} <= set(failed(res))
    assert any(abs(x[2] - 14.25) <= 0.02 for x in res["normal"])


def test_old_heat_with_zero_grey_fails_both():
    ramp = ["#DCD3F5", "#B4A2EA", "#8467D6", "#5A3DB3"]
    fails = judge_ordinal(ramp, first_gap_from="#E3E6EB")
    assert "delta_l" in fails and "light_end_contrast" in fails
    dl = oklch("#E3E6EB")[0] - oklch("#DCD3F5")[0]
    assert abs(dl - 0.039) <= 0.001
    assert abs(contrast("#DCD3F5", "#FFFFFF") - 1.43) <= 0.01
    assert judge_ordinal(ramp) == ["light_end_contrast"]


def test_a1_with_grey_as_ramp_end_fails_contrast_and_hue():
    fails = judge_ordinal(["#E3E6EB", "#B4A2EA", "#8467D6", "#5A3DB3", "#3B2585"])
    assert "light_end_contrast" in fails and "hue_span" in fails


def test_nonmonotone_ramp_fails():
    assert "monotone" in judge_ordinal(["#B4A2EA", "#5A3DB3", "#8467D6", "#3B2585"])


def test_good_colour_stays_silent_and_bad_one_speaks_per_check():
    good = judge_categorical(["#3987e5", "#199e70", "#d95926"], "dark")
    assert failed(good) == {}
    assert judge_categorical(["#2a78d6", "#2a78d7", "#eb6834"], "light")["normal"]
    assert judge_categorical(["#2a78d6", "#1baf7a", "#eb6834"], "dark")["band"]  # wait-l L 0.6708 is above the dark 0.67 ceiling
    assert judge_categorical(["#808080", "#2a78d6", "#eb6834"], "light")["chroma"]
    # far apart to normal vision, confusable under colour-vision deficiency: only cvd speaks
    cvd = judge_categorical(["#008300", "#a85600"], "light")
    assert cvd["cvd"] and not cvd["normal"] and set(failed(cvd)) == {"cvd"}


# ------------------------------------------------------------------ secondary encoding on rendered output

class _Probe:
    pass


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


def full_board():
    lane = {"host": "s-host", "project": HOME + "/dev/projdeck", "session_id": "b1", "state": "busy",
            "segments": [["busy", NOW - 3600, NOW - 2400], ["sub", NOW - 2400, NOW - 1800],
                         ["wait", NOW - 1800, NOW - 1200], ["wait_now", NOW - 1200, NOW]],
            "commits": [NOW - 900], "merges": [["T07", NOW - 300]], "busy_s": 1800, "wait_s": 1200}
    s = sess("b1", tasks=tasks(["done", "doing", "stuck", "todo"], current="T02"))
    tile = {"host": "s-host", "project": HOME + "/dev/app", "state": "busy", "todo": 1,
            "commits_14d": [0, 1, 5, 12, 40] + [0] * 9, "tasks": ["done", "doing", "stuck", "todo"],
            "text": "t"}
    return {"hosts": [], "summary": {}, "waiting": [], "busy": [s], "idle": [], "ended_today": [],
            "stale": [], "stale_host_sessions": [],
            "lanes": {"start": NOW - 4 * 3600, "end": NOW, "rows": [lane], "more": 0},
            "top_todos": [], "backlogs": [], "tiles": [tile]}


@pytest.fixture(autouse=True)
def fixed_home(monkeypatch):
    monkeypatch.setenv("HOME", HOME)


def rects(html):
    out = []
    for m in re.finditer(r"<rect\b([^>]*?)/?>", html):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(1)))
        out.append(a)
    return out


def task_cells(html):
    return [a for a in rects(html) if "task-cell" in a.get("class", "").split()]


def check_stuck_shape(html):
    """Return a problem string or None."""
    cells = task_cells(html)
    stuck = [a for a in cells if "task-stuck" in a["class"].split()]
    normal = [a for a in cells if "task-stuck" not in a["class"].split()]
    if not stuck or not normal:
        return "no stuck or no normal cell rendered"
    if {a["height"] for a in stuck} & {a["height"] for a in normal}:
        return "stuck cell height equals a normal cell height"
    return None


def check_hatch(html):
    problems = []
    if html.count('id="hatch-wait-now"') != 1:
        problems.append("hatch pattern defined %d times" % html.count('id="hatch-wait-now"'))
    wn = [a for a in rects(html) if "wait-now" in a.get("class", "").split()
          or "seg-wait-now" in a.get("class", "").split()]
    if not wn:
        problems.append("no wait-now segment rendered")
    overlays = [a for a in rects(html) if a.get("fill") == "url(#hatch-wait-now)"]
    if len(overlays) < len(wn):
        problems.append("wait-now segment without hatch overlay")
    return problems


def check_no_fill_hex(html):
    return ['fill="#' in html, "fill='#" in html]


def test_web_stuck_cell_taller_exact_geometry():
    html = web.render(full_board(), "home")
    assert check_stuck_shape(html) is None
    assert 'viewBox="0 0 300 12"' in html
    for a in task_cells(html):
        if "task-stuck" in a["class"].split():
            assert (a["y"], a["height"]) == ("0", "12")
        else:
            assert (a["y"], a["height"]) == ("2", "8")


def test_web_wait_now_hatched_once_defined():
    html = web.render(full_board(), "home")
    assert check_hatch(html) == []
    assert 'fill="url(#hatch-wait-now)"' in html


def test_projects_page_has_stuck_shape_and_no_fill_hex():
    html = web.render(full_board(), "projects")
    assert check_stuck_shape(html) is None
    assert check_no_fill_hex(html) == [False, False]


def test_home_has_no_fill_hex():
    assert check_no_fill_hex(web.render(full_board(), "home")) == [False, False]


def strip(s):
    return ui.ANSI_RE.sub("", s)


def test_terminal_stuck_glyph_differs_from_doing_and_is_red():
    bar = ui.task_bar([{"id": "T1", "status": "doing"}, {"id": "T2", "status": "stuck"},
                       {"id": "T3", "status": "done"}])
    plain = strip(bar)
    assert plain.startswith("▣!■")
    assert "\x1b[38;2;230;103;103m!" in bar
    assert "\x1b[38;2;57;135;229m▣" in bar
    assert "▣" not in strip(ui.task_bar([{"id": "T1", "status": "stuck"}]).split(" ")[0])


def test_terminal_wait_now_glyph_differs_from_wait_same_colour_one_char():
    a, b = ui.spark(["wait"]), ui.spark(["wait_now"])
    assert strip(a) == "▆" and strip(b) == "▒"
    assert len(strip(ui.spark(["busy", "sub", "wait", "wait_now", None]))) == 5
    assert a.split("m")[0] == b.split("m")[0]  # identical colour escape


def test_terminal_long_form_stuck_current():
    items = [{"id": "T%02d" % i, "status": "done"} for i in range(1, 21)]
    items[12]["status"] = "stuck"
    out = ui.task_bar({"items": items, "current": "T13"})
    assert strip(out) == "19/20 !T13"
    assert "\x1b[38;2;230;103;103m!" in out
    items = [{"id": "T%02d" % i, "status": "done"} for i in range(1, 22)]
    items[12]["status"] = "doing"
    out = ui.task_bar({"items": items, "current": "T13"})
    assert strip(out).endswith("▣T13") and "\x1b[38;2;57;135;229m▣" in out


# ------------------------------------------------------------------ secondary encoding checkers must bite

def test_stuck_checker_fails_when_shape_removed():
    html = web.render(full_board(), "home")
    flat = re.sub(r'(class="task-cell task-stuck" x="[^"]*") y="0"([^>]*?) height="12"',
                  r'\1 y="2"\2 height="8"', html)
    assert flat != html
    assert check_stuck_shape(flat) is not None


def test_hatch_checker_fails_when_overlay_removed_or_duplicated():
    html = web.render(full_board(), "home")
    bare = re.sub(r'<rect class="seg-hatch"[^>]*/>', "", html)
    assert bare != html and check_hatch(bare)
    dup = html.replace("</svg>", '<pattern id="hatch-wait-now"></pattern></svg>', 1)
    assert check_hatch(dup)


def test_fill_hex_checker_bites():
    assert check_no_fill_hex('<rect fill="#123456" />')[0] is True
    assert check_no_fill_hex("<rect fill='#123456' />")[1] is True


def test_terminal_wait_now_and_stuck_glyphs_differ_from_wait_and_doing():
    # a renderer that drew stuck as the doing glyph / wait_now as wait would equal; ours must differ
    assert strip(ui.spark(["wait_now"])) != strip(ui.spark(["wait"]))
    assert strip(ui.task_bar([{"id": "T1", "status": "stuck"}])[:30]).split(" ")[0] != \
        strip(ui.task_bar([{"id": "T1", "status": "doing"}])).split(" ")[0]


# ------------------------------------------------------------------ css hygiene

def test_app_css_has_no_external_or_url_refs():
    css = APP_CSS.read_text()
    for bad in ("url(", "http://", "https://"):
        assert bad not in css


def test_web_html_has_no_style_attr_or_hex_fill():
    for page in ("home", "projects"):
        html = web.render(full_board(), page)
        assert " style=" not in html
        assert check_no_fill_hex(html) == [False, False], page
