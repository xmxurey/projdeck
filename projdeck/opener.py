"""Build the shell command for a project/session and open it in iTerm2."""
import hashlib
import os
import re
import shlex
import subprocess
import sys

q = shlex.quote
LOGIN_SHELL = 'exec "$SHELL" -l'


def resume_inner(session_id):
    return "claude --resume %s" % q(session_id)


def tmux_name(path, session_id=None):
    """Stable tmux session name: project name + short path hash (+ Claude session)."""
    base = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.basename(path.rstrip("/")) or "root")[:24]
    name = "proj-%s-%s" % (base, hashlib.sha1(path.encode("utf-8")).hexdigest()[:4])
    return name + ("-" + session_id[:8] if session_id else "")


def _remote_script(path, inner, tmux=False, session_id=None):
    if inner is None:
        rest = LOGIN_SHELL
    else:
        # login + interactive so PATH (claude) is set; keep a shell open afterwards
        rest = '"$SHELL" -lic %s; %s' % (q(inner), LOGIN_SHELL)
    if not tmux:
        return "cd %s && %s" % (q(path), rest)
    # tmux keeps the remote shell/claude alive when the connection drops; -A re-attaches
    new = "tmux new-session -A -s %s" % q(tmux_name(path, session_id))
    if inner is not None:
        new += " " + q(rest)
    return "cd %s && if command -v tmux >/dev/null 2>&1; then exec %s; else %s; fi" % (
        q(path), new, rest)


def build_command(target, path, inner=None, new_tab=True, session_id=None):
    if target.get("local"):
        if inner is None and not new_tab:
            inner = LOGIN_SHELL
        return "cd %s" % q(path) + (" && %s" % inner if inner else "")
    script = _remote_script(path, inner, tmux=target.get("tmux", True), session_id=session_id)
    return "ssh -t %s %s" % (q(target["ssh"]), q(script))


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
        try:
            run(["osascript", "-e", applescript(cmd, title)], check=True)
            return "iterm"
        except subprocess.CalledProcessError:
            print("iTerm2 automation failed (allow it in System Settings \u203a Privacy & "
                  "Security \u203a Automation); running here instead", file=sys.stderr)
    run(cmd, shell=True)  # no iTerm2: run in the current terminal
    return "inline"
