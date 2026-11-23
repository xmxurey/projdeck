"""Independent spec tests for T12: the docs for D6 (palette) and D7 (brief display) must say
what the brief says and point at things that exist."""
import json
import os
import re

import pytest

from tests.test_readme_claims_spec import (ROOT, build_cfg, good_cfg, incompat_bullets,
                                           readme, run_proj)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def section(text, start_re, end_re):
    m = re.search(start_re, text, re.M)
    assert m, "section %r not found" % start_re
    rest = text[m.end():]
    e = re.search(end_re, rest, re.M)
    return rest[:e.start()] if e else rest


def per_host_item():
    text = readme()
    m = re.search(r"^- per host:.*?(?=\n\n)", text, re.S | re.M)
    assert m, "README has no 'per host' key list"
    return " ".join(m.group(0).split())


def legend():
    return section(readme(), r"^## Reading the dashboard\s*$", r"^## ")


# ---------- 1. README per-host key `brief` ----------

def test_per_host_list_names_brief_boolean_default_false():
    item = per_host_item()
    m = re.search(r"`brief` \(`false`;([^)]*)\)", item)
    assert m, item
    assert "boolean" in m.group(1)
    assert "brief" in item.split("`brief`", 1)[1]  # says what it does


def test_brief_not_in_incompat_list():
    assert not [b for b in incompat_bullets() if "brief" in b]


def test_readme_example_host_is_only_em_host():
    text = readme()
    seg = text[text.index("`brief` (`false`"):]
    assert "em-host" in legend()
    # the only host name in brief-related prose is the fictional one
    for name in re.findall(r"`([a-z0-9]+-host)[ `]", legend()):
        assert name in ("em-host", "s-host")


@pytest.mark.parametrize("val", [True, False])
def test_brief_boolean_accepted(tmp_path, val):
    p = run_proj(tmp_path, build_cfg(tmp_path, "host", {"brief": val}), "report")
    assert p.returncode == 0, p.stderr


def test_brief_default_is_accepted_when_absent(tmp_path):
    assert run_proj(tmp_path, good_cfg(tmp_path), "report").returncode == 0


@pytest.mark.parametrize("val", [None, "yes", 1, 0, [], "true"])
def test_brief_non_boolean_rejected_exit_2_named_not_echoed(tmp_path, val):
    p = run_proj(tmp_path, build_cfg(tmp_path, "host", {"brief": val}), "report")
    assert p.returncode == 2, p.stderr
    assert "hosts[0].brief" in p.stderr
    assert "Traceback" not in p.stderr
    if isinstance(val, str):
        assert "'%s'" % val not in p.stderr and '"%s"' % val not in p.stderr


def test_readme_says_null_refused_matches_behaviour(tmp_path):
    assert "`null`" in per_host_item()
    p = run_proj(tmp_path, build_cfg(tmp_path, "host", {"brief": None}), "report")
    assert p.returncode == 2


# ---------- 2. README legend ----------

@pytest.mark.parametrize("frag", ["▆", "▒", "■", "▣", "!", "!T13", "brief:"])
def test_legend_mentions_symbol(frag):
    assert frag in legend(), frag


def test_legend_summary_line_and_preview():
    lg = legend()
    assert re.search(r"summary line", lg)
    assert re.search(r"cursor on the summary line", lg)
    assert "preview" in lg


def test_legend_brief_field_matches_code():
    from projdeck import ui
    assert "`%s`" % ui.BRIEF_FIELD in legend()


def test_legend_summary_example_matches_brief_label_shape():
    m = re.search(r"\(`([^`]*进行中[^`]*)`\)", legend())
    assert m
    assert re.fullmatch(r"em-host · 进行中 \d+ · 空闲 \d+", m.group(1))


def test_legend_stuck_marker_text_in_code():
    from projdeck import ui
    assert "⚠ 状态读取失败" in legend()
    assert "⚠ 状态读取失败" in open(os.path.join(ROOT, "projdeck/ui.py"),
                                           encoding="utf-8").read()
    assert ui.BRIEF_FIELD == "brief:"


# ---------- 3. R-007 code anchors, backlog ----------

def r007():
    t = read("docs/rulings.md")
    return section(t, r"^## R-007 ", r"^## R-\d+ ")


def test_r007_anchor_not_placeholder():
    s = r007()
    assert "待 run 实现时补上" not in s
    assert "代码落点" in s


@pytest.mark.parametrize("path,names", [
    ("projdeck/board.py", ["brief_fold", "folded", "session_error", "brief_label"]),
    ("projdeck/agent.py", ["state_error"]),
    ("projdeck/web.py", []),
    ("projdeck/ui.py", []),
])
def test_r007_lists_anchor_and_it_exists(path, names):
    s = r007()
    assert path in s, path
    code = read(path)
    for n in names:
        assert n in s, n
        assert n in code, (path, n)


def test_r007_names_resolve_in_board_module():
    from projdeck import board
    s = r007()
    for n in ["brief_fold", "folded", "session_error", "brief_label"]:
        assert callable(getattr(board, n)) and n in s


def test_backlog_dropped_palette_and_brief_items():
    t = read("docs/backlog.md")
    assert "R-007" not in t
    assert "看板精简展示实现主机" not in t
    assert "进度条和时间线换用区分度更高的颜色" not in t
    assert "配色校验" not in t
    m = re.search(r"^## 看板与终端\s*$", t, re.M)
    if m:  # if the heading survives it must not be empty
        body = re.split(r"^## ", t[m.end():], maxsplit=1, flags=re.M)[0]
        assert body.strip()
    # other items untouched
    assert "64 KiB" in t and "M-7" in t and "AppleScript" in t


def test_backlog_other_sections_kept():
    t = read("docs/backlog.md")
    assert "命令：" in t and "proj_session_name" in t


# ---------- 4. guards.md ----------

def guard_row(g):
    for line in read("docs/dev/guards.md").splitlines():
        if line.startswith("| %s " % g):
            return line
    pytest.fail("guards.md has no row for %s" % g)


def test_g15_row():
    row = guard_row("G15")
    assert "tests/test_palette.py" in row
    for frag in ["0.1", "15", "6", "3.0"]:
        assert frag in row, frag
    assert os.path.isfile(os.path.join(ROOT, "tests/test_palette.py"))


def test_g16_row():
    row = guard_row("G16")
    assert "tests/test_one_definition.py" in row and "tests/test_brief_chain.py" in row
    assert "board.folded" in row
    for f in ["tests/test_one_definition.py", "tests/test_brief_chain.py"]:
        assert os.path.isfile(os.path.join(ROOT, f))


def test_guard_rows_cite_existing_test_functions():
    for g in ("G15", "G16"):
        row = guard_row(g)
        for name in re.findall(r"`(test_\w+)`", row):
            hit = False
            for fn in os.listdir(os.path.join(ROOT, "tests")):
                if fn.endswith(".py") and "def %s(" % name in read("tests/" + fn):
                    hit = True
            assert hit, (g, name)


def test_guards_table_rows_have_same_column_count():
    rows = [l for l in read("docs/dev/guards.md").splitlines() if re.match(r"\| G\d+ ", l)]
    ids = [re.match(r"\| (G\d+) ", r).group(1) for r in rows]
    assert "G15" in ids and "G16" in ids and len(ids) == len(set(ids))
    counts = {len(re.split(r"(?<!\\)\|", r)) for r in rows}
    assert len(counts) == 1, counts
