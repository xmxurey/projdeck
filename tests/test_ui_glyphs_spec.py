"""Independent spec tests for T04: terminal glyphs (wait_now '▒', stuck '!').
Expected values are literals from the brief."""
import itertools
import re

from projdeck import ui

SGR = re.compile(r"\x1b\[([0-9;]*)m")
FG = re.compile(r"38;2;(\d+);(\d+);(\d+)")
WAIT, BUSY, STUCK = (217, 89, 38), (57, 135, 229), (230, 103, 103)
KINDS = ["busy", "sub", "wait", "wait_now", None]


def fg_per_char(raw):
    out, cur, pos = [], None, 0
    for m in SGR.finditer(raw):
        for ch in raw[pos:m.start()]:
            out.append((ch, cur))
        mm = FG.search(m.group(1))
        if mm:
            cur = tuple(int(x) for x in mm.groups())
        elif m.group(1) in ("", "0"):
            cur = None
        pos = m.end()
    for ch in raw[pos:]:
        out.append((ch, cur))
    return out


def tasks(statuses):
    return [{"id": "T%02d" % i, "title": "t", "status": s} for i, s in enumerate(statuses, 1)]


# ---- AC1
def test_cell_glyph_constant():
    assert ui.CELL_GLYPH == {"wait_now": "▒"}


def test_spark_wait_then_wait_now():
    out = ui.spark(["wait", "wait_now"])
    assert ui.strip_ansi(out) == "▆▒"
    assert [c for _, c in fg_per_char(out)] == [WAIT, WAIT]


def test_spark_one_char_per_cell_any_length():
    for n in (24, 48):
        cells = [KINDS[i % 5] for i in range(n)]
        assert len(ui.strip_ansi(ui.spark(cells))) == n
        cells = list(itertools.islice(itertools.cycle(["wait_now", "wait"]), n))
        assert len(ui.strip_ansi(ui.spark(cells))) == n
    assert len(ui.strip_ansi(ui.spark(["wait_now"] * 24))) == 24


def test_spark_wait_now_only_glyph_differs():
    plain = ui.strip_ansi(ui.spark(["busy", "sub", "wait", "wait_now", "wait_now", "wait"]))
    assert plain.count("▒") == 2
    assert "▒" not in ui.strip_ansi(ui.spark(["busy", "sub", "wait"]))


# ---- AC2
def test_task_bar_stuck_cell_is_red_bang_not_block():
    st = ["done", "stuck", "doing"] + ["todo"] * 5
    out = ui.task_bar(tasks(st))
    plain = ui.strip_ansi(out)
    assert plain.startswith("■!▣")
    chars = fg_per_char(out)
    assert chars[1] == ("!", STUCK)
    assert chars[2] == ("▣", BUSY)
    assert "▣" in plain and plain.count("!") == 1


def test_task_bar_no_stuck_has_no_bang():
    out = ui.strip_ansi(ui.task_bar(tasks(["done", "doing"] + ["todo"] * 6)))
    assert "!" not in out and out.startswith("■▣")


# ---- AC3
def test_long_form_current_stuck():
    st = ["done"] * 12 + ["stuck"] + ["todo"] * 8
    out = ui.task_bar({"items": tasks(st), "current": "T13"})
    assert ui.strip_ansi(out) == "12/21 !T13"
    i = ui.strip_ansi(out).index("!")
    assert fg_per_char(out)[i] == ("!", STUCK)


def test_long_form_bare_list_stuck_only_has_no_label():
    st = ["done"] * 12 + ["stuck"] + ["todo"] * 8
    assert ui.strip_ansi(ui.task_bar(tasks(st))) == "12/21"


def test_long_form_current_doing():
    st = ["done"] * 12 + ["doing"] + ["todo"] * 8
    out = ui.task_bar(tasks(st))
    assert ui.strip_ansi(out) == "12/21 ▣T13"
    i = ui.strip_ansi(out).index("▣")
    assert fg_per_char(out)[i] == ("▣", BUSY)


# ---- AC4: the cell parser of the existing tests must count and colour '▒'
def _cells(raw):
    return [(ch, c) for ch, c in fg_per_char(raw) if ch in "▆▒"]


def test_parser_counts_wait_now_in_24_cells():
    cells = ["busy"] * 6 + ["wait_now"] * 4 + ["wait"] * 4 + [None] * 4 + ["sub"] * 6
    out = ui.spark(cells)
    parsed = _cells(out)
    assert len(parsed) == 24
    assert [c for ch, c in parsed[6:10]] == [WAIT] * 4
    assert [ch for ch, _ in parsed[6:10]] == ["▒"] * 4
