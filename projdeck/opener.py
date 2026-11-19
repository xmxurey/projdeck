"""Build the shell command for a project/session and open it in iTerm2."""
import hashlib
import os
import re
import shlex
import subprocess
import sys

from projdeck import sync

q = shlex.quote
LOGIN_SHELL = 'exec "$SHELL" -l'
PICKER = "claude --resume"  # Claude's own session picker for this directory


def resume_inner(session_id):
    return "claude --resume %s" % q(session_id)


def tmux_name(path, session_id=None):
    """Stable tmux session name: project name + short path hash (+ Claude session)."""
    base = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.basename(path.rstrip("/")) or "root")[:24]
    name = "proj-%s-%s" % (base, hashlib.sha1(path.encode("utf-8")).hexdigest()[:4])
    return name + ("-" + session_id[:8] if session_id else "")


def _tmux_mode(target):
    """What _remote_script gets as tmux=: only hosts whose multiplexer is tmux use it."""
    mux = target.get("mux", "tmux" if target.get("tmux", True) else False)
    if mux != "tmux":
        return False
    return "always" if target.get("tmux") == "always" else True


def _then_shell(inner):
    return '"$SHELL" -lic %s; %s' % (q(inner), LOGIN_SHELL)


def zellij_attach(name, path):
    """Join a live zellij session; if it has exited meanwhile, do not resurrect it:
    run the picker in a plain shell instead."""
    live = ("zellij list-sessions -n 2>/dev/null | "
            "awk -v n=%s '$1 == n && !/EXITED/ {f = 1} END {exit !f}'" % q(name))
    return "if %s; then exec zellij attach %s; else cd %s && %s; fi" % (
        live, q(name), q(path), _then_shell(PICKER))


def zellij_new_tab(path, title, run=subprocess.run):
    """Inside zellij: a new tab in the current session runs the picker; proj stays open."""
    shell = os.environ.get("SHELL") or "/bin/sh"
    run(["zellij", "action", "new-tab", "--cwd", path, "--name", title, "--",
         shell, "-lic", '%s; exec "$SHELL" -l' % PICKER], check=True)


PROJ_SESSION_PREFIX = "proj-"
_SESSION_NAME_RE = re.compile(r"^proj-[A-Za-z0-9._-]+$")


def proj_session_name(project_name):
    """The one zellij session proj creates for a project: proj-<project name made safe>.
    When making the name safe lost something (replaced or stripped characters, or cut it
    short), a short hash of the original keeps different projects apart: proj-<name>-ab12.
    The "/" of a nested project becomes "-" without counting as a loss."""
    flat = project_name.replace("/", "-")
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", flat).lstrip(".-")
    if safe == flat and len(safe) <= 32:
        return PROJ_SESSION_PREFIX + (safe or "project")
    digest = hashlib.sha1(project_name.encode("utf-8", "surrogateescape")).hexdigest()[:4]
    return "%s%s-%s" % (PROJ_SESSION_PREFIX, (safe[:27] or "project").rstrip("-") or "project", digest)


def session_state(sessions, name):
    """"exited", "live" or None for the session called exactly `name` in a
    agent.zellij_session_list() result."""
    for s in sessions:
        if s["name"] == name:
            return "exited" if s["exited"] else "live"
    return None


def _kdl_str(text):
    """A KDL string literal: backslash, quote and control characters escaped."""
    out = []
    for ch in text:
        if ch in '\\"':
            out.append("\\" + ch)
        elif ch in "\n\r\t\b\f":
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7f:
            out.append("\\u{%x}" % ord(ch))
        else:
            out.append(ch)
    return '"%s"' % "".join(out)


_write_atomic = sync._write_atomic


def zellij_new_session(path, name, layout_dir, delete_first):
    """Write the layout (tab bar, one command pane with cwd = project that runs the picker
    and then a login shell and closes when that exits, status bar) to <layout_dir>/<name>.kdl
    and return the shell command that creates the session. Raises OSError when the layout
    cannot be written. ValueError when `name` is not a proj- session name. delete_first
    removes the exited session of that exact name first; only a proj- name may ever be
    deleted. Any failure of the zellij commands falls back to running the picker right here."""
    if not _SESSION_NAME_RE.match(name):
        raise ValueError("not a proj session name: %r" % name)
    shell = os.environ.get("SHELL") or "/bin/sh"
    layout = "\n".join([
        "layout {",
        "    pane size=1 borderless=true {",
        '        plugin location="zellij:tab-bar"',
        "    }",
        "    pane cwd=%s command=%s close_on_exit=true {" % (_kdl_str(path), _kdl_str(shell)),
        "        args %s %s" % (_kdl_str("-lic"), _kdl_str('%s; %s' % (PICKER, LOGIN_SHELL))),
        "    }",
        "    pane size=2 borderless=true {",
        '        plugin location="zellij:status-bar"',
        "    }",
        "}", ""])
    file = os.path.join(layout_dir, name + ".kdl")
    _write_atomic(file, layout)
    create = "zellij --session %s --new-session-with-layout %s" % (q(name), q(file))
    if delete_first:
        create = "zellij delete-session %s && %s" % (q(name), create)
    return "cd %s && { %s; } || { cd %s && %s; }" % (q(path), create, q(path), _then_shell(PICKER))


def _remote_script(path, inner, tmux=True, session_id=None):
    """tmux: False never; "always" create or attach (survives drops, costs memory);
    otherwise (default) re-attach only a proj tmux session that is already running."""
    if inner is None:
        rest = LOGIN_SHELL
    else:
        # login + interactive so PATH (claude) is set; keep a shell open afterwards
        rest = _then_shell(inner)
    if not tmux:
        return "cd %s && %s" % (q(path), rest)
    name = tmux_name(path, session_id)
    target = q("=" + name)
    # already inside tmux: switch to it, never nest
    go = 'if [ -n "$TMUX" ]; then tmux switch-client -t %s; else exec tmux attach-session -t %s; fi' % (
        target, target)
    if tmux == "always":
        cmd = "" if inner is None else " " + q(rest)
        create = 'tmux new-session -d -s %s -c %s%s' % (q(name), q(path), cmd)
        run = ('if [ -n "$TMUX" ]; then { tmux has-session -t %s 2>/dev/null || %s; } && '
               'tmux switch-client -t %s; else exec tmux new-session -A -s %s%s; fi') % (
            target, create, target, q(name), cmd)
    else:
        run = "if tmux has-session -t %s 2>/dev/null; then %s; else %s; fi" % (target, go, rest)
    return "cd %s && if command -v tmux >/dev/null 2>&1; then %s; else %s; fi" % (
        q(path), run, rest)


def build_command(target, path, inner=None, new_tab=True, session_id=None):
    tmux = _tmux_mode(target)
    if target.get("local"):
        if not new_tab:  # no iTerm2 tab: runs right here
            return _remote_script(path, inner, tmux=tmux, session_id=session_id)
        return "cd %s" % q(path) + (" && %s" % inner if inner else "")
    script = _remote_script(path, inner, tmux=tmux, session_id=session_id)
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
        "    set name to %s" % _as_str(re.sub(r"[\r\n]", " ", title)),
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


def open_url(url):
    """Open an http(s) url in a browser on this machine, without waiting for it. False when
    nothing was opened (not an http(s) url, no graphical session, or the opener failed), so
    the caller can show the URL instead."""
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    if sys.platform == "darwin":
        cmd = ["open", url]
    elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        cmd = ["xdg-open", url]
    else:
        return False
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    return True
