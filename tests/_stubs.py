"""Stand-ins for the programs tests and probes must never reach for real.

One list for both users: tests/conftest.py puts them first on PATH for every test, and
scripts/sandbox.py does the same for probes and debug scripts run outside pytest.
"""
import os

# program name -> what its stand-in does. ("print", text) prints text and exits 0;
# ("exit", code) just exits with code. Every stand-in records its arguments, one call per
# line, in the calls file.
REAL_PROGRAM_STUBS = {
    "claude": ("print", "[]"),
    "ssh": ("exit", 255),
    "zellij": ("exit", 1),
    "tmux": ("exit", 1),
    "tailscale": ("exit", 1),
    "systemctl": ("exit", 1),
    "systemd-analyze": ("exit", 1),
    "osascript": ("exit", 1),
    "open": ("exit", 1),
    "xdg-open": ("exit", 1),
    "fzf": ("exit", 1),
}


def install(stub_dir, claude_dir, calls):
    """Write every stand-in: claude into claude_dir (tests inspect its directory name),
    the rest into stub_dir. Returns the PATH entries to put in front, in order."""
    for prog, (kind, arg) in REAL_PROGRAM_STUBS.items():
        body = "#!/bin/sh\necho \"%s $*\" >> \"%s\"\n" % (prog, calls)
        body += "echo '%s'\n" % arg if kind == "print" else "exit %d\n" % arg
        path = os.path.join(str(claude_dir if prog == "claude" else stub_dir), prog)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)
    return [str(stub_dir), str(claude_dir)]
