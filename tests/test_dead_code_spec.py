"""Independent spec tests for T20: dead code removed, no stale noqa, web home check shared
with paths.rel_to_home, and an honest hint for sessions that cannot be attached."""
import importlib
import io
import os
import re
import tokenize

import pytest

from projdeck import paths, sync, ui, web

NOW = 1_800_000_000
EVIL = "<script>alert(1)</script>"


# ---------------------------------------------------------------- 1. removed names

def test_dead_names_are_gone():
    assert not hasattr(ui, "LIVE_STALE_SECONDS")
    assert not hasattr(web, "SEG_FILL")
    assert not hasattr(sync, "live_marker_path")


def test_live_marker_replacement_still_works():
    p = sync.marker_path(sync.LIVE_MARKER)
    assert os.path.basename(p) == ".syncing-live"
    assert os.path.basename(sync.marker_path(sync.LIVE_MARKER)) == os.path.basename(p)


# ---------------------------------------------------------------- 2. stale noqa

@pytest.mark.parametrize("modname", ["projdeck.report", "projdeck.web"])
def test_no_noqa_f401_comment(modname):
    mod = importlib.import_module(modname)
    with open(mod.__file__, "rb") as f:
        data = f.read()
    bad = 0
    for tok in tokenize.tokenize(io.BytesIO(data).readline):
        if tok.type == tokenize.COMMENT and re.search(r"noqa\s*:\s*F401", tok.string, re.I):
            bad += 1
    assert bad == 0


def test_tokenize_check_detects_a_noqa_comment():
    src = b"import os  # noqa: F401\n"
    hits = [t for t in tokenize.tokenize(io.BytesIO(src).readline)
            if t.type == tokenize.COMMENT and re.search(r"noqa\s*:\s*F401", t.string, re.I)]
    assert len(hits) == 1


def test_still_importable():
    from projdeck.report import UNSAFE_NAME
    from projdeck.web import span
    assert UNSAFE_NAME is not None
    assert callable(span)


# ---------------------------------------------------------------- 3. home check

def sess(sid="a", state="waiting", **kw):
    s = {"id": sid, "project": "/home/u/dev/p", "host": "s-host", "name": "name-" + sid,
         "title": "title-" + sid, "state": state, "status": state, "started_at": NOW - 7200,
         "idle_since": NOW - 60, "question": "q?", "last_reply": "", "last_human": None,
         "last_input": "human", "subagents_running": 0, "segments": [],
         "attach": "zellij attach z-" + sid, "tasks": None, "tasks_owner": None, "errors": []}
    s.update(kw)
    return s


def mkboard(waiting=(), busy=(), idle=()):
    return {"hosts": [], "summary": {}, "waiting": list(waiting), "busy": list(busy),
            "idle": list(idle), "ended_today": [], "stale": [], "stale_host_sessions": [],
            "lanes": {"start": NOW - 3 * 3600, "end": NOW, "rows": [], "more": 0},
            "top_todos": [], "backlogs": [], "tiles": []}


@pytest.fixture
def at_home(monkeypatch):
    """Set HOME, and re-import web so both a call-time and an import-time home are the same."""
    orig = os.environ.get("HOME")

    def setter(home):
        monkeypatch.setenv("HOME", home)
        importlib.reload(web)
    yield setter
    if orig is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = orig
    importlib.reload(web)


def shown(path):
    w = sess(project=path, name="nm", title="tt")
    return web.render_columns(mkboard(waiting=[w]), now=NOW)


def test_home_subdir_is_relative_and_matches_rel_to_home(at_home, tmp_path):
    home = str(tmp_path / "h")
    at_home(home)
    p = home + "/dev/deep/proj-x"
    assert paths.rel_to_home(p, home) == "dev/deep/proj-x"
    out = shown(p)
    assert "dev/deep/proj-x" in out
    assert home not in out


def test_trailing_slash_subdir(at_home, tmp_path):
    home = str(tmp_path / "h")
    at_home(home)
    out = shown(home + "/dev/proj-y/")
    assert "dev/proj-y" in out and home not in out


def test_home_itself_is_not_under_home(at_home, tmp_path):
    home = str(tmp_path / "h")
    at_home(home)
    assert paths.rel_to_home(home, home) is None
    out = web.project_name(home)
    assert isinstance(out, str) and out != ""
    assert not out.startswith("/") or out == home  # no bogus empty / relative form


def test_similar_prefix_home_is_not_stripped(at_home):
    at_home("/home/u")
    other = "/home/ux/dev/proj-z"
    assert paths.rel_to_home(other, "/home/u") is None
    name = web.project_name(other)
    assert name != "x/dev/proj-z"
    assert not name.startswith("x/")
    out = shown(other)
    assert "x/dev/proj-z" not in re.sub(r"/home/ux/dev/proj-z", "", out)


def test_similar_prefix_other_direction(at_home):
    at_home("/home/ux")
    p = "/home/u/dev/proj-z"
    assert paths.rel_to_home(p, "/home/ux") is None
    assert web.project_name(p) != p[len("/home/ux") + 1:]


def test_outside_home_path_keeps_its_tail(at_home, tmp_path):
    home = str(tmp_path / "h")
    at_home(home)
    p = "/srv/data/proj-q"
    assert paths.rel_to_home(p, home) is None
    name = web.project_name(p)
    assert name.endswith("proj-q")
    assert "proj-q" in shown(p)


def test_empty_home_matches_nothing(at_home):
    at_home("")
    assert paths.rel_to_home("/dev/p", "") is None
    assert "p" in web.project_name("/dev/p")


def test_surrogate_path_renders_without_error(at_home, tmp_path):
    home = str(tmp_path / "h")
    at_home(home)
    for p in (home + "/dev/b\udcffd", "/srv/b\udcffd"):
        out = shown(p)
        assert isinstance(out, str)  # no exception; whether the output is encodable is a spec gap
        assert isinstance(web.project_name(p), str)


def test_project_name_html_escaped_in_card(at_home, tmp_path):
    home = str(tmp_path / "h")
    at_home(home)
    out = shown(home + "/dev/" + EVIL)
    assert "<script" not in out.lower()


# ---------------------------------------------------------------- 4. attach hint

MANUAL = "手动接入"


def test_empty_attach_shows_manual_hint_in_waiting_and_busy():
    for state, key in (("waiting", "waiting"), ("busy", "busy")):
        s = sess(state=state, attach="")
        out = web.render_columns(mkboard(**{key: [s]}), now=NOW)
        assert MANUAL in out, state


def test_hint_present_with_attach_absent():
    s = sess(attach="")
    assert MANUAL in web.render_columns(mkboard(waiting=[s]), now=NOW)


def test_real_attach_has_no_manual_hint():
    s = sess(attach="zellij attach z-a")
    out = web.render_columns(mkboard(waiting=[s]), now=NOW)
    assert MANUAL not in out
    assert "zellij attach z-a" in out


def test_hint_card_is_escaped_with_script_in_name_and_title():
    s = sess(attach="", name=EVIL, title=EVIL, question=EVIL)
    out = web.render_columns(mkboard(waiting=[s]), now=NOW)
    assert MANUAL in out
    assert "<script" not in out.lower()
    assert "&lt;script&gt;" in out


def test_leading_dash_session_name_gets_hint():
    s = sess(attach="", name="-weird", title="t")
    out = web.render_columns(mkboard(waiting=[s]), now=NOW)
    assert MANUAL in out
    assert "-weird" in out
