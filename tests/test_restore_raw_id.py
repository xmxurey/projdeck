"""F14: restore sends back the batch id exactly as listed; only the shown column is cleaned."""
import os

from projdeck import agent, cli, ui

NOW = 1_800_000_000


def test_restore_finds_a_batch_whose_project_name_has_two_spaces_and_esc(tmp_path, monkeypatch,
                                                                         make_repo):
    repo = make_repo(tmp_path / "a  b\x1bc")  # basename goes into the batch id
    with open(os.path.join(repo, "junk.log"), "w") as f:
        f.write("x")
    trash = str(tmp_path / "trash")
    res = agent.act_trash({"path": repo, "files": ["junk.log"], "trash_dir": trash, "now": NOW})
    assert "a  b\x1bc" in res["batch"] and not os.path.exists(os.path.join(repo, "junk.log"))

    cfg = {"hosts": [{"name": "local", "local": True}], "trash_dir": trash, "ssh_timeout": 20}
    shown = []

    def fzf(lines, args, expect=()):  # fzf hands back the whole input line it was given
        shown.extend(args)
        return "", [lines[0]]
    monkeypatch.setattr(ui, "run_fzf", fzf)
    assert cli.cmd_restore(cfg, type("A", (), {"host": None})()) == 0
    assert os.path.exists(os.path.join(repo, "junk.log"))
    assert "--with-nth=3.." in shown  # the raw host / id fields are not displayed


def test_batch_line_keeps_id_and_cleans_the_display():
    line = cli._batch_line({"name": "h"}, {"id": "b  \x1b[2Jx", "created": NOW, "count": 1,
                                          "project": "/p\x1b]52;c;x\x07"})
    host, bid, display = line.split("\t", 2)
    assert (host, bid) == ("h", "b  \x1b[2Jx")
    assert "\x1b" not in display and "b x" in display and "/p" in display
