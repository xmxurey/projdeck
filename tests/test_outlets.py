"""T17: usage.jsonl and the remote status-line file are private (0600); the last terminal
outlets (clean preview, directory listing, serve log, tab titles) strip control characters."""
import os
import subprocess

import pytest

from projdeck import cli, opener, serve, sync

EVIL = "a\x1b]52;c;AAAA\x07b\nc"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    monkeypatch.setenv("PROJ_CACHE", str(d))
    return d


def mode(p):
    return os.stat(str(p)).st_mode & 0o777


# ---- permissions

def test_log_event_creates_usage_jsonl_0600_under_umask_002(cache):
    old = os.umask(0o002)
    try:
        cli.log_event("open", "s-host", "/home/u/p")
    finally:
        os.umask(old)
    assert mode(cache / "usage.jsonl") == 0o600


def test_log_event_tightens_an_existing_0664_file(cache):
    cache.mkdir()
    f = cache / "usage.jsonl"
    f.write_text("")
    os.chmod(str(f), 0o664)
    cli.log_event("open", "s-host", "/home/u/p")
    assert mode(f) == 0o600
    assert f.read_text().count("\n") == 1


def test_push_cmd_starts_with_umask_077():
    assert serve.PUSH_CMD.startswith("umask 077;")


def test_push_cmd_creates_a_0600_file_when_run_by_sh(tmp_path):
    home = tmp_path / "rhome"
    home.mkdir()
    env = dict(os.environ, HOME=str(home))
    subprocess.run(["sh", "-c", "umask 002; " + serve.PUSH_CMD], input=b"x", env=env, check=True)
    assert mode(home / ".cache/proj/live/statusline.state") == 0o600


# ---- log failures never break the tool

def test_symlinked_usage_jsonl_does_not_stop_enter_and_target_is_untouched(cache, tmp_path, monkeypatch):
    cache.mkdir()
    target = tmp_path / "elsewhere"
    target.write_text("keep")
    os.symlink(str(target), str(cache / "usage.jsonl"))
    monkeypatch.setattr(cli.opener, "open_command", lambda cmd, title, iterm=None: "inline")
    monkeypatch.setattr(cli.opener, "has_iterm", lambda: False)
    monkeypatch.setattr(cli.config, "zellij_in_scope", lambda r: False)
    r = {"host": "s-host", "path": "/home/u/p", "local": True, "ssh": None}
    assert cli.open_project([r], r) == ("inline", None)
    assert target.read_text() == "keep"


def test_attach_that_cannot_be_built_logs_nothing(monkeypatch):
    events = []
    monkeypatch.setattr(cli, "log_event", lambda *a, **k: events.append(a))
    monkeypatch.setattr(cli.config, "zellij_in_scope", lambda r: True)
    monkeypatch.setattr(cli.agent, "zellij_by_project",
                        lambda paths: {"/home/u/p": [{"name": "-x"}]})
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
    r = {"host": "s-host", "path": "/home/u/p", "local": True}
    out = cli.open_project([r], r)
    assert out[0] is None
    assert events == []


# ---- terminal outlets

def clean(text):
    return "\x1b" not in text and "\x07" not in text


def test_file_preview_strips_control_characters(tmp_path):
    f = tmp_path / "f"
    f.write_bytes(EVIL.encode())
    out = cli.file_preview({"local": True}, str(f))
    assert out == "ab\nc"  # the sequence is gone, the real text and its own lines stay


def test_directory_listing_has_no_control_chars_or_forged_lines(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / EVIL).write_text("")
    (d / "real").write_text("")
    out = cli.file_preview({"local": True}, str(d))
    assert clean(out)
    assert len(out.split("\n")) == 2


def test_serve_log_message_is_one_clean_line(capsys):
    serve._log("push to " + EVIL + " failed")
    err = capsys.readouterr().err
    assert clean(err) and err.count("\n") == 1


def test_zellij_tab_name_is_clean():
    seen = []
    opener.zellij_new_tab("/home/u/p", EVIL, run=lambda argv, **k: seen.append(argv))
    name = seen[0][seen[0].index("--name") + 1]
    assert clean(name) and "\n" not in name


def test_applescript_title_is_clean():
    script = opener.applescript("true", EVIL)
    assert clean(script)
    line = [l for l in script.split("\n") if "set name to" in l]
    assert len(line) == 1
    assert len(script.split("\n")) == len(opener.applescript("true", "ok").split("\n"))


def test_remote_directory_listing_script_has_one_clean_line_per_file(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a\nb").write_text("")
    (d / "x\x1b]52;c;AAAA\x07y").write_text("")
    script = cli.remote_preview_script(str(d))
    out = subprocess.run(["sh", "-c", script], stdout=subprocess.PIPE, check=True).stdout
    text = cli._show(out)
    assert len(text.splitlines()) == 2
    assert "\x1b" not in text


# ---- review round 1

def test_failed_usage_log_gives_one_notice_without_the_path(cache, tmp_path):
    cache.mkdir()
    os.symlink(str(tmp_path / "elsewhere"), str(cache / "usage.jsonl"))
    assert cli.log_event("open", "s-host", "/home/u/p") is False
    assert cli.log_event("open", "s-host", "/home/u/p") is False
    notes = cli.take_notices()
    assert len(notes) == 1 and "usage log" in notes[0] and "ELOOP" in notes[0]
    assert str(tmp_path) not in notes[0]


def test_enter_with_symlinked_usage_log_still_opens_and_notes_it(cache, tmp_path, monkeypatch):
    cache.mkdir()
    os.symlink(str(tmp_path / "elsewhere"), str(cache / "usage.jsonl"))
    monkeypatch.setattr(cli.opener, "open_command", lambda cmd, title, iterm=None: "inline")
    monkeypatch.setattr(cli.opener, "has_iterm", lambda: False)
    monkeypatch.setattr(cli.config, "zellij_in_scope", lambda r: False)
    r = {"host": "s-host", "path": "/home/u/p", "local": True, "ssh": None}
    assert cli.open_project([r], r) == ("inline", None)
    cli.open_project([r], r)
    assert [n for n in cli.take_notices() if "usage log" in n] != []


def test_browse_notice_is_one_line_even_with_a_newline_in_a_name(monkeypatch):
    seen = []

    def pick(rows, now, notice=None, select=None, board=None):
        seen.append(notice)
        raise cli.ui.Cancelled()

    monkeypatch.setattr(cli.ui, "pick_project", pick)
    monkeypatch.setattr(cli.model, "rows", lambda *a, **k: [{"host": "s-host"}])
    monkeypatch.setattr(cli.board, "build", lambda *a, **k: {})
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: {})
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: {})
    cli.cmd_browse({"warnings": ["directory no longer exists: /x/a\n" + EVIL]}, None)
    assert seen and "\n" not in seen[0] and clean(seen[0])


# ---- review round 2

def test_inline_open_in_browse_prints_the_queued_usage_notice_to_stderr(monkeypatch, capsys):
    r = {"host": "s-host", "path": "/home/u/p", "exists": True}
    monkeypatch.setattr(cli.ui, "pick_project", lambda *a, **k: (None, "s-host", "/home/u/p"))
    monkeypatch.setattr(cli.model, "rows", lambda *a, **k: [r])
    monkeypatch.setattr(cli.model, "find_row", lambda rows, h, p: r)
    monkeypatch.setattr(cli.board, "build", lambda *a, **k: {})
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: {})
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: {})

    def fake_open(rows, row):
        cli._note("usage log not written: ELOOP")
        return "inline", None

    monkeypatch.setattr(cli, "open_project", fake_open)
    monkeypatch.setattr(cli.config, "load_config", lambda: {})
    assert cli.main([]) == 0
    assert "usage log" in capsys.readouterr().err


def test_restore_prints_the_queued_usage_notice_to_stderr(monkeypatch, capsys):
    host = {"name": "s-host"}
    monkeypatch.setattr(cli, "_all_batches", lambda cfg, h: ([(host, {"id": "b"})], []))
    monkeypatch.setattr(cli, "_batch_line", lambda h, b: "s-host\tb\tx")
    monkeypatch.setattr(cli.ui, "pick_line", lambda *a, **k: "s-host\tb\tx")
    monkeypatch.setattr(cli.config, "host_by_name", lambda cfg, n: host)
    monkeypatch.setattr(cli.sync, "run_agent", lambda *a, **k: {"restored": [], "skipped": []})
    cli._note("usage log not written: ELOOP")
    monkeypatch.setattr(cli.config, "load_config", lambda: {"trash_dir": "t", "ssh_timeout": 1})
    assert cli.main(["restore"]) == 0
    assert "usage log" in capsys.readouterr().err


def test_report_says_when_usage_log_is_a_symlink(cache, tmp_path, monkeypatch, capsys):
    cache.mkdir()
    os.symlink(str(tmp_path / "elsewhere"), str(cache / "usage.jsonl"))
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg: {})
    monkeypatch.setattr(cli.sync, "load_lives", lambda cfg: {})
    monkeypatch.setattr(cli.board, "build", lambda *a, **k: {})
    monkeypatch.setattr(cli.model, "rows", lambda *a, **k: [])
    monkeypatch.setattr(cli.report, "render", lambda *a, **k: "REPORT\n")
    cli.cmd_report({}, None)
    out = capsys.readouterr().out
    assert "not being written" in out and str(tmp_path) not in out


def test_usage_failure_labels_are_accurate():
    assert cli._usage_failure(OSError()) == "OSError"
    assert cli._usage_failure(OSError("not a regular file: x")) == "not a regular file"
    assert cli._usage_failure(ValueError("embedded null")) == "ValueError"
    assert cli._usage_failure(UnicodeEncodeError("ascii", "é", 0, 1, "x")) == "not ASCII"


@pytest.mark.parametrize("cmd", ["sessions", "grep", "clean"])
def test_any_command_prints_a_queued_usage_notice_when_it_ends(monkeypatch, capsys, cmd):
    def fake(cfg, args):
        cli._note("usage log not written: ELOOP")
        return 0

    monkeypatch.setitem(cli.COMMANDS, cmd, fake)
    monkeypatch.setattr(cli.config, "load_config", lambda: {})
    argv = {"sessions": ["sessions"], "grep": ["grep", "x"], "clean": ["clean", "s-host"]}[cmd]
    assert cli.main(argv) == 0
    assert "usage log" in capsys.readouterr().err


def test_notice_already_shown_in_browse_is_not_printed_again_on_exit(monkeypatch, capsys):
    r = {"host": "s-host", "path": "/home/u/p", "exists": True}
    shown, calls = [], []

    def pick(rows, now, notice=None, select=None, board=None):
        shown.append(notice)
        calls.append(1)
        if len(calls) == 2:
            raise cli.ui.Cancelled()
        return "ctrl-v", None, None  # back to the list

    monkeypatch.setattr(cli.ui, "pick_project", pick)
    monkeypatch.setattr(cli.model, "rows", lambda *a, **k: [r])
    monkeypatch.setattr(cli.board, "build", lambda *a, **k: {})
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: {})
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: {})
    monkeypatch.setattr(cli, "open_dashboard", lambda cfg: cli._note("usage log not written: ELOOP"))
    monkeypatch.setattr(cli.config, "load_config", lambda: {})
    assert cli.main([]) == 0
    assert "usage log" in (shown[1] or "")
    assert "usage log" not in capsys.readouterr().err


def test_usage_log_problem_is_none_when_the_file_is_absent(cache, monkeypatch, capsys):
    assert cli.usage_log_problem() is None
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg: {})
    monkeypatch.setattr(cli.sync, "load_lives", lambda cfg: {})
    monkeypatch.setattr(cli.board, "build", lambda *a, **k: {})
    monkeypatch.setattr(cli.model, "rows", lambda *a, **k: [])
    monkeypatch.setattr(cli.report, "render", lambda *a, **k: "REPORT\n")
    cli.cmd_report({}, None)
    assert capsys.readouterr().out == "REPORT\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_report_warns_when_usage_log_is_not_writable(cache, monkeypatch, capsys):
    cache.mkdir()
    f = cache / "usage.jsonl"
    f.write_text("")
    os.chmod(str(f), 0o400)
    assert cli.usage_log_problem() == "not writable"
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg: {})
    monkeypatch.setattr(cli.sync, "load_lives", lambda cfg: {})
    monkeypatch.setattr(cli.board, "build", lambda *a, **k: {})
    monkeypatch.setattr(cli.model, "rows", lambda *a, **k: [])
    monkeypatch.setattr(cli.report, "render", lambda *a, **k: "REPORT\n")
    cli.cmd_report({}, None)
    assert "not being written" in capsys.readouterr().out


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
@pytest.mark.parametrize("missing_levels", [0, 2])
def test_usage_log_problem_names_an_unwritable_directory(cache, tmp_path, missing_levels, monkeypatch):
    """The report must warn exactly when the write would fail: the file does not exist yet but
    its directory (or the nearest one that exists) cannot be written."""
    locked = tmp_path / "locked"
    locked.mkdir()
    target = locked
    for i in range(missing_levels):
        target = target / ("sub%d" % i)
    monkeypatch.setenv("PROJ_CACHE", str(target))
    os.chmod(str(locked), 0o500)
    try:
        assert cli.log_event("open", "s-host", "/p") is False  # the write really fails
        assert cli.usage_log_problem() == "directory not writable"
    finally:
        os.chmod(str(locked), 0o700)
    assert cli.usage_log_problem() is None  # writable again: nothing to warn about


@pytest.mark.parametrize("bad_ts", ['"yesterday"', "null", "true", "[1]", '{"a": 1}'])
def test_read_events_skips_lines_whose_ts_is_not_a_number(cache, bad_ts):
    cache.mkdir()
    (cache / "usage.jsonl").write_text(
        '{"ts": 100, "action": "open"}\n'
        '{"ts": %s, "action": "bad"}\n'
        '{"action": "no-ts"}\n'
        '{"ts": 200.5, "action": "float"}\n' % bad_ts)
    assert [e["action"] for e in cli.read_events(50)] == ["open", "float"]
