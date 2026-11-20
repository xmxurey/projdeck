"""Independent spec tests for T18: zellij_in_scope lives in config; board and report do not
import sync; cli does not touch ui's private names. Behaviour is unchanged."""
import ast
import os

import pytest

from projdeck import board, cli, config, report, sync

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "projdeck")
NOW = 1795000000


def parse(name):
    with open(os.path.join(PKG, name), encoding="utf-8") as f:
        return ast.parse(f.read())


def sync_imports(tree):
    """Every import statement that pulls in the sync module, in any spelling."""
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[-1] == "sync" and a.name.split(".")[0] in ("projdeck", "sync"):
                    bad.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")
            if mod[-1] == "sync" and (node.level > 0 or mod[0] in ("projdeck", "sync")):
                bad.append("from %s%s" % ("." * node.level, node.module))
            elif node.level > 0 or mod[0] == "projdeck":
                if not node.module or mod == ["projdeck"]:
                    bad.extend("from %s import %s" % ("." * node.level + (node.module or ""), a.name)
                               for a in node.names if a.name == "sync")
    return bad


def ui_private_access(tree):
    aliases = {"ui"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module in (None, "projdeck") or node.level):
            for a in node.names:
                if a.name == "ui":
                    aliases.add(a.asname or "ui")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "projdeck.ui" and a.asname:
                    aliases.add(a.asname)
    return [node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr.startswith("_")
            and isinstance(node.value, ast.Name) and node.value.id in aliases]


# ---- the checkers themselves: bad spellings are caught, good ones stay quiet

@pytest.mark.parametrize("src", [
    "from projdeck import sync",
    "import projdeck.sync",
    "from . import sync",
    "from .sync import live_request",
    "from projdeck.sync import live_request",
    "from projdeck import model, sync as s",
    "import sync",
    "def f():\n    from . import sync\n",
])
def test_sync_import_checker_catches_every_spelling(src):
    assert sync_imports(ast.parse(src))


@pytest.mark.parametrize("src", [
    "from projdeck import model",
    "from . import config, termtext",
    "from .model import sync_age",
    "x = model.sync_age(1)\nsync_age = 3",
    "import os",
])
def test_sync_import_checker_is_quiet_on_clean_code(src):
    assert sync_imports(ast.parse(src)) == []


def test_ui_checker_catches_private_access_and_ignores_public():
    assert ui_private_access(ast.parse("from projdeck import ui\nui._one_line(x)"))
    assert ui_private_access(ast.parse("from . import ui as u\nu._x")) == ["_x"]
    assert ui_private_access(ast.parse("from projdeck import ui\nui.render(x)\nself._y")) == []


# ---- acceptance 1 and 2

@pytest.mark.parametrize("name", ["board.py", "report.py"])
def test_board_and_report_do_not_import_sync(name):
    assert sync_imports(parse(name)) == []


def test_cli_does_not_use_ui_one_line():
    assert "_one_line" not in ui_private_access(parse("cli.py"))


# ---- zellij_in_scope lives in config only

def test_config_has_callable_zellij_in_scope():
    assert callable(config.zellij_in_scope)


def test_sync_namespace_does_not_have_zellij_in_scope():
    assert not hasattr(sync, "zellij_in_scope")


# ---- return values follow the README

LOCAL_Z = {"name": "s-host", "local": True, "mux": "zellij"}
REMOTE_Z = {"name": "em-host", "local": False, "ssh": "em-host", "mux": "zellij"}
LOCAL_TMUX = {"name": "s-host", "local": True, "mux": "tmux"}
LOCAL_OFF = {"name": "s-host", "local": True, "mux": False}
REMOTE_OFF = {"name": "em-host", "local": False, "ssh": "em-host", "mux": False}


def test_local_host_with_zellij_is_in_scope():
    assert config.zellij_in_scope(LOCAL_Z) is True


@pytest.mark.parametrize("host", [REMOTE_Z, REMOTE_OFF])
def test_remote_host_is_not_in_scope(host):
    assert not config.zellij_in_scope(host)


@pytest.mark.parametrize("host", [LOCAL_OFF, REMOTE_OFF])
def test_mux_false_is_not_in_scope(host):
    assert not config.zellij_in_scope(host)


def test_tmux_and_default_mux_are_not_in_scope():
    assert not config.zellij_in_scope(LOCAL_TMUX)
    assert not config.zellij_in_scope({"name": "s-host", "local": True})


def test_scope_does_not_mutate_its_argument():
    h = dict(LOCAL_Z)
    config.zellij_in_scope(h)
    assert h == LOCAL_Z


# ---- every caller really goes through config.zellij_in_scope

class Boom(Exception):
    pass


def boom(*a, **k):
    raise Boom("zellij_in_scope was called")


def _cache():
    return {"schema": 1, "host": "s-host", "collected_at": NOW - 60, "synced_at": NOW - 60,
            "stale_since": None, "last_error": None, "projects": []}


def _live():
    return {"schema": 1, "host": "s-host", "collected_at": NOW - 10, "source": "claude agents",
            "since": NOW - 86400, "sessions": [], "projects": {}, "zellij_exited": ["proj-a"]}


CFG = {"hosts": [LOCAL_Z], "tiers_days": {"active": 14, "stale": 90}}


def test_board_build_calls_config_zellij_in_scope(monkeypatch):
    monkeypatch.setattr(config, "zellij_in_scope", boom)
    with pytest.raises(Boom):
        board.build([_cache()], [_live()], CFG, NOW)


def test_report_render_calls_config_zellij_in_scope(monkeypatch):
    from projdeck import model
    caches = [_cache()]
    rows = model.rows(caches, CFG, NOW)
    lives = [_live()]
    b = board.build(caches, lives, CFG, NOW)  # built with the real function first
    monkeypatch.setattr(config, "zellij_in_scope", boom)
    with pytest.raises(Boom):
        report.render(rows, caches, NOW, board=b, lives=lives, cfg=CFG)


def test_cli_open_project_calls_config_zellij_in_scope(monkeypatch):
    monkeypatch.setattr(config, "zellij_in_scope", boom)
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
    monkeypatch.setattr(cli.opener, "open_command", lambda *a, **k: "inline")
    monkeypatch.setattr(cli.opener, "has_iterm", lambda: False)
    monkeypatch.setattr(cli, "log_event", lambda *a, **k: None)
    r = {"host": "s-host", "path": "/home/u/p", "local": True, "ssh": None, "mux": "zellij"}
    with pytest.raises(Boom):
        cli.open_project([r], r)


def test_no_caller_module_binds_zellij_in_scope_by_name():
    for mod in (board, report, cli, sync):
        assert not hasattr(mod, "zellij_in_scope"), mod.__name__
