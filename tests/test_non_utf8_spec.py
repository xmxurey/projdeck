"""T02 spec tests: surrogate / bidi / zero-width handling at the terminal and web exits."""
import json

import pytest

from projdeck import board, model, report, termtext
from tests.test_web_serve import NOW, make_board, request, running, sess  # noqa: F401

BAD_CHARS = ["\udcff", "\ud800", "\udfff", "؜", "​", "‎", "‏",
             "‪", "‫", "‬", "‭", "‮",
             "⁦", "⁧", "⁨", "⁩", "﻿"]
FFFD = "�"


def clean(text):
    return not any(c in text for c in BAD_CHARS)


# ---- acceptance 2: termtext

def test_no_surrogates_replaces_whole_range():
    assert termtext.no_surrogates("a\ud800b\udcffc\udfffd") == "a�b�c�d"


def test_no_surrogates_leaves_clean_text_alone():
    s = "plain 中文 \U0001f600 ‍ ok"
    assert termtext.no_surrogates(s) == s
    assert termtext.no_surrogates("") == ""


def test_no_surrogates_result_encodes_strictly():
    termtext.no_surrogates("x\udcffy\ud800").encode("utf-8")


@pytest.mark.parametrize("ch", BAD_CHARS)
@pytest.mark.parametrize("fn", [termtext.safe, termtext.one_line])
def test_each_bad_char_removed(fn, ch):
    out = fn("a" + ch + "b")
    assert ch not in out
    assert out.startswith("a") and out.endswith("b")
    out.encode("utf-8")


@pytest.mark.parametrize("fn", [termtext.safe, termtext.one_line])
def test_all_bad_chars_together(fn):
    out = fn("x" + "".join(BAD_CHARS) + "y")
    assert clean(out)
    out.encode("utf-8")


@pytest.mark.parametrize("fn", [termtext.safe, termtext.one_line])
def test_surrogate_becomes_fffd(fn):
    assert FFFD in fn("/home/u/\udcff")


@pytest.mark.parametrize("fn", [termtext.safe, termtext.one_line])
def test_zwj_kept_and_clean_text_unchanged(fn):
    assert fn("a‍b") == "a‍b"
    assert fn("/home/u/proj 中文") == "/home/u/proj 中文"
    assert fn("") == ""


def test_safe_still_strips_escape_sequences():
    out = termtext.safe("a\x1b]52;c;QQ==\x07b\x1b[31mc")
    assert "\x1b" not in out and "\x07" not in out


@pytest.mark.parametrize("ch", BAD_CHARS)
def test_has_control_true_for_bad_chars(ch):
    assert termtext.has_control("name" + ch + "x") is True


def test_has_control_quiet_on_good_text():
    assert not termtext.has_control("/home/u/proj-1 中文")
    assert not termtext.has_control("a‍b")
    assert not termtext.has_control("")


# ---- acceptance 3: report.render

def _caches(path):
    return [{"host": "s-host", "synced_at": NOW - 5, "stale_since": None, "last_error": None,
             "projects": [{"path": path, "exists": True, "last_mtime": NOW - 100, "sessions": [],
                           "git": {"branch": "m", "dirty": 1, "ahead": 0, "no_upstream": True,
                                   "last_commit_ts": NOW - 50}, "errors": [],
                           "suspicious": ["f\udcff.log"]}]}]


CFG = {"hosts": [{"name": "s-host", "local": True}], "tiers_days": {"active": 14, "stale": 90}}


def test_report_render_encodes_strictly():
    caches = _caches("/home/u/\udcffdir")
    rows = model.rows(caches, CFG, NOW)
    events = [{"ts": NOW, "action": "open", "host": "s-host", "path": "/home/u/\udcffdir"}]
    md = report.render(rows, caches, NOW, events=events,
                       board=board.build(caches, [], CFG, NOW), lives=[], cfg=CFG)
    md.encode("utf-8")
    assert clean(md)
    assert "/home/u/" in md


def test_report_render_clean_path_unchanged():
    caches = _caches("/home/u/okdir")
    md = report.render(model.rows(caches, CFG, NOW), caches, NOW,
                       board=board.build(caches, [], CFG, NOW), lives=[], cfg=CFG)
    assert "/home/u/okdir" in md


# ---- acceptance 1: web

BAD_PATH = "/home/u/bad\udcffdir"


def bad_board():
    caches = [{"host": "s-host", "synced_at": NOW, "collected_at": NOW, "stale_since": None,
               "last_error": None, "projects": _caches(BAD_PATH)[0]["projects"]}]
    b = board.build(caches, [], CFG, NOW)
    return b


def bad_board_with_session():
    b = make_board()
    b["waiting"] = [sess("a", "waiting", project=BAD_PATH, name="n\udcffx", question="q\ud800")]
    return b


@pytest.mark.parametrize("make", [bad_board, bad_board_with_session])
@pytest.mark.parametrize("path", ["/", "/projects", "/api/state.json", "/fragment/home",
                                  "/fragment/projects"])
def test_web_routes_survive_surrogate_paths(running, make, path):
    port, _ = running(lambda: make())
    st, hd, body = request(port, path)
    assert st == 200
    text = body.decode("utf-8")  # strict: body must be valid utf-8
    assert not any(0xD800 <= ord(c) <= 0xDFFF for c in text)


def test_web_replacement_char_shows_in_body(running):
    port, _ = running(lambda: bad_board_with_session())
    body = request(port, "/")[2].decode("utf-8")
    assert "bad" + FFFD + "dir" in body


def test_web_projects_page_shows_replacement_char(running):
    port, _ = running(lambda: bad_board())
    body = request(port, "/projects")[2].decode("utf-8")
    assert FFFD in body


def test_web_api_json_parses_and_has_replacement(running):
    port, _ = running(lambda: bad_board_with_session())
    st, _, body = request(port, "/api/state.json")
    assert st == 200
    data = json.loads(body.decode("utf-8"))
    assert FFFD in json.dumps(data, ensure_ascii=False)


def test_web_clean_path_unchanged(running):
    port, _ = running()
    for p in ("/", "/projects", "/api/state.json"):
        assert request(port, p)[0] == 200
    assert FFFD not in request(port, "/")[2].decode("utf-8")
