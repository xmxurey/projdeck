import shlex
import subprocess

from projdeck import opener

LOCAL = {"local": True, "ssh": None}
REMOTE = {"local": False, "ssh": "s-host", "tmux": False}
TMUX = {"local": False, "ssh": "s-host"}  # default: re-attach existing proj tmux sessions only
ALWAYS = {"local": False, "ssh": "s-host", "tmux": "always"}


def test_local_commands():
    assert opener.build_command(LOCAL, "/a b") == "cd '/a b'"
    assert opener.build_command(LOCAL, "/a", opener.resume_inner("id-1")) == \
        "cd /a && claude --resume id-1"
    plain = dict(LOCAL, tmux=False)
    assert opener.build_command(plain, "/a", new_tab=False) == "cd /a && exec \"$SHELL\" -l"


def test_remote_commands_quote_correctly():
    cmd = opener.build_command(REMOTE, "/home/u/my proj", opener.resume_inner("id-1"))
    argv = shlex.split(cmd)
    assert argv[:3] == ["ssh", "-t", "s-host"]
    assert argv[3] == "cd '/home/u/my proj' && \"$SHELL\" -lic 'claude --resume id-1'; exec \"$SHELL\" -l"
    assert shlex.split(opener.build_command(REMOTE, "/p"))[3] == "cd /p && exec \"$SHELL\" -l"


def test_remote_script_runs_in_sh(tmp_path):
    """The remote string must be valid sh: run it with SHELL=echo-like stub."""
    d = tmp_path / "my dir"
    d.mkdir()
    remote = shlex.split(opener.build_command(REMOTE, str(d), "pwd"))[3]
    out = subprocess.run(["sh", "-c", remote], env={"SHELL": "/bin/sh", "PATH": "/usr/bin:/bin"},
                         stdout=subprocess.PIPE, universal_newlines=True, input="")
    # (a login shell may print a motd first: only the line `pwd` printed matters)
    assert str(d) in out.stdout.splitlines()


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


def _run(target, path, inner, tmp_path, with_tmux=True, session_id=None, has_session=False,
         in_tmux=False, local=False):
    cmd = opener.build_command(target, path, inner, session_id=session_id, new_tab=not local)
    script = cmd if local else shlex.split(cmd)[3]
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    if with_tmux:
        fake = bindir / "tmux"
        fake.write_text('#!/bin/sh\necho "tmux pwd=$PWD"; for a in "$@"; do echo "[$a]"; done\n'
                        '[ "$1" = has-session ] && [ -z "$FAKE_HAS" ] && exit 1; exit 0\n')
        fake.chmod(0o755)
    env = {"SHELL": "/bin/sh", "PATH": str(bindir)}  # never the real tmux
    if has_session:
        env["FAKE_HAS"] = "1"
    if in_tmux:
        env["TMUX"] = "/tmp/s,1,0"
    out = subprocess.run(["/bin/sh", "-c", script], env=env, stdout=subprocess.PIPE,
                         universal_newlines=True, input="").stdout.splitlines()
    return [ln for ln in out if not ln.startswith("tmux pwd=")]


def test_no_live_session_opens_plain_shell_not_tmux(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    name = opener.tmux_name(str(d))
    out = _run(TMUX, str(d), "pwd", tmp_path)
    assert out == ["[has-session]", "[-t]", "[=%s]" % name, str(d)]


def test_live_session_is_reattached(tmp_path):
    d = tmp_path / "my proj"
    d.mkdir()
    name = opener.tmux_name(str(d))
    out = _run(TMUX, str(d), None, tmp_path, has_session=True)
    assert out[3:] == ["[attach-session]", "[-t]", "[=%s]" % name]
    assert name.startswith("proj-my_proj-") and "." not in name and ":" not in name
    assert opener.tmux_name("/a/x") != opener.tmux_name("/b/x")  # same basename, other dir


def test_resume_reattaches_the_sessions_own_tmux(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    out = _run(TMUX, str(d), opener.resume_inner("abcdef123456"), tmp_path,
               session_id="abcdef123456", has_session=True)
    assert out[-1] == "[=%s]" % opener.tmux_name(str(d), "abcdef123456")
    assert out[-1].endswith("-abcdef12]")


def test_inside_tmux_switches_client_instead_of_nesting(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    name = opener.tmux_name(str(d))
    out = _run(TMUX, str(d), None, tmp_path, has_session=True, in_tmux=True)
    assert out[3:] == ["[switch-client]", "[-t]", "[=%s]" % name]


def test_tmux_always_creates_or_attaches(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    out = _run(ALWAYS, str(d), opener.resume_inner("abcdef123456"), tmp_path,
               session_id="abcdef123456")
    assert out[:4] == ["[new-session]", "[-A]", "[-s]",
                       "[%s]" % opener.tmux_name(str(d), "abcdef123456")]
    assert out[4] == "[\"$SHELL\" -lic 'claude --resume abcdef123456'; exec \"$SHELL\" -l]"


def test_without_tmux_binary_falls_back(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    assert _run(TMUX, str(d), "pwd", tmp_path, with_tmux=False) == [str(d)]
    assert _run(ALWAYS, str(d), "pwd", tmp_path, with_tmux=False) == [str(d)]


def test_local_without_iterm_reattaches_live_tmux(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    out = _run(LOCAL, str(d), None, tmp_path, has_session=True, local=True)
    assert out[3:] == ["[attach-session]", "[-t]", "[=%s]" % opener.tmux_name(str(d))]
    assert "tmux" not in opener.build_command(LOCAL, str(d), new_tab=True)  # iTerm2 tab
    plain = dict(LOCAL, tmux=False)
    assert "tmux" not in opener.build_command(plain, str(d), new_tab=False)


def test_applescript_title_newlines_flattened():
    s = opener.applescript("ls", "a\nb\rc")
    assert 'set name to "a b c"' in s


ZELLIJ = {"local": True, "ssh": None, "mux": "zellij"}


def _fake_zellij(bindir, sessions):
    z = bindir / "zellij"
    z.write_text('#!/bin/sh\nif [ "$1" = list-sessions ]; then printf "%s"; exit 0; fi\n'
                 'for a in "$@"; do echo "[$a]"; done\n' % sessions)
    z.chmod(0o755)


def _sh(script, tmp_path, env_extra=None):
    env = {"SHELL": "/bin/sh", "PATH": str(tmp_path / "bin") + ":/usr/bin:/bin"}
    env.update(env_extra or {})
    return subprocess.run(["/bin/sh", "-c", script], env=env, stdout=subprocess.PIPE,
                          universal_newlines=True, input="").stdout.splitlines()


def test_zellij_attach_only_when_live(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (tmp_path / "bin").mkdir()
    _fake_zellij(tmp_path / "bin", "research [Created 2h ago] \\nold [Created 9d ago] (EXITED - attach to resurrect)\\n")
    assert _sh(opener.zellij_attach("research", str(d)), tmp_path) == ["[attach]", "[research]"]
    # exited (or vanished) session: never resurrect, run the picker in a plain shell
    out = _sh(opener.zellij_attach("old", str(d)).replace("claude --resume", "pwd"), tmp_path)
    assert str(d) in out and "[attach]" not in out  # (a login shell may print a motd too)


def test_picker_is_the_enter_command():
    assert opener.PICKER == "claude --resume"
    cmd = opener.build_command(dict(ZELLIJ), "/a", opener.PICKER, new_tab=False)
    assert "tmux" not in cmd and "claude --resume" in cmd


def test_mux_false_and_zellij_skip_tmux_but_tmux_mux_keeps_it():
    assert "tmux" not in opener.build_command({"local": False, "ssh": "e", "mux": False}, "/p", opener.PICKER)
    assert "tmux has-session" in opener.build_command({"local": False, "ssh": "e", "mux": "tmux"}, "/p", opener.PICKER)
    assert "new-session" in opener.build_command({"local": False, "ssh": "e", "mux": "tmux", "tmux": "always"}, "/p")


def test_zellij_new_tab_argv():
    calls = []
    opener.zellij_new_tab("/w/p q", "s-host:p", run=lambda argv, **k: calls.append(argv))
    argv = calls[0]
    assert argv[:7] == ["zellij", "action", "new-tab", "--cwd", "/w/p q", "--name", "s-host:p"]
    assert argv[7] == "--" and argv[9:] == ["-lic", 'claude --resume; exec "$SHELL" -l']
