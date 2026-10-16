import json
import os

import pytest

from projdeck import cli, ui

NOW = 100 * 86400


def row(**kw):
    r = {"host": "vm", "path": "/home/u/quant", "exists": True, "local": False, "ssh": "vm",
         "tier": "active", "activity": NOW - 3 * 86400, "stale_since": None, "last_mtime": 0,
         "git": {"branch": "main", "dirty": 2, "untracked": 1, "ahead": 1, "no_upstream": False,
                 "last_commit_ts": NOW - 86400},
         "suspicious": ["debug.log"], "progress": "step 1\nstep 2", "errors": [],
         "sessions": [{"id": "s1", "cwd": "/home/u/quant", "title": "fix rebate", "last_ts": NOW - 3600,
                       "size_kb": 10, "headless": False}]}
    r.update(kw)
    return r


def test_project_line_fields_and_flags():
    host, path, display = ui.project_line(row(), NOW).split("\t")
    assert (host, path) == ("vm", "/home/u/quant")
    assert "quant" in display and "3d" in display
    for flag in ("±2", "↑1", "⚑1", "💬1"):
        assert flag in display
    _, _, d2 = ui.project_line(row(git=None, suspicious=[], sessions=[]), NOW).split("\t")
    assert "non-git" in d2


def test_session_line_and_file_line():
    r = row()
    fields = ui.session_line(r, r["sessions"][0], NOW).split("\t")
    assert fields[:3] == ["vm", "/home/u/quant", "s1"] and "fix rebate" in fields[3]
    assert ui.file_line({"path": "a b.log", "suspicious": True, "size": 2048}).split("\t")[0] == "a b.log"


def test_preview_text_mentions_key_facts():
    text = ui.preview_text(row(stale_since=NOW - 7200), NOW)
    for needle in ("vm:/home/u/quant", "main", "±2", "debug.log", "step 2", "fix rebate", "⚠"):
        assert needle in text


def test_run_fzf_parses_output(monkeypatch):
    class R:
        returncode = 0
        stdout = "ctrl-r\nvm\t/p\tdisp\n"
    monkeypatch.setattr(ui.subprocess, "run", lambda *a, **k: R())
    assert ui.run_fzf(["x"], [], expect=["ctrl-r"]) == ("ctrl-r", ["vm\t/p\tdisp"])
    R.stdout = "vm\t/p\tdisp\n"
    assert ui.run_fzf(["x"], []) == ("", ["vm\t/p\tdisp"])
    R.returncode = 130
    with pytest.raises(ui.Cancelled):
        ui.run_fzf(["x"], [])


def setup_env(tmp_path, monkeypatch, make_repo):
    root = tmp_path / "root"
    make_repo(root / "p1", {"README.md": "hi", "debug.log": "x", "run.log": "y"}, commit=False)
    monkeypatch.setenv("HOME", str(tmp_path / "isolated_home"))  # never scan the real ~/.claude
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"hosts": [{"name": "local", "local": True, "roots": [str(root)]}],
                                "trash_dir": str(tmp_path / "trash")}))
    monkeypatch.setenv("PROJ_CONFIG", str(cfgp))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    return str(root / "p1")


def test_cli_sync_report_and_preview(tmp_path, monkeypatch, capsys, make_repo):
    p1 = setup_env(tmp_path, monkeypatch, make_repo)
    assert cli.main(["sync"]) == 0
    assert "local: ok" in capsys.readouterr().out
    assert cli.main(["report"]) == 0
    assert "`local:%s` 2 files" % p1 in capsys.readouterr().out
    assert cli.main(["_preview", "local", p1 + "\t"]) == 0
    assert "local:%s" % p1 in capsys.readouterr().out


def test_cli_clean_restore_purge(tmp_path, monkeypatch, capsys, make_repo):
    p1 = setup_env(tmp_path, monkeypatch, make_repo)
    monkeypatch.setattr(ui, "pick_files", lambda files: [f["path"] for f in files if f["suspicious"]])
    answers = iter(["y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.main(["clean", "local:" + p1]) == 0
    assert not os.path.exists(os.path.join(p1, "debug.log"))
    assert os.path.exists(os.path.join(p1, "README.md"))
    monkeypatch.setattr(ui, "pick_line", lambda lines, header: lines[0])
    assert cli.main(["restore"]) == 0
    assert os.path.exists(os.path.join(p1, "debug.log"))
    answers = iter(["y", "no"])
    assert cli.main(["clean", "local:" + p1]) == 0
    assert cli.main(["trash", "--purge", "--older", "0d"]) == 0  # answered "no"
    assert "1 batch" in capsys.readouterr().out
    assert cli.main(["trash"]) == 0
    assert "p1" in capsys.readouterr().out
