"""T17 spec tests: usage.jsonl / remote statusline file permissions, and terminal outlets
that must not carry control characters or forged newlines."""
import json
import os
import stat
import subprocess

import pytest

from projdeck import cli, opener, serve, sync

EVIL = "\x1b]52;c;AAAA\x07"


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def umask002():
    old = os.umask(0o002)
    yield
    os.umask(old)


def usage_path():
    return os.path.join(sync.cache_dir(), "usage.jsonl")


# ---------------------------------------------------------------- 1: usage.jsonl 0600
def test_new_usage_log_is_0600_under_umask_002(umask002):
    assert not os.path.exists(usage_path())
    cli.log_event("open", "s-host", "/home/u/p")
    assert mode(usage_path()) == 0o600
    assert len(open(usage_path()).read().splitlines()) == 1


def test_existing_0664_usage_log_is_tightened_after_one_write(umask002):
    os.makedirs(sync.cache_dir(), exist_ok=True)
    with open(usage_path(), "w") as f:
        f.write('{"old": 1}\n')
    os.chmod(usage_path(), 0o664)
    cli.log_event("open", "s-host", "/home/u/p")
    assert mode(usage_path()) == 0o600
    lines = open(usage_path()).read().splitlines()
    assert lines[0] == '{"old": 1}' and len(lines) == 2  # appended, not truncated


def test_second_write_keeps_0600_and_appends(umask002):
    cli.log_event("open", "s-host", "/a")
    cli.log_event("resume", "s-host", "/b")
    assert mode(usage_path()) == 0o600
    assert len(open(usage_path()).read().splitlines()) == 2


# ---------------------------------------------------------------- 2: PUSH_CMD
def test_push_cmd_starts_with_umask_077():
    assert serve.PUSH_CMD.startswith("umask 077;")


def test_push_cmd_run_by_sh_writes_0600_even_under_umask_000(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    old = os.umask(0)
    try:
        r = subprocess.run(["sh", "-c", serve.PUSH_CMD], input=b'{"waiting": []}',
                           env=dict(os.environ, HOME=str(home)), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
    finally:
        os.umask(old)
    assert r.returncode == 0, r.stderr
    state = home / ".cache" / "proj" / "live" / "statusline.state"
    assert state.read_bytes() == b'{"waiting": []}'
    assert mode(str(state)) == 0o600
    left = [n for n in os.listdir(str(state.parent)) if n != "statusline.state"]
    assert left == []  # temp file was renamed away


def test_push_cmd_replaces_a_world_readable_old_state_with_0600(tmp_path):
    live = tmp_path / ".cache" / "proj" / "live"
    live.mkdir(parents=True)
    (live / "statusline.state").write_text("old")
    os.chmod(str(live / "statusline.state"), 0o644)
    r = subprocess.run(["sh", "-c", serve.PUSH_CMD], input=b"new",
                       env=dict(os.environ, HOME=str(tmp_path)), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    assert r.returncode == 0
    assert (live / "statusline.state").read_bytes() == b"new"
    assert mode(str(live / "statusline.state")) == 0o600


# ---------------------------------------------------------------- 3: outlets
def test_serve_log_message_has_no_esc_and_no_extra_newline(capfd):
    serve._log("a" + EVIL + "b\nc\r\nd")
    out = capfd.readouterr()
    text = out.out + out.err
    assert "\x1b" not in text and "\x07" not in text and "\r" not in text
    assert text.count("\n") == 1 and text.endswith("\n")


def test_serve_log_plain_message_is_kept(capfd):
    serve._log("hello world")
    out = capfd.readouterr()
    assert "hello world" in out.out + out.err


def test_zellij_tab_name_has_no_control_chars():
    calls = []
    opener.zellij_new_tab("/w/p", "s-host:p" + EVIL + "x\ny\r\nz",
                          run=lambda argv, **k: calls.append(argv))
    argv = calls[0]
    name = argv[argv.index("--name") + 1]
    assert "\x1b" not in name and "\x07" not in name
    assert "\n" not in name and "\r" not in name
    assert name.startswith("s-host:p") and "x" in name and "z" in name


def test_zellij_tab_name_plain_name_unchanged():
    calls = []
    opener.zellij_new_tab("/w/p", "s-host:p", run=lambda argv, **k: calls.append(argv))
    argv = calls[0]
    assert argv[argv.index("--name") + 1] == "s-host:p"


def test_applescript_title_has_no_esc_and_no_extra_newline():
    base = opener.applescript("ls", "clean")
    s = opener.applescript("ls", "a" + EVIL + "b\nc\r\nd\re")
    assert "\x1b" not in s and "\x07" not in s
    assert s.count("\n") == base.count("\n")
    assert "\r" not in s
    assert 'set name to "' in s


def test_applescript_clean_title_still_used():
    assert 'set name to "my title"' in opener.applescript("ls", "my title")


def mk_dir_with_evil_names(root):
    d = root / "d"
    d.mkdir()
    names = ["plain.txt", "nl\nname", "esc" + EVIL + "x", "cr\rname"]
    for n in names:
        (d / n).write_text("x")
    return d, len(names)


def test_remote_dir_listing_script_one_line_per_file_no_esc(tmp_path):
    d, n = mk_dir_with_evil_names(tmp_path)
    script = cli.remote_preview_script(str(d))
    r = subprocess.run(["sh", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=dict(os.environ, HOME=str(tmp_path)))
    out = r.stdout.decode("utf-8", "replace")
    assert "\x1b" not in out and "\x07" not in out and "\r" not in out
    assert len(out.splitlines()) == n
    assert "plain.txt" in out


def test_remote_dir_listing_script_through_file_preview_is_clean(tmp_path, monkeypatch):
    d, n = mk_dir_with_evil_names(tmp_path)
    script = cli.remote_preview_script(str(d))

    class R:
        returncode = 0
        stdout = subprocess.run(["sh", "-c", script], stdout=subprocess.PIPE).stdout
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **k: R())
    out = cli.file_preview({"name": "s-host", "ssh": "s-host"}, str(d))
    assert "\x1b" not in out and "\x07" not in out and "\r" not in out
    assert len(out.splitlines()) == n


def test_remote_script_text_file_has_its_content(tmp_path):
    f = tmp_path / "a b.log"
    f.write_text("hello\n")
    r = subprocess.run(["sh", "-c", cli.remote_preview_script(str(f))], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    assert b"hello" in r.stdout


def test_remote_preview_output_with_esc_is_cleaned(monkeypatch):
    class R:
        returncode = 0
        stdout = ("line1" + EVIL + "\nline2\n").encode()
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **k: R())
    out = cli.file_preview({"name": "vm", "ssh": "vm"}, "/home/u/a.log")
    assert "\x1b" not in out and "\x07" not in out
    assert "line1" in out and "line2" in out
    assert len(out.splitlines()) == 2


def run_fpreview(root, arg, capsys, monkeypatch):
    cfgp = os.path.join(os.path.dirname(str(root)), "config.json")
    with open(cfgp, "w") as f:
        json.dump({"hosts": [{"name": "local", "local": True, "roots": [str(root)]}]}, f)
    monkeypatch.setenv("PROJ_CONFIG", cfgp)
    assert cli.main(["_fpreview", "local", str(root), arg]) == 0
    return capsys.readouterr().out


def test_local_dir_listing_one_line_per_file_no_esc(tmp_path, capsys, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    d = root / "out"
    d.mkdir()
    names = ["plain.txt", "nl\nname", "esc" + EVIL + "x", "cr\rname"]
    for n in names:
        (d / n).write_text("x")
    out = run_fpreview(root, "out/", capsys, monkeypatch)
    assert "\x1b" not in out and "\x07" not in out and "\r" not in out
    assert len(out.splitlines()) == len(names)
    assert "plain.txt" in out


def test_local_dir_listing_plain_names_are_all_shown(tmp_path, capsys, monkeypatch):
    root = tmp_path / "root"
    (root / "out").mkdir(parents=True)
    for n in ("a.txt", "b.txt"):
        (root / "out" / n).write_text("x")
    out = run_fpreview(root, "out/", capsys, monkeypatch)
    assert "a.txt" in out and "b.txt" in out and len(out.splitlines()) == 2


def test_local_text_file_preview_has_no_esc(tmp_path, capsys, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "debug.log").write_text("one" + EVIL + "\ntwo\r\nthree\n")
    out = run_fpreview(root, "debug.log\t", capsys, monkeypatch)
    assert "\x1b" not in out and "\x07" not in out and "\r" not in out
    assert "one" in out and "two" in out and "three" in out


# ---------------------------------------------------------------- extras
def tmux_row(path="/home/u/p"):
    return {"host": "s-host", "ssh": "s-host", "local": False, "path": path, "tmux": "always",
            "mux": "tmux", "home": "/home/u"}


def capture_open(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.opener, "open_command",
                        lambda cmd, title, iterm=None: calls.append(cmd) or "inline")
    monkeypatch.setattr(cli.opener, "has_iterm", lambda: False)
    return calls


def test_usage_log_symlink_does_not_stop_enter_and_target_is_untouched(tmp_path, monkeypatch):
    calls = capture_open(monkeypatch)
    target = tmp_path / "elsewhere.txt"
    target.write_text("precious\n")
    os.chmod(str(target), 0o644)
    os.makedirs(sync.cache_dir(), exist_ok=True)
    os.symlink(str(target), usage_path())
    r = tmux_row()
    out = cli.open_project([r], r)
    assert out is not None and calls  # still opened
    cli.launch(r, r["path"], {"id": "abcdef123456", "cwd": r["path"]})
    assert len(calls) == 2
    assert target.read_text() == "precious\n"
    assert mode(str(target)) == 0o644
    assert os.path.islink(usage_path())


def test_usage_log_unwritable_cache_does_not_stop_enter(tmp_path, monkeypatch):
    calls = capture_open(monkeypatch)
    blocker = tmp_path / "file"
    blocker.write_text("not a dir")
    monkeypatch.setenv("PROJ_CACHE", str(blocker / "sub"))
    r = tmux_row()
    out = cli.open_project([r], r)
    assert out is not None and calls


def test_usage_log_non_regular_file_does_not_stop_enter(monkeypatch):
    calls = capture_open(monkeypatch)
    os.makedirs(usage_path())  # a directory where the log should be
    r = tmux_row()
    assert cli.open_project([r], r) is not None and calls


def test_log_event_itself_swallows_a_symlinked_log(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text("keep")
    os.makedirs(sync.cache_dir(), exist_ok=True)
    os.symlink(str(target), usage_path())
    cli.log_event("open", "s-host", "/p")  # must not raise
    assert target.read_text() == "keep"


def zellij_row():
    return {"host": "s-host", "path": "/home/u/p", "local": True, "ssh": None, "mux": "zellij"}


def zellij_env(monkeypatch, session_name):
    monkeypatch.setattr(cli.config, "zellij_in_scope", lambda row: True)
    monkeypatch.setattr(cli.agent, "zellij_by_project",
                        lambda paths: {"/home/u/p": [{"name": session_name, "exited": False}]})
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
    monkeypatch.delenv("ZELLIJ", raising=False)


def test_failed_attach_writes_no_usage_event(monkeypatch):
    zellij_env(monkeypatch, "-x")  # zellij_attach raises ValueError for option-like names
    ran = []
    monkeypatch.setattr(cli.opener, "open_command", lambda *a, **k: ran.append(a))
    r = zellij_row()
    outcome, notice = cli.open_project([r], r)
    assert outcome is None and ran == []
    assert not os.path.exists(usage_path()) or open(usage_path()).read() == ""


def test_successful_attach_does_write_usage_event(monkeypatch):
    zellij_env(monkeypatch, "proj-a")
    ran = []
    monkeypatch.setattr(cli.opener, "open_command",
                        lambda cmd, title, iterm=None: ran.append(cmd) or "inline")
    monkeypatch.setattr(cli.opener, "has_iterm", lambda: False)
    r = zellij_row()
    outcome = cli.open_project([r], r)
    assert outcome is not None and ran
    events = [json.loads(ln) for ln in open(usage_path())]
    assert len(events) == 1 and events[0]["path"] == "/home/u/p"
