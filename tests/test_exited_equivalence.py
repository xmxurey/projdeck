"""agent.zellij_session_list (Python) and opener.zellij_attach (awk, runs on the host) both decide
whether a `zellij list-sessions -n` line is an exited session. They must agree on every line,
or Enter attaches to a session the board shows as exited (or the other way round)."""
import os
import shutil
import subprocess

import pytest

from projdeck import agent, opener

NAME = "proj-a"
HEAD = NAME + " [Created 2h ago] "
LINES = [
    HEAD,
    HEAD + "(EXITED - attach to resurrect)",
    HEAD + "(EXITED)",
    HEAD + "(EXITED )",
    HEAD + "(EXITED - attach to resurrect)   ",
    HEAD + "(EXITED - attach to resurrect)\t",
    HEAD + "(EXITED - attach to resurrect)\r",
    HEAD + "(EXITED - attach to resurrect)\f",
    HEAD + "(EXITED - attach to resurrect)\v",
    HEAD + "(EXITED - attach to resurrect)\u00a0",
    HEAD + "(EXITED - attach to resurrect) trailing",
    HEAD + "(EXITED-x)",
    HEAD + "(EXITED_x)",
    HEAD + "(EXITED2)",
    HEAD + "(EXITEDx)",
    HEAD + "(EXITED\u00e9)",              # \b treats an accented letter as a word character
    HEAD + "(EXITED\u00e9 - attach to resurrect)",
    HEAD + "(EXITED\u2014 attach)",       # an em dash is not a word character
    HEAD + "(EXITED\tattach)",
    HEAD + "(EXITED\u00a0attach)",
    HEAD + "(EXITED (nested))",
    HEAD + "(exited - attach)",
    HEAD + "EXITED",
    HEAD + "[EXITED]",
    HEAD + "(EXITED) (EXITED)",
    HEAD + "(CURRENT)",
]


# every awk installed here: the script must mean the same in each (gawk, mawk, busybox)
AWKS = [a for a in ("gawk", "mawk", "busybox") if shutil.which(a)]


def _agent_says_exited(line):
    class R:
        returncode = 0
        stdout = line + "\n"
        stderr = ""
    sessions = agent.zellij_session_list(run=lambda *a, **k: R)
    assert [s["name"] for s in sessions] == [NAME]
    return sessions[0]["exited"]


def _awk_says_exited(line, tmp_path, awk):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    os.symlink(shutil.which(awk), str(bindir / "awk"))  # busybox picks the applet by this name
    (tmp_path / "out.txt").write_bytes((line + "\n").encode("utf-8"))
    z = bindir / "zellij"
    z.write_text('#!/bin/sh\nif [ "$1" = list-sessions ]; then cat "%s"; exit 0; fi\n'
                 'echo ATTACHED\n' % (tmp_path / "out.txt"))
    z.chmod(0o755)
    script = opener.zellij_attach(NAME, str(tmp_path)).replace("claude --resume", "echo PICKER")
    out = subprocess.run(["/bin/sh", "-c", script], env={"SHELL": "/bin/sh", "PATH": "%s:/usr/bin:/bin" % bindir},
                         stdout=subprocess.PIPE, universal_newlines=True, input="").stdout.splitlines()
    assert ("ATTACHED" in out) != ("PICKER" in out), out
    return "ATTACHED" not in out


@pytest.mark.parametrize("awk", AWKS)
@pytest.mark.parametrize("line", LINES, ids=[repr(l[len(HEAD):]) for l in LINES])
def test_agent_and_attach_script_agree_on_exited(line, awk, tmp_path):
    assert _agent_says_exited(line) == _awk_says_exited(line, tmp_path, awk)
