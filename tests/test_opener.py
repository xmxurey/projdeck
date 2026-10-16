import shlex
import subprocess

from projdeck import opener

LOCAL = {"local": True, "ssh": None}
REMOTE = {"local": False, "ssh": "sg"}


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
