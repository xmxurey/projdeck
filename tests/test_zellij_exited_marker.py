"""F11: exited means the trailing (EXITED marker; cleanup commands end options with --."""
import shlex
import subprocess

from projdeck import agent, board, model, report

NOW = 1_800_000_000


def listing(stdout, code=0):
    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")
    return agent.zellij_session_list(run=run)


def test_a_live_session_named_exited_is_not_exited():
    out = ("proj-EXITED [Created 2h 3m ago] (current)\n"
           "EXITED-notes [Created 1day ago] \n"
           "old [Created 3months ago] (EXITED - attach to resurrect)\n")
    assert listing(out) == [{"name": "proj-EXITED", "exited": False},
                            {"name": "EXITED-notes", "exited": False},
                            {"name": "old", "exited": True}]


def test_unrecognised_line_judged_after_its_name():
    assert listing("EXITED-x something\n") == [{"name": "EXITED-x", "exited": False}]


CFG = {"hosts": [{"name": "s-host", "local": True, "mux": "zellij"}]}


def built(names):
    lv = {"schema": 1, "host": "s-host", "collected_at": NOW - 5, "source": "claude agents",
          "sessions": [], "projects": {}, "errors": [], "zellij_exited": names}
    return board.build([], [lv], CFG, NOW), [lv]


def test_cleanup_command_ends_options_before_the_name():
    b, _ = built(["dao", "-bad", "my s"])
    cmds = {e["name"]: e["cmd"] for e in b["summary"]["zellij_exited"]}
    assert cmds["dao"] == "zellij delete-session -- dao"
    assert shlex.split(cmds["-bad"]) == ["zellij", "delete-session", "--", "-bad"]
    assert shlex.split(cmds["my s"]) == ["zellij", "delete-session", "--", "my s"]


def test_report_lists_the_same_commands():
    b, lives = built(["-bad"])
    caches = [{"host": "s-host", "synced_at": NOW, "projects": []}]
    md = report.render(model.rows(caches, dict(CFG, tiers_days={"active": 14, "stale": 90}), NOW),
                       caches, NOW, board=b, lives=lives, cfg=CFG)
    assert "- `s-host`: `zellij delete-session -- -bad`" in md
