import json
import os
import re

import pytest

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "mockup_terminal.json")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# repo-relative: the run ledger's design.md, present wherever the ledger is checked out
DESIGN = os.path.join(ROOT, "docs", "dev", "runs", "2026-11-18-proj-dashboard", "design.md")
KINDS = {"w", "live", "s", "b", "n"}


@pytest.fixture(scope="module")
def fx():
    with open(FIX) as f:
        return json.load(f)


def cells(segs, frm, n, step, order, thr):
    out = []
    for i in range(n):
        a = frm + i * step
        z = a + step
        acc = {"b": 0, "s": 0, "w": 0, "live": 0}
        for k, s, e in segs:
            o = min(z, e) - max(a, s)
            if o > 0:
                acc[k] += o
        best, v = "n", thr
        for k in order:
            if acc[k] > v:
                best, v = k, acc[k]
        out.append(best)
    return out


def test_valid_json_and_keys(fx):
    for k in ("unit", "now", "threshold", "order", "map", "segs", "expected"):
        assert k in fx
    assert fx["unit"] == "minute"
    assert fx["now"] == 517
    assert fx["threshold"] == 0.5
    assert fx["order"] == ["w", "live", "s", "b"]
    assert fx["map"] == {"b": "busy", "s": "sub", "w": "wait", "live": "wait_now", "n": None}
    assert set(fx["segs"]) == {"qp", "pc"}
    assert set(fx["expected"]) == {"qp_24", "pc_24", "pc_48"}


def test_expected_lengths_and_values(fx):
    e = fx["expected"]
    assert len(e["qp_24"]) == 24
    assert len(e["pc_24"]) == 24
    assert len(e["pc_48"]) == 48
    for name, arr in e.items():
        assert set(arr) <= KINDS, name


def test_segs_wellformed(fx):
    assert len(fx["segs"]["qp"]) == 7
    assert len(fx["segs"]["pc"]) == 27
    for name, segs in fx["segs"].items():
        for k, s, e in segs:
            assert k in {"w", "live", "s", "b"}
            assert s < e <= fx["now"], (name, k, s, e)


def _recompute(fx, name, n, span):
    now = fx["now"]
    return cells(fx["segs"][name], now - span, n, 7.5, fx["order"], fx["threshold"])


def test_recompute_qp_24(fx):
    assert _recompute(fx, "qp", 24, 180) == fx["expected"]["qp_24"]


def test_recompute_pc_24(fx):
    assert _recompute(fx, "pc", 24, 180) == fx["expected"]["pc_24"]


def test_recompute_pc_48(fx):
    assert _recompute(fx, "pc", 48, 360) == fx["expected"]["pc_48"]


def test_hand_checked_spot_values(fx):
    # independent facts: last 157 minutes of qp are live; qp wait 305-360 dominates earlier cells
    e = fx["expected"]
    assert e["qp_24"][-1] == "live"
    assert e["pc_24"][-1] == "b"
    assert e["pc_48"][0] == "n" and e["pc_48"][-1] == "b"
    # pc_48 cell 13 covers [277.0+..]: now-360=157 -> cell13 = [254.5,262) -> w 253-256 and 257-260 = 1.5+3=4.5 wait
    assert e["pc_48"][13] == "w"


def test_tie_prefers_earlier_order():
    segs = [["b", 0, 5], ["s", 0, 5]]
    assert cells(segs, 0, 1, 10, ["w", "live", "s", "b"], 0.5) == ["s"]
    assert cells([["b", 0, 0.5]], 0, 1, 10, ["w", "live", "s", "b"], 0.5) == ["n"]


def _design_block(label_regex):
    with open(DESIGN) as f:
        text = f.read()
    return text[text.index("## 附录 A"):]


@pytest.mark.skipif(not os.path.exists(DESIGN),
                    reason="%s is missing: the run ledger (docs/dev/runs/) is not in this checkout, "
                           "so the fixture cannot be compared with design appendix A"
                           % os.path.relpath(DESIGN, ROOT))
def test_matches_design_appendix_verbatim(fx):
    app = _design_block(None)
    for name in ("qp", "pc"):
        start = app.index("%s = [[" % name) + len(name) + 3
        end = app.index("]]", start) + 2
        lit = json.loads(app[start:end])
        assert lit == fx["segs"][name], name
    for key, hdr in (("qp_24", "cells(qp, NOW-180, 24, 7.5)"),
                     ("pc_24", "cells(pc, NOW-180, 24, 7.5)"),
                     ("pc_48", "cells(pc, NOW-360, 48, 7.5)")):
        i = app.index(hdr)
        block = app[app.index("```", i) + 3:]
        block = block[:block.index("```")]
        assert block.split() == fx["expected"][key], key
