"""G15: palette guard. Re-checks the light (web) and dark (terminal) colours against the thresholds
and anchor numbers of an external palette validator (numbers copied from its output, the script
itself is not in the repo). Every property is a function that returns its failures, and every one is
also fed an old or broken palette and must fail there (rule 2)."""
import math
import re
from itertools import combinations
from pathlib import Path

from projdeck import ui, web

ROOT = Path(__file__).resolve().parent.parent
BASE_CSS = ROOT / "projdeck" / "vendor" / "webkit" / "base.css"
APP_CSS = ROOT / "projdeck" / "web" / "app.css"

# ---------------------------------------------------------------- colour maths


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def lin(rgb):
    out = []
    for c in rgb:
        c = c / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return out


def luminance(rgb):
    r, g, b = lin(rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _cbrt(x):
    return math.copysign(abs(x) ** (1 / 3), x)


def oklab_lin(v):
    r, g, b = v
    l = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def oklch(rgb):
    L, a, b = oklab_lin(lin(rgb))
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


CVD = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}


def _lab(rgb, kind):
    v = lin(rgb)
    if kind != "normal":
        v = [min(1.0, max(0.0, sum(m * c for m, c in zip(row, v)))) for row in CVD[kind]]
    return oklab_lin(v)


def delta_e(x, y, kind="normal"):
    return 100 * math.dist(_lab(x, kind), _lab(y, kind))


# ------------------------------------------------------------------ thresholds

BAND = {"light": (0.43, 0.77), "dark": (0.48, 0.67)}
SURFACE = {"light": "#FFFFFF", "dark": "#15181D"}
CHROMA_FLOOR, NORMAL_FLOOR, CVD_FLOOR, CONTRAST_MIN = 0.1, 15.0, 6.0, 3.0
HEAT_STEP, HEAT_LIGHT_END, HEAT_HUE = 0.06, 2.0, (8.0, 10.0)
KNOWN_WARNS = {("light", "#1baf7a", 2.82)}     # sub on white, accepted: the legend and lane labels carry it


def check_categorical(mode, palette):
    """palette: {name: '#rrggbb'}. Returns (failures, warnings)."""
    fails, warns = [], []
    lo, hi = BAND[mode]
    surface = hex_rgb(SURFACE[mode])
    for name, hx in palette.items():
        L, C, _ = oklch(hex_rgb(hx))
        if not lo <= L <= hi:
            fails.append("band %s %s L=%.3f" % (name, hx, L))
        if C < CHROMA_FLOOR:
            fails.append("chroma %s %s C=%.3f" % (name, hx, C))
        cr = contrast(hex_rgb(hx), surface)
        if cr < CONTRAST_MIN:
            warns.append((mode, hx.lower(), round(cr, 2)))
    for (na, ha), (nb, hb) in combinations(palette.items(), 2):
        a, b = hex_rgb(ha), hex_rgb(hb)
        if delta_e(a, b) < NORMAL_FLOOR:
            fails.append("normal %s/%s dE=%.2f" % (na, nb, delta_e(a, b)))
        worst = min(delta_e(a, b, "protan"), delta_e(a, b, "deutan"))
        if worst < CVD_FLOOR:
            fails.append("cvd %s/%s dE=%.2f" % (na, nb, worst))
    return fails, warns


def check_ramp(steps, surface="#FFFFFF"):
    """steps: hex list, light to dark. OKLCH L strictly decreasing, adjacent dL, light end, single hue."""
    fails = []
    ok = [oklch(hex_rgb(s)) for s in steps]
    for (sa, a), (sb, b) in zip(zip(steps, ok), zip(steps[1:], ok[1:])):
        if not b[0] < a[0]:
            fails.append("not decreasing %s->%s" % (sa, sb))
        elif a[0] - b[0] < HEAT_STEP:
            fails.append("step %s->%s dL=%.3f" % (sa, sb, a[0] - b[0]))
    cr = contrast(hex_rgb(steps[0]), hex_rgb(surface))
    if cr < HEAT_LIGHT_END:
        fails.append("light end %s %.2f:1" % (steps[0], cr))
    hues = [h for _, _, h in ok]
    span = max(hues) - min(hues)
    if not HEAT_HUE[0] <= span <= HEAT_HUE[1]:
        fails.append("hue span %.1f" % span)
    return fails


def zero_gap_fails(zero, first):
    """The 0-commit grey against heat-1: heat-1 must be darker by at least HEAT_STEP (OKLCH L)."""
    dl = oklch(hex_rgb(zero))[0] - oklch(hex_rgb(first))[0]
    return [] if dl >= HEAT_STEP else ["zero gap %s->%s dL=%.3f" % (zero, first, dl)]


# ------------------------------------------------------------- CSS cascade


def root_vars(*sheets):
    """Custom properties of every :root block; a later sheet wins (app.css is loaded after base.css)."""
    out = {}
    for text in sheets:
        for block in re.findall(r":root\s*\{(.*?)\}", text, re.S):
            out.update({k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block)})
    return out


def css_vars():
    return root_vars(BASE_CSS.read_text(), APP_CSS.read_text())


def light_palettes(v):
    return ({"busy": v["--busy"], "sub": v["--sub"], "wait": v["--wait"]},
            {"done": v["--done"], "doing": v["--busy"], "stuck": v["--stuck"]})


def dark_palettes():
    t = {k: rgb_hex(ui.CELL_RGB[k]) for k in ("busy", "sub", "wait")}
    b = {"done": rgb_hex(ui.DONE_RGB), "doing": rgb_hex(ui.DOING_RGB), "stuck": rgb_hex(ui.STUCK_RGB)}
    return t, b


def heat_steps(v):
    return [v["--heat-%d" % i] for i in (1, 2, 3, 4)]


# ------------------------------------------------------------- secondary encoding

WAIT_NOW_SEG = [["busy", 100, 200], ["wait", 200, 300], ["wait_now", 300, 400]]


def render_home():
    board = {"now": 1000, "lanes": {"start": 0, "end": 1000, "rows": [
        {"session_id": "s1", "host": "s-host", "state": "busy", "project": "/home/u/app",
         "segments": WAIT_NOW_SEG, "commits": [], "merges": []}]},
        "busy": [{"id": "s1", "host": "s-host", "project": "/home/u/app", "state": "busy",
                  "tasks": {"items": [{"id": "T1", "status": "done"}, {"id": "T2", "status": "doing"},
                                      {"id": "T3", "status": "stuck"}, {"id": "T4", "status": "todo"}],
                            "current": "T2"}}],
        "idle": [], "waiting": [], "tiles": [], "hosts": []}
    return web.render(board, "home", True, 1000)


def render_projects():
    t = {"host": "s-host", "project": "/home/u/app", "state": "busy", "todo": 1,
         "commits_14d": [0, 1, 5, 12, 40] + [0] * 9, "tasks": ["done", "doing", "stuck", "todo"], "text": "x"}
    return web.render({"tiles": [t], "lanes": {"start": 0, "end": 1000, "rows": []}}, "projects", True, 1000)


def render_terminal():
    stuck = [{"id": "T1", "status": "doing"}, {"id": "T2", "status": "stuck"}]
    return {"wait": ui.strip_ansi(ui.spark(["wait"])), "wait_now": ui.strip_ansi(ui.spark(["wait_now"])),
            "doing": ui.strip_ansi(ui.task_bar([stuck[0]])).split()[0],
            "stuck": ui.strip_ansi(ui.task_bar([stuck[1]])).split()[0]}


def check_encoding(home, projects, term):
    fails = []
    for html in (home, projects):
        heights = {m.group(1): m.group(2) for m in re.finditer(
            r'<rect class="task-cell task-(\w+)"[^>]* height="(\d+)"', html)}
        if "stuck" not in heights or "doing" not in heights:
            fails.append("task bar cells not found in render")
        elif heights["stuck"] == heights["doing"]:
            fails.append("web stuck cell has the normal height")
    if 'fill="url(#hatch-wait-now)"' not in home:
        fails.append("web wait_now has no hatch overlay")
    if term["stuck"] == term["doing"]:
        fails.append("terminal stuck glyph equals doing glyph")
    if term["wait_now"] == term["wait"]:
        fails.append("terminal wait_now glyph equals wait glyph")
    for name, html in (("home", home), ("projects", projects)):
        if 'fill="#' in html:
            fails.append("%s html has an inline fill colour" % name)
    return fails


# =================================================================== anchors
# name: (hex, L, C)  -- copied from the external validator output
ANCHOR_LC = {
    "old-term-busy": ("#6f9bcb", 0.6765, 0.0862), "old-term-sub": ("#a9c6e4", 0.8151, 0.0528),
    "busy-l": ("#2a78d6", 0.5753, 0.1626), "sub-l": ("#1baf7a", 0.6690, 0.1408),
    "wait-l": ("#eb6834", 0.6708, 0.1750), "busy-d": ("#3987e5", 0.6221, 0.1612),
    "sub-d": ("#199e70", 0.6212, 0.1283), "wait-d": ("#d95926", 0.6221, 0.1726),
    "done-l": ("#008300", 0.5285, 0.1798), "stuck-l": ("#e34948", 0.6226, 0.1909),
    "done-d": ("#0ca30c", 0.6210, 0.2073), "stuck-d": ("#e66767", 0.6693, 0.1586),
    "heat0": ("#E3E6EB", 0.9241, 0.0075), "heat-orig1": ("#DCD3F5", 0.8846, 0.0472),
}
ANCHOR_H = {"old-term-busy": 251.28, "old-term-sub": 249.36, "busy-l": 255.53, "sub-l": 162.11,
            "wait-l": 40.64, "busy-d": 255.05, "sub-d": 163.11, "wait-d": 40.11, "done-l": 142.50,
            "stuck-l": 24.91, "done-d": 142.56, "stuck-d": 22.31, "heat0": 260.73, "heat-orig1": 297.54}
# (a, b): normal, protan, deutan, tritan
ANCHOR_PAIRS = {
    ("old-term-busy", "old-term-sub"): (14.25, 13.52, 14.61, 14.45),
    ("busy-l", "sub-l"): (24.04, 23.06, 23.16, 9.62),
    ("busy-l", "wait-l"): (33.59, 24.74, 31.66, 32.68),
    ("sub-l", "wait-l"): (27.60, 12.59, 9.23, 33.27),
    ("busy-d", "sub-d"): (20.94, 19.74, 19.56, 3.97),
    ("busy-d", "wait-d"): (31.84, 26.81, 28.58, 32.38),
    ("sub-d", "wait-d"): (26.53, 12.63, 9.45, 32.36),
    ("done-l", "stuck-l"): (33.08, 7.19, 10.64, 34.89),
    ("done-l", "busy-l"): (28.95, 26.51, 26.67, 7.55),
    ("busy-l", "stuck-l"): (32.33, 21.63, 27.32, 34.51),
    ("done-d", "stuck-d"): (32.19, 11.95, 7.12, 31.78),
    ("done-d", "busy-d"): (30.75, 27.96, 27.89, 5.12),
    ("busy-d", "stuck-d"): (29.04, 19.17, 24.94, 31.38),
    ("heat0", "heat-orig1"): (5.72, 5.68, 5.33, 4.15),
}
ANCHOR_CONTRAST = {"sub-l": 2.82, "busy-l": 4.42, "wait-l": 3.20, "done-l": 4.95, "stuck-l": 3.95,
                   "heat-orig1": 1.43, "busy-d": 4.89, "sub-d": 5.22, "wait-d": 4.58,
                   "done-d": 5.30, "stuck-d": 5.51}
DARK_ANCHORS = {"busy-d", "sub-d", "wait-d", "done-d", "stuck-d"}


def test_anchor_lightness_and_chroma():
    for name, (hx, L, C) in ANCHOR_LC.items():
        gl, gc, gh = oklch(hex_rgb(hx))
        assert abs(gl - L) <= 0.0005, name
        assert abs(gc - C) <= 0.0005, name
        assert abs(gh - ANCHOR_H[name]) <= 0.05, name


def test_anchor_delta_e_all_four_views():
    for (a, b), want in ANCHOR_PAIRS.items():
        x, y = hex_rgb(ANCHOR_LC[a][0]), hex_rgb(ANCHOR_LC[b][0])
        got = [delta_e(x, y, k) for k in ("normal", "protan", "deutan", "tritan")]
        for g, w, k in zip(got, want, ("normal", "protan", "deutan", "tritan")):
            assert abs(g - w) <= 0.02, (a, b, k, g, w)


def test_anchor_contrast():
    for name, want in ANCHOR_CONTRAST.items():
        surface = SURFACE["dark" if name in DARK_ANCHORS else "light"]
        got = contrast(hex_rgb(ANCHOR_LC[name][0]), hex_rgb(surface))
        assert abs(got - want) <= 0.01, (name, got, want)


# ===================================================== the current palette passes


def test_current_light_palettes_pass_with_only_the_known_warn():
    v = css_vars()
    warns = []
    for pal in light_palettes(v):
        f, w = check_categorical("light", pal)
        assert f == []
        warns += w
    assert set(warns) == KNOWN_WARNS, "a WARN appeared, vanished or changed: %r" % warns


def test_current_dark_palettes_pass_without_warn():
    for pal in dark_palettes():
        assert check_categorical("dark", pal) == ([], [])


def test_current_heat_ramp_passes():
    assert check_ramp(heat_steps(css_vars())) == []


def test_heat_zero_is_the_line_grey_and_first_step_clears_it():
    v = css_vars()
    assert v["--line"].lower() == "#e3e6eb"
    assert re.search(r"\.heat-0\s*\{\s*fill:\s*var\(--line\)", APP_CSS.read_text())
    assert zero_gap_fails(v["--line"], heat_steps(v)[0]) == []


def test_app_css_overrides_base_css():
    v = css_vars()
    base = root_vars(BASE_CSS.read_text())
    assert v["--busy"] != base["--busy"] and v["--busy"].lower() == "#2a78d6"


def test_current_rendering_has_secondary_encoding():
    assert check_encoding(render_home(), render_projects(), render_terminal()) == []


def test_render_samples_actually_contain_the_features():
    home = render_home()
    assert "seg-wait-now" in home and "task-stuck" in home and "hatch-wait-now" in home
    assert render_terminal()["stuck"] and "task-stuck" in render_projects()


# =============================================== bad data makes the guards fail

OLD_TERM = {"busy": "#6f9bcb", "sub": "#a9c6e4", "wait": "#f0a35e"}
ORIG_HEAT = ["#DCD3F5", "#B4A2EA", "#8467D6", "#5A3DB3"]


def test_old_terminal_timeline_fails_band_chroma_and_normal():
    fails, _ = check_categorical("dark", OLD_TERM)
    kinds = {f.split()[0] for f in fails}
    assert {"band", "chroma", "normal"} <= kinds


def test_original_heat_ramp_fails_light_end():
    assert any(f.startswith("light end") and "1.43" in f for f in check_ramp(ORIG_HEAT))


def test_original_heat_with_zero_grey_fails_step_and_light_end():
    assert zero_gap_fails("#E3E6EB", ORIG_HEAT[0]) == ["zero gap #E3E6EB->#DCD3F5 dL=0.039"]
    assert any(f.startswith("light end") for f in check_ramp(ORIG_HEAT))


def test_zero_gap_fails_when_heat_1_is_lighter_than_the_grey():
    [f] = zero_gap_fails("#E3E6EB", "#F0F0F0")
    assert f.startswith("zero gap #E3E6EB->#F0F0F0 dL=-")


def test_ramp_failures_for_non_monotone_and_wide_hue():
    assert any("not decreasing" in f for f in check_ramp(["#B4A2EA", "#B4A2EA", "#5A3DB3", "#3B2585"]))
    assert any("hue span" in f for f in check_ramp(["#B4A2EA", "#8467D6", "#5A3DB3", "#7A2D3B"]))


def test_warn_set_changes_are_noticed():
    v = css_vars()
    same = light_palettes(v)[0]
    assert set(check_categorical("light", same)[1]) == KNOWN_WARNS
    moved = dict(same, sub="#1bb07b")
    assert set(check_categorical("light", moved)[1]) != KNOWN_WARNS
    washed = dict(light_palettes(v)[0], wait="#f0a35e")
    _, w2 = check_categorical("light", washed)
    assert set(w2) != KNOWN_WARNS


def test_close_or_cvd_confusable_colours_fail():
    f, _ = check_categorical("light", {"a": "#2a78d6", "b": "#2a7ad8", "c": "#eb6834"})
    assert [x.rsplit(" ", 1)[0] for x in f] == ["normal a/b", "cvd a/b"]
    # far apart to normal vision, confusable only under colour-vision deficiency
    f, _ = check_categorical("light", {"done": "#008300", "stuck": "#a85600"})
    assert [x.rsplit(" ", 1)[0] for x in f] == ["cvd done/stuck"]


def test_encoding_fails_without_stuck_shape_or_hatch_or_with_inline_fill():
    home, proj, term = render_home(), render_projects(), render_terminal()
    flat = re.sub(r'(task-stuck"[^>]*?) y="0"([^>]*?) height="12"', r'\1 y="2"\2 height="8"', home)
    assert flat != home
    assert any("stuck cell" in f for f in check_encoding(flat, proj, term))
    nohatch = home.replace('fill="url(#hatch-wait-now)"', "")
    assert nohatch != home
    assert any("hatch" in f for f in check_encoding(nohatch, proj, term))
    assert any("inline fill" in f for f in check_encoding(home + '<rect fill="#123456"/>', proj, term))
    assert any("stuck glyph" in f for f in check_encoding(home, proj, dict(term, stuck=term["doing"])))
    assert any("wait_now glyph" in f for f in check_encoding(home, proj, dict(term, wait_now=term["wait"])))
