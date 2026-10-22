import shlex
import subprocess

from projdeck import opener

LOCAL = {"local": True, "ssh": None}
REMOTE = {"local": False, "ssh": "sg", "tmux": False}
TMUX = {"local": False, "ssh": "sg"}  # tmux defaults on


def test_local_commands():
    assert opener.build_command(LOCAL, "/a b") == "cd '/a b'"
    assert opener.build_command(LOCAL, "/a", opener.resume_inner("id-1")) == \
        "cd /a && claude --resume id-1"
    assert opener.build_command(LOCAL, "/a", new_tab=False) == "cd /a && exec \"$SHELL\" -l"


def test_remote_commands_quote_correctly():
    cmd = opener.build_command(REMOTE, "/home/u/my proj", opener.resume_inner("id-1"))
    argv = shlex.split(cmd)
    assert argv[:3] == ["ssh", "-t", "sg"]
    assert argv[3] == "cd '/home/u/my proj' && \"$SHELL\" -lic 'claude --resume id-1'; exec \"$SHELL\" -l"
    assert shlex.split(opener.build_command(REMOTE, "/p"))[3] == "cd /p && exec \"$SHELL\" -l"


def test_remote_script_runs_in_sh(tmp_path):
    """The remote string must be valid sh: run it with SHELL=echo-like stub."""
    d = tmp_path / "my dir"
    d.mkdir()
    remote = shlex.split(opener.build_command(REMOTE, str(d), "pwd"))[3]
    out = subprocess.run(["sh", "-c", remote], env={"SHELL": "/bin/sh", "PATH": "/usr/bin:/bin"},
                         stdout=subprocess.PIPE, universal_newlines=True, input="")
    assert out.stdout.splitlines()[0] == str(d)


def test_applescript_escapes():
    s = opener.applescript('echo "hi" \\ there', 'ti"tle')
    assert 'write text "echo \\"hi\\" \\\\ there"' in s
    assert 'set name to "ti\\"tle"' in s
    assert s.startswith('tell application "iTerm2"')


def test_open_command_paths():
    calls = []
    run = lambda *a, **k: calls.append((a, k))
    assert opener.open_command("ls", "t", run=run, iterm=True) == "iterm"
    assert calls[0][0][0][:2] == ["osascript", "-e"]
    assert opener.open_command("ls", "t", run=run, iterm=False) == "inline"
    assert calls[1] == (("ls",), {"shell": True})


def test_open_command_falls_back_when_osascript_fails(capsys):
    calls = []

    def run(*a, **k):
        calls.append(a)
        if a[0][0] == "osascript":
            raise subprocess.CalledProcessError(1, "osascript")
    assert opener.open_command("ls", "t", run=run, iterm=True) == "inline"
    assert calls[-1] == ("ls",)
    assert "iTerm2 automation failed" in capsys.readouterr().err


def _run_remote(target, path, inner, tmp_path, with_tmux, session_id=None):
    remote = shlex.split(opener.build_command(target, path, inner, session_id=session_id))[3]
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    if with_tmux:
        fake = bindir / "tmux"
        fake.write_text('#!/bin/sh\necho "tmux pwd=$PWD"; for a in "$@"; do echo "[$a]"; done\n')
        fake.chmod(0o755)
    env = {"SHELL": "/bin/sh", "PATH": str(bindir)}  # never the real tmux
    return subprocess.run(["/bin/sh", "-c", remote], env=env, stdout=subprocess.PIPE,
                          universal_newlines=True, input="").stdout.splitlines()


def test_remote_uses_tmux_session_per_project(tmp_path):
    d = tmp_path / "my proj"
    d.mkdir()
    out = _run_remote(TMUX, str(d), None, tmp_path, with_tmux=True)
    name = opener.tmux_name(str(d))
    assert out == ["tmux pwd=%s" % d, "[new-session]", "[-A]", "[-s]", "[%s]" % name]
    assert name.startswith("proj-my_proj-") and "." not in name and ":" not in name
    assert opener.tmux_name("/a/x") != opener.tmux_name("/b/x")  # same basename, other dir


def test_remote_resume_in_tmux_runs_claude_then_shell(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    out = _run_remote(TMUX, str(d), opener.resume_inner("abcdef123456"), tmp_path, with_tmux=True,
                      session_id="abcdef123456")
    assert out[4] == "[%s]" % opener.tmux_name(str(d), "abcdef123456")
    assert out[4].endswith("-abcdef12]")
    assert out[5] == "[\"$SHELL\" -lic 'claude --resume abcdef123456'; exec \"$SHELL\" -l]"


def test_remote_without_tmux_falls_back(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    out = _run_remote(TMUX, str(d), "pwd", tmp_path, with_tmux=False)
    assert out[0] == str(d)


def test_applescript_title_newlines_flattened():
    s = opener.applescript("ls", "a\nb\rc")
    assert 'set name to "a b c"' in s
