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
    R.returncode = 1
    with pytest.raises(ui.Cancelled):
        ui.run_fzf(["x"], [])


def test_run_fzf_other_exit_code_is_error(monkeypatch):
    class R:
        returncode = 2
        stdout = ""
    monkeypatch.setattr(ui.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(SystemExit, match="fzf failed \\(exit 2\\)"):
        ui.run_fzf(["x"], [])


def test_run_fzf_uses_utf8_text_mode(monkeypatch):
    seen = {}

    class R:
        returncode = 0
        stdout = ""

    def fake_run(*a, **k):
        seen.update(k)
        return R()
    monkeypatch.setattr(ui.subprocess, "run", fake_run)
    ui.run_fzf(["x"], [])
    assert seen.get("encoding") == "utf-8"
    assert "universal_newlines" not in seen


def test_pick_files_preselects_on_load(monkeypatch):
    captured = {}

    def fake_fzf(lines, args, expect=()):
        captured["args"] = args
        return "", []
    monkeypatch.setattr(ui, "run_fzf", fake_fzf)
    files = [{"path": "a.log", "suspicious": True, "size": 1},
             {"path": "b.log", "suspicious": True, "size": 1},
             {"path": "c.txt", "suspicious": False, "size": 1}]
    ui.pick_files(files)
    assert "--bind=load:toggle+down+toggle+down+first" in captured["args"]
    assert not any(a.startswith("--bind=start:") for a in captured["args"])


def test_cli_clean_eof_at_prompt_is_no(tmp_path, monkeypatch, capsys, make_repo):
    p1 = setup_env(tmp_path, monkeypatch, make_repo)
    monkeypatch.setattr(ui, "pick_files", lambda files, **k: [f["path"] for f in files if f["suspicious"]])

    def eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert cli.main(["clean", "local:" + p1]) == 0
    assert os.path.exists(os.path.join(p1, "debug.log"))


def test_cli_sessions_none_skips_fzf(tmp_path, monkeypatch, capsys, make_repo):
    setup_env(tmp_path, monkeypatch, make_repo)

    def no_fzf(*a, **k):
        raise AssertionError("fzf must not run without sessions")
    monkeypatch.setattr(ui, "run_fzf", no_fzf)
    assert cli.main(["sessions"]) == 0
    assert "no sessions found" in capsys.readouterr().out


def test_env_setup_dedupes_repo_root(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/some/other")
    cli._env_setup()
    cli._env_setup()
    parts = os.environ["PYTHONPATH"].split(os.pathsep)
    assert parts.count(cli.REPO_ROOT) == 1
    assert parts[0] == cli.REPO_ROOT
    assert "/some/other" in parts


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
    monkeypatch.setattr(ui, "pick_files", lambda files, **k: [f["path"] for f in files if f["suspicious"]])
    answers = iter(["y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.main(["clean", "local:" + p1]) == 0
    assert not os.path.exists(os.path.join(p1, "debug.log"))
    assert os.path.exists(os.path.join(p1, "README.md"))
    monkeypatch.setattr(ui, "pick_line", lambda lines, header, **k: lines[0])  # F14: shown_fields
    assert cli.main(["restore"]) == 0
    assert os.path.exists(os.path.join(p1, "debug.log"))
    answers = iter(["y", "no"])
    assert cli.main(["clean", "local:" + p1]) == 0
    real = cli.time.time()
    with monkeypatch.context() as m:
        m.setattr(cli.time, "time", lambda: real + 40 * 86400)
        assert cli.main(["trash", "--purge", "--older", "30d"]) == 0  # answered "no"
    assert "1 batch" in capsys.readouterr().out
    assert cli.main(["trash"]) == 0
    assert "p1" in capsys.readouterr().out


def test_cli_purge_yes_deletes_shown_batch(tmp_path, monkeypatch, capsys, make_repo):
    p1 = setup_env(tmp_path, monkeypatch, make_repo)
    monkeypatch.setattr(ui, "pick_files", lambda files, **k: [f["path"] for f in files if f["suspicious"]])
    answers = iter(["y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.main(["clean", "local:" + p1]) == 0
    real = cli.time.time()
    with monkeypatch.context() as m:
        m.setattr(cli.time, "time", lambda: real + 40 * 86400)
        m.setattr("builtins.input", lambda prompt="": "yes")
        assert cli.main(["trash", "--purge", "--older", "30d"]) == 0
    assert "purged 1" in capsys.readouterr().out
    assert cli.main(["trash"]) == 0
    assert capsys.readouterr().out.strip() == ""


@pytest.mark.parametrize("v", ["0d", "-3d", "0"])
def test_cli_purge_rejects_non_positive_older(v):
    with pytest.raises(SystemExit, match="--older must be > 0"):
        cli._days(v)


def test_pick_files_passes_preview(monkeypatch):
    captured = {}

    def fake_fzf(lines, args, expect=()):
        captured["args"] = args
        return "", []
    monkeypatch.setattr(ui, "run_fzf", fake_fzf)
    ui.pick_files([{"path": "a.log", "suspicious": True, "size": 1}], preview="PREVIEW {1}")
    assert "--preview=PREVIEW {1}" in captured["args"]


def test_file_preview_cmd_quotes_host_and_root():
    cmd = ui.file_preview_cmd("my host", "/a b/repo")
    assert cmd.endswith("_fpreview 'my host' '/a b/repo' {1}")


def test_cli_fpreview_local_text_dir_and_binary(tmp_path, monkeypatch, capsys, make_repo):
    p1 = setup_env(tmp_path, monkeypatch, make_repo)
    os.makedirs(os.path.join(p1, "out"))
    with open(os.path.join(p1, "out", "x.txt"), "w") as f:
        f.write("1")
    with open(os.path.join(p1, "blob.bin"), "wb") as f:
        f.write(b"\x00\x01" * 10)
    assert cli.main(["_fpreview", "local", p1, "debug.log\t"]) == 0  # fzf keeps the delimiter
    assert capsys.readouterr().out.strip() == "x"
    assert cli.main(["_fpreview", "local", p1, "out/"]) == 0
    assert "x.txt" in capsys.readouterr().out
    assert cli.main(["_fpreview", "local", p1, "blob.bin"]) == 0
    assert "binary" in capsys.readouterr().out


def test_fpreview_remote_builds_quoted_ssh(monkeypatch):
    calls = []

    class R:
        stdout = b"hello\n"
        returncode = 0
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **k: calls.append(argv) or R())
    out = cli.file_preview({"name": "vm", "ssh": "vm"}, "/home/u/a b.log")
    assert out == "hello\n"
    assert calls[0][:2] == ["ssh", "-o"] and calls[0][-2] == "vm"
    assert "'/home/u/a b.log'" in calls[0][-1]



def test_project_lines_insert_host_headers():
    rows = [row(host="mac", path="/a"), row(host="mac", path="/b"), row(host="vm", path="/c")]
    lines = ui.project_lines(rows, NOW)
    assert [ln.split("\t")[:2] for ln in lines] == [
        ["mac", ""], ["mac", "/a"], ["mac", "/b"], ["vm", ""], ["vm", "/c"]]
    assert "── mac" in lines[0].split("\t")[2]


def test_browse_stays_open_after_iterm_launch(monkeypatch):
    cfg = {"hosts": [{"name": "vm", "ssh": "vm"}], "tiers_days": {"active": 14, "stale": 90}}
    cache = {"host": "vm", "projects": [row(path="/p")]}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: [cache])
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg: [cache])
    picks = iter([("", "vm", ""), ("", "vm", "/p"), ("ctrl-r", "vm", "/p")])
    notices = []

    def fake_pick(rows, now, notice=None, select=None, board=None):
        notices.append(notice)
        try:
            return next(picks)
        except StopIteration:
            raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_project", fake_pick)
    launched = []
    monkeypatch.setattr(cli, "launch", lambda r, path, s=None: launched.append((path, s and s["id"])) or "iterm")
    assert cli.cmd_browse(cfg, None) == 0
    assert launched == [("/p", None), ("/home/u/quant", "s1")]  # header row ignored
    assert len(notices) == 4 and "opened" in notices[2]


def test_browse_exits_after_inline_launch(monkeypatch):
    cfg = {"hosts": [{"name": "vm", "ssh": "vm"}], "tiers_days": {"active": 14, "stale": 90}}
    cache = {"host": "vm", "projects": [row(path="/p")]}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: [cache])
    calls = []
    monkeypatch.setattr(ui, "pick_project", lambda rows, now, notice=None, select=None, board=None: calls.append(1) or ("", "vm", "/p"))
    monkeypatch.setattr(cli, "launch", lambda r, path, s=None: "inline")
    assert cli.cmd_browse(cfg, None) == 0
    assert calls == [1]


def test_preview_of_host_header(tmp_path, monkeypatch, capsys, make_repo):
    setup_env(tmp_path, monkeypatch, make_repo)
    assert cli.main(["sync"]) == 0
    capsys.readouterr()
    assert cli.main(["_preview", "local", "\t"]) == 0
    out = capsys.readouterr().out
    assert "local" in out and "1 project" in out


def test_pick_project_keeps_cursor_on_selected(monkeypatch):
    captured = {}

    def fake_fzf(lines, args, expect=()):
        captured["args"] = args
        return "", [lines[2]]
    monkeypatch.setattr(ui, "run_fzf", fake_fzf)
    rows = [row(host="mac", path="/a"), row(host="mac", path="/b")]
    assert ui.pick_project(rows, NOW, select=("mac", "/b")) == ("", "mac", "/b")
    assert "--bind=load:pos(3)" in captured["args"]  # line 1 is the "── mac" header
    ui.pick_project(rows, NOW, select=("mac", "/gone"))
    assert not any(a.startswith("--bind=load:pos") for a in captured["args"])


def test_preview_shows_where_you_left_off():
    s = dict(row()["sessions"][0], recent=["add tests", "run them"], last_reply="all 12 pass")
    text = ui.preview_text(row(sessions=[s]), NOW)
    lines = text.splitlines()
    i = lines.index("left off · fix rebate · 1h ago")
    assert lines[i + 1:i + 4] == ["  › add tests", "  › run them", "  ⏺ all 12 pass"]
    assert i < lines.index("progress:")
    assert "left off" not in ui.preview_text(row(sessions=[dict(s, recent=[], last_reply="")]), NOW)


def test_live_tmux_flag_and_preview():
    r = row(live_tmux=["proj-quant-ab12"])
    assert "▶1" in ui.project_line(r, NOW).split("\t")[2]
    assert "▶ live tmux: proj-quant-ab12" in ui.preview_text(r, NOW)
    assert "▶" not in ui.project_line(row(), NOW)


def test_usage_log_and_report_section(tmp_path, monkeypatch, capsys, make_repo):
    p1 = setup_env(tmp_path, monkeypatch, make_repo)
    assert cli.main(["sync"]) == 0
    monkeypatch.setattr(cli.opener, "open_command", lambda cmd, title, iterm=None: "iterm")
    monkeypatch.setattr(cli.opener, "has_iterm", lambda: True)
    r = {"host": "local", "local": True, "path": p1}
    cli.launch(r, p1)
    cli.launch(r, p1, {"id": "s1", "cwd": p1})
    events = [json.loads(ln) for ln in open(tmp_path / "cache" / "usage.jsonl")]
    assert [(e["action"], e["host"], e["path"]) for e in events] == [
        ("open", "local", p1), ("resume", "local", p1)]
    capsys.readouterr()
    assert cli.main(["report"]) == 0
    out = capsys.readouterr().out
    assert "## Your usage, last 14 days" in out and "open 1 · resume 1" in out


def test_prompt_line_and_pick_prompt_args(monkeypatch):
    r = row()
    s = r["sessions"][0]
    line = ui.prompt_line(r, s, "fix the nginx config", NOW)
    host, path, sid, text, display = line.split("\t")
    assert (host, path, sid, text) == ("vm", "/home/u/quant", "s1", "fix the nginx config")
    assert display.endswith("vm:quant                     │ fix the nginx config")
    captured = {}

    def fake_fzf(lines, args, expect=()):
        captured["args"] = args
        return "", [lines[0]]
    monkeypatch.setattr(ui, "run_fzf", fake_fzf)
    assert ui.pick_prompt([(r, s, "fix the nginx config")], NOW, "nginx") == ("vm", "/home/u/quant", "s1")
    assert "--exact" in captured["args"] and "--query=nginx" in captured["args"]
    assert "--with-nth=5.." in captured["args"]
    ui.pick_prompt([(r, s, "x")], NOW)
    assert not any(a.startswith("--query") for a in captured["args"])


def test_session_preview_text():
    s = dict(row()["sessions"][0], recent=["add tests"], last_reply="done")
    text = ui.session_preview_text(row(sessions=[s]), s, NOW, "fix the nginx config")
    lines = text.splitlines()
    assert lines[0] == "› fix the nginx config"
    assert "vm:/home/u/quant" in lines
    assert "left off · fix rebate · 1h ago" in lines and "  ⏺ done" in lines


def test_cli_grep_resumes_chosen_session(tmp_path, monkeypatch, capsys, make_repo, make_session):
    p1 = setup_env(tmp_path, monkeypatch, make_repo)
    from tests.conftest import user
    make_session(tmp_path / "isolated_home" / ".claude", p1, "sid1",
                 [user(p1, "fix the nginx config")])
    assert cli.main(["sync"]) == 0
    seen = {}

    def fake_pick(items, now, query=""):
        seen["texts"], seen["query"] = [t for _, _, t in items], query
        return "local", p1, "sid1"
    monkeypatch.setattr(ui, "pick_prompt", fake_pick)
    launched = []
    monkeypatch.setattr(cli, "launch", lambda r, path, s=None: launched.append((path, s["id"])))
    assert cli.main(["grep", "nginx", "config"]) == 0
    assert seen == {"texts": ["fix the nginx config"], "query": "nginx config"}
    assert launched == [(p1, "sid1")]
    capsys.readouterr()
    assert cli.main(["_spreview", "local\t", p1 + "\t", "sid1\t", "fix the nginx config\t"]) == 0
    assert capsys.readouterr().out.startswith("› fix the nginx config\n")


def test_cli_grep_without_prompts(tmp_path, monkeypatch, capsys, make_repo):
    setup_env(tmp_path, monkeypatch, make_repo)

    def no_fzf(*a, **k):
        raise AssertionError("fzf must not run without prompts")
    monkeypatch.setattr(ui, "run_fzf", no_fzf)
    assert cli.main(["grep"]) == 0
    assert "no prompts cached; run proj sync" in capsys.readouterr().out


def _browse_with_keys(monkeypatch, picks, sessions=None):
    cfg = {"hosts": [{"name": "vm", "ssh": "vm"}], "tiers_days": {"active": 14, "stale": 90}}
    r = row(path="/p") if sessions is None else row(path="/p", sessions=sessions)
    cache = {"host": "vm", "projects": [r]}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: [cache])
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg: [cache])
    picks = iter(picks)
    seen = []

    def fake_pick(rows, now, notice=None, select=None, board=None):
        seen.append((notice, select))
        try:
            return next(picks)
        except StopIteration:
            raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_project", fake_pick)
    launched = []
    monkeypatch.setattr(cli, "launch", lambda r, path, s=None: launched.append((path, s and s["id"])) or "iterm")
    assert cli.cmd_browse(cfg, None) == 0
    return seen, launched


def test_browse_ctrl_f_resumes_from_prompt_search(monkeypatch):
    s = dict(row()["sessions"][0], prompts=["fix the nginx config"])
    monkeypatch.setattr(ui, "pick_prompt", lambda items, now, query="": (
        ("vm", "/p", "s1") if [t for _, _, t in items] == ["fix the nginx config"] else None))
    seen, launched = _browse_with_keys(monkeypatch, [("ctrl-f", "vm", "")], sessions=[s])
    assert launched == [("/home/u/quant", "s1")]
    assert "opened vm:p (resume)" in seen[1][0] and seen[1][1] == ("vm", "/p")


def test_browse_ctrl_f_cancel_and_no_prompts(monkeypatch):
    s = dict(row()["sessions"][0], prompts=["x"])

    def cancel(items, now, query=""):
        raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_prompt", cancel)
    seen, launched = _browse_with_keys(monkeypatch, [("ctrl-f", "vm", "/p")], sessions=[s])
    assert launched == [] and seen[1] == (None, ("vm", "/p"))
    seen, launched = _browse_with_keys(monkeypatch, [("ctrl-f", "vm", "/p")])  # no prompts cached
    assert launched == [] and seen[1][0] == "no prompts cached; press ^U to sync"


def test_project_header_mentions_ctrl_f():
    assert "ctrl-f" in ui.PROJECT_KEYS and "^F: search prompts" in ui.PROJECT_HEADER


def test_names_show_path_under_home_when_nested():
    assert ui.project_name(row(home="/home/u")) == "quant"
    assert ui.project_name(row(home="/home/u", path="/home/u/daily/digest")) == "daily/digest"
    assert ui.project_name(row(home="/home/u", path="/srv/x/app")) == "app"
    assert ui.project_name(row(path="/home/u/daily/digest")) == "digest"  # old cache: no home
    r = row(home="/home/u", path="/home/u/dev/projdeck")
    assert "dev/projdeck" in ui.project_line(r, NOW).split("\t")[2]
    assert "dev/projdeck" in ui.session_line(r, r["sessions"][0], NOW).split("\t")[3]
    assert "vm:dev/projdeck" in ui.prompt_line(r, r["sessions"][0], "hi", NOW)


def test_project_line_colors_name_by_tag():
    order = ["Quant", "Invest"]
    plain = ui.project_line(row(), NOW)
    assert "\x1b[" not in plain
    tagged = ui.project_line(row(tag="Invest"), NOW, order)
    d = tagged.split("\t")[2]
    assert ui.tag_color("Invest", order) in d and "quant" in d and ui.RESET in d
    assert ui.tag_color("Quant", order) != ui.tag_color("Invest", order)
    assert ui.strip_ansi(d) == plain.split("\t")[2]  # same layout, only colored


def test_tag_legend_lists_tags_in_order():
    rows_ = [row(tag="Invest", tags=["Quant", "Invest"]), row(tag=None)]
    order = ui.tag_order(rows_)
    assert order == ["Quant", "Invest"]
    legend = ui.strip_ansi(ui.tag_legend(order))
    assert legend.index("Quant") < legend.index("Invest")
    assert ui.tag_legend([]) == ""


def test_flagged_projects_get_their_own_group_on_top():
    rows_ = [row(host="s-host", path="/h/a"), row(host="s-host", path="/h/f1", flag=True),
             row(host="em", path="/e/b"), row(host="em", path="/e/f2", flag=True,
                                              activity=NOW - 60)]
    lines = [ln.split("\t") for ln in ui.project_lines(rows_, NOW)]
    shown = [(h, p) for h, p, _ in lines]
    assert shown == [("", ""), ("em", "/e/f2"), ("s-host", "/h/f1"),  # 🚩 group, newest first
                     ("s-host", ""), ("s-host", "/h/a"), ("em", ""), ("em", "/e/b")]
    assert "🚩" in lines[0][2]
    top = ui.strip_ansi(lines[1][2])
    assert "@em" in top  # the host is named in the cross-host group
    assert "@" not in ui.strip_ansi(lines[4][2])
    assert ui.strip_ansi(ui.project_line(row(flag=True), NOW).split("\t")[2]) == \
        ui.strip_ansi(ui.project_line(row(), NOW).split("\t")[2])  # no flag column


def test_no_flag_group_without_flags():
    lines = ui.project_lines([row()], NOW)
    assert "🚩" not in "".join(lines) and lines[0].split("\t")[:2] == ["vm", ""]


def test_live_flag_variants():
    assert ui.live_flag(row()) == ""
    assert ui.live_flag(row(live_zellij=[{"name": "research", "claude": 2}])) == "▶research"
    assert ui.live_flag(row(live_zellij=[{"name": "dev", "claude": 0}])) == "▶dev·0"
    assert ui.live_flag(row(live_tmux=["proj-q-ab12"])) == "▶1"
    assert ui.live_flag(row(live_tmux=["t"], live_zellij=[{"name": "z", "claude": 1}])) == "▶2"
    r = row(live_zellij=[{"name": "research", "claude": 2}])
    assert "▶research" in ui.project_line(r, NOW).split("\t")[2]
    assert "▶ zellij: research (2 claude)" in ui.preview_text(r, NOW)
    assert ui.PROJECT_HEADER.startswith("Enter: resume")


ZROW = dict(local=True, ssh=None, mux="zellij", host="s-host", path="/home/u/quant")


def _open(monkeypatch, live, env=None, picked=None):
    for k in ("ZELLIJ", "ZELLIJ_SESSION_NAME"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(cli.agent, "zellij_by_project", lambda paths: {"/home/u/quant": live} if live else {})
    calls = {"open": [], "tab": [], "launch": []}
    monkeypatch.setattr(cli.opener, "open_command", lambda cmd, title, iterm=None: calls["open"].append(cmd) or "inline")
    monkeypatch.setattr(cli.opener, "zellij_new_tab", lambda path, title: calls["tab"].append(path))
    monkeypatch.setattr(cli, "launch", lambda r, path, s=None: calls["launch"].append(path) or "inline")
    monkeypatch.setattr(ui, "pick_live", lambda live, name: picked or live[0])
    r = row(**ZROW)
    return cli.open_project([r], r), calls


def test_enter_attaches_live_zellij_outside_zellij(monkeypatch):
    out, calls = _open(monkeypatch, [{"name": "research", "claude": 2}])
    assert out == ("inline", None)
    assert "zellij attach research" in calls["open"][0]


def test_enter_inside_zellij_with_live_session_only_tells(monkeypatch):
    out, calls = _open(monkeypatch, [{"name": "research", "claude": 2}],
                       env={"ZELLIJ": "0", "ZELLIJ_SESSION_NAME": "poly"})
    assert out[0] is None and "research" in out[1] and "Ctrl-o w" in out[1]
    assert calls == {"open": [], "tab": [], "launch": []}


def test_enter_inside_zellij_without_live_session_opens_tab(monkeypatch):
    out, calls = _open(monkeypatch, [], env={"ZELLIJ": "0", "ZELLIJ_SESSION_NAME": "poly"})
    assert out[0] == "tab" and calls["tab"] == ["/home/u/quant"]


def test_enter_without_live_session_creates_proj_session_when_zellij_works(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path))
    monkeypatch.setattr(cli.agent, "zellij_session_list", lambda *a, **k: [])
    out, calls = _open(monkeypatch, [])
    assert out == ("inline", None) and calls["launch"] == []
    assert "zellij --session proj-quant --new-session-with-layout" in calls["open"][0]


def test_enter_without_live_session_runs_picker_via_launch_when_zellij_is_unusable(monkeypatch):
    monkeypatch.setattr(cli.agent, "zellij_session_list", lambda *a, **k: None)
    out, calls = _open(monkeypatch, [])
    assert out == ("inline", None) and calls["launch"] == ["/home/u/quant"] and calls["open"] == []


def test_enter_with_two_live_sessions_asks(monkeypatch):
    two = [{"name": "a", "claude": 1}, {"name": "b", "claude": 0}]
    out, calls = _open(monkeypatch, two, picked=two[1])
    assert "zellij attach b" in calls["open"][0]


def test_launch_without_session_runs_the_picker(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.opener, "has_iterm", lambda: False)
    monkeypatch.setattr(cli.opener, "open_command", lambda cmd, title, iterm=None: seen.append(cmd) or "inline")
    cli.launch(row(mux=False), "/home/u/quant")
    assert "claude --resume" in seen[0] and "claude --resume s1" not in seen[0]


def test_project_keys_and_header_use_ctrl_y():
    assert ui.PROJECT_KEYS == ["ctrl-r", "ctrl-y", "ctrl-f", "ctrl-x", "ctrl-u", "ctrl-l", "ctrl-v"]
    assert ui.PROJECT_HEADER == ("Enter: resume · ^R: resume latest · ^Y: pick session · "
                                 "^F: search prompts · ^X: clean · ^U: sync · ^L: backlog · ^V: dashboard")


def test_browse_ctrl_y_picks_and_resumes_chosen_session(monkeypatch):
    s2 = dict(row()["sessions"][0], id="s2")
    monkeypatch.setattr(ui, "pick_session", lambda items, now: ("vm", "/p", "s2"))
    seen, launched = _browse_with_keys(monkeypatch, [("ctrl-y", "vm", "/p")], sessions=[s2])
    assert launched == [("/home/u/quant", "s2")]


def test_browse_ctrl_s_does_nothing(monkeypatch):
    def no_pick(items, now):
        raise AssertionError("must not pick a session")
    monkeypatch.setattr(ui, "pick_session", no_pick)
    for k in ("ctrl-s",):
        seen, launched = _browse_with_keys(monkeypatch, [(k, "vm", "/p")])
        assert launched == [] and seen[1][0] is None
