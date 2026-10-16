"""Build the shell command for a project/session and open it in iTerm2."""
import os
import shlex
import subprocess
import sys

q = shlex.quote
LOGIN_SHELL = 'exec "$SHELL" -l'


def resume_inner(session_id):
    return "claude --resume %s" % q(session_id)


def _remote_script(path, inner):
    if inner is None:
        return "cd %s && %s" % (q(path), LOGIN_SHELL)
    # login + interactive so PATH (claude) is set; keep a shell open afterwards
    return 'cd %s && "$SHELL" -lic %s; %s' % (q(path), q(inner), LOGIN_SHELL)


def build_command(target, path, inner=None, new_tab=True):
    if target.get("local"):
        if inner is None and not new_tab:
            inner = LOGIN_SHELL
        return "cd %s" % q(path) + (" && %s" % inner if inner else "")
    return "ssh -t %s %s" % (q(target["ssh"]), q(_remote_script(path, inner)))


def _as_str(s):
    return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')


def applescript(cmd, title):
    return "\n".join([
        'tell application "iTerm2"',
        "  activate",
        "  if (count of windows) = 0 then",
        "    create window with default profile",
        "  else",
        "    tell current window to create tab with default profile",
        "  end if",
        "  tell current session of current window",
        "    set name to %s" % _as_str(title),
        "    write text %s" % _as_str(cmd),
        "  end tell",
        "end tell",
    ])


def has_iterm():
    return sys.platform == "darwin" and any(
        os.path.isdir(p) for p in ("/Applications/iTerm.app",
                                   os.path.expanduser("~/Applications/iTerm.app")))


def open_command(cmd, title, run=subprocess.run, iterm=None):
    if iterm is None:
        iterm = has_iterm()
    if iterm:
        run(["osascript", "-e", applescript(cmd, title)], check=True)
        return "iterm"
    run(cmd, shell=True)  # no iTerm2: run in the current terminal
    return "inline"
