"""Exited zellij sessions in the terminal header and `proj report` (D15, T32)."""
import json
import os
import re
import shlex
import time

from projdeck import cli, config, report, sync, ui

NOW = 1790000000
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SUFFIX = "已退出 zellij 2（proj report 看清理命令）"


def exited(*names, host="s-host"):
    return [{"host": host, "name": n, "cmd": "zellij delete-session " + shlex.quote(n)}
            for n in names]


def board_with(n_rows):
    return {"hosts": [{"name": "s-host", "live_at": NOW - 30}],
            "summary": {"waiting": 0, "busy": 0, "idle": 0, "open_todos": 1, "today": {},
                        "zellij_exited": n_rows, "zellij_exited_n": len(n_rows)}}


def plain(line):
    return ANSI.sub("", line)


def test_header_first_line_ends_with_exited_count():
    l1, l2 = ui.header_lines(board_with(exited("dao", "x")), NOW)
    assert plain(l1).endswith(SUFFIX)
    assert "已退出" not in l2


def test_header_zero_count_is_identical_to_before():
    with_zero = ui.header_lines(board_with([]), NOW)
    b = board_with([])
    del b["summary"]["zellij_exited"], b["summary"]["zellij_exited_n"]
    assert with_zero == ui.header_lines(b, NOW)
    assert "已退出" not in with_zero[0]


def test_header_without_summary_keys_has_no_segment():
    assert "已退出" not in ui.header_lines({"hosts": [], "summary": {}}, NOW)[0]


ZELLIJ = {"name": "s-host", "local": True, "mux": "zellij"}
REMOTE = {"name": "em", "ssh": "em"}
TMUX = {"name": "tm", "local": True, "mux": "tmux"}


def bd(exited):
    return {"summary": {"zellij_exited": exited, "zellij_exited_n": len(exited)}}


def lv(name, exited, at=NOW - 120):
    return {"host": name, "collected_at": at, "zellij_exited": exited}


def render(board, hosts=(ZELLIJ,), lives=(), rows=()):
    return report.render(list(rows), [], NOW, board=board, lives=list(lives), cfg={"hosts": list(hosts)})


def sec_of(out):
    return out.split("## Exited zellij sessions")[1]


def test_zellij_in_scope_is_the_condition_live_request_uses():
    for h in (ZELLIJ, REMOTE, TMUX, {"name": "x", "local": True}):
        assert config.zellij_in_scope(h) == bool(sync.live_request({"hosts": [h]}, h, []).get("zellij"))
    assert config.zellij_in_scope(ZELLIJ) and not config.zellij_in_scope(REMOTE)
    assert not config.zellij_in_scope(TMUX)


def test_report_lists_cleanup_commands():
    out = render(bd(exited("dao", "my s")), lives=[lv("s-host", ["dao", "my s"])])
    assert "## Exited zellij sessions (2)\n" in out
    assert "- `s-host`: `zellij delete-session dao`\n" in out
    assert "- `s-host`: `zellij delete-session 'my s'`\n" in out


def test_report_none_only_when_checked_and_really_empty():
    out = render(bd([]), lives=[lv("s-host", [])])
    assert "## Exited zellij sessions (0)\n" in out and "- none\n" in out


def test_report_in_scope_host_without_data_is_unknown():
    for lives in ([], [lv("s-host", None)], [{"host": "s-host"}]):  # no cache / no field
        out = render(bd([]), lives=lives)
        assert "## Exited zellij sessions (?)\n" in out
        assert "- `s-host`: unknown (no live data)\n" in out
        assert "- none" not in sec_of(out)


def test_report_lives_none_means_no_data_for_in_scope_hosts():
    out = report.render([], [], NOW, board=bd([]), lives=None, cfg={"hosts": [ZELLIJ]})
    assert "(?)" in out and "- `s-host`: unknown (no live data)" in out


def test_report_out_of_scope_hosts_are_not_listed_and_not_unknown():
    out = render(bd([]), hosts=(ZELLIJ, REMOTE, TMUX),
                 lives=[lv("s-host", []), lv("em", None, NOW - 30), lv("tm", None)])
    sec = sec_of(out)
    assert "(0)" in sec and "- none\n" in sec
    assert "em" not in sec and "tm" not in sec and "unknown" not in sec


def test_report_no_zellij_hosts_at_all():
    out = render(bd([]), hosts=(REMOTE, TMUX), lives=[lv("em", None)])
    assert "## Exited zellij sessions (0)\n\n- none (no zellij hosts)\n" in out


def test_report_mixed_known_and_unknown_in_scope_hosts():
    z2 = {"name": "z2", "local": True, "mux": "zellij"}
    out = render(bd(exited("dao")), hosts=(ZELLIJ, z2), lives=[lv("s-host", ["dao"])])
    assert "## Exited zellij sessions (1)\n" in out
    assert "- `s-host`: `zellij delete-session dao`\n" in out
    assert "- `z2`: unknown (no live data)\n" in out
    assert "- none" not in sec_of(out)


def test_report_shows_live_cache_age_per_in_scope_host():
    z2 = {"name": "z2", "local": True, "mux": "zellij"}
    out = render(bd(exited("dao")), hosts=(ZELLIJ, z2, REMOTE),
                 lives=[lv("s-host", ["dao"], NOW - 7200), lv("em", None)])
    sec = sec_of(out)
    assert "- s-host: live data 2h ago\n" in sec
    assert "- z2: no live data\n" in sec
    assert "em:" not in sec


def test_report_proj_session_gets_auto_delete_note():
    from projdeck.model import ROW_DEFAULTS
    row = dict(ROW_DEFAULTS, host="s-host", path="/home/u/x", home="/home/u", tier="active", activity=NOW)
    out = render(bd(exited("proj-x", "dao")), lives=[lv("s-host", [])], rows=[row])
    line = [ln for ln in out.splitlines() if "delete-session proj-x" in ln][0]
    assert "proj 下次在该项目按 Enter 时会自动删除" in line
    plain = [ln for ln in out.splitlines() if "delete-session dao" in ln][0]
    assert "自动删除" not in plain


def test_report_name_changed_by_safe_gets_no_command():
    names = ["a\x1b[31mb", "tab\there", "nl\nx", "x\x85y", "bell\x07"]
    b = bd([{"host": "s-host", "name": n, "cmd": "zellij delete-session " + shlex.quote(n)}
            for n in names])
    out = render(b, lives=[lv("s-host", [])])
    sec = out.split("## Exited zellij sessions")[1]
    assert "delete-session" not in sec
    assert sec.count("名字含控制字符，先用 `zellij list-sessions` 核对再手动删除") == 5
    assert "\x1b" not in out and "\x07" not in out and "\x85" not in out
    assert "ab" not in sec.replace("list-sessions", "")  # the name itself is not printed


def test_report_backtick_name_still_fenced():
    n = "a`b"
    b = bd([{"host": "s-host", "name": n, "cmd": "zellij delete-session " + shlex.quote(n)}])
    assert "- `s-host`: `` zellij delete-session 'a`b' ``\n" in render(b, lives=[lv("s-host", [])])


def _env(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"hosts": [ZELLIJ]}))
    monkeypatch.setenv("PROJ_CONFIG", str(cfgp))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))


def _put_live(zellij_exited):
    sync._write_json(sync._live_path("s-host"), {
        "schema": 1, "host": "s-host", "collected_at": time.time(), "source": "claude agents",
        "since": 0, "sessions": [], "projects": {}, "zellij_exited": zellij_exited})


def _zellij_calls():
    p = os.environ["PROJ_STUB_CALLS"]
    return open(p).read() if os.path.exists(p) else ""


def test_cmd_report_reads_live_cache_and_never_calls_zellij(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    _put_live(["dao", "my s"])
    assert cli.main(["report"]) == 0
    out = capsys.readouterr().out
    assert "## Exited zellij sessions (2)" in out
    assert "- `s-host`: `zellij delete-session -- dao`" in out  # F11: "--" before the name
    assert "- `s-host`: `zellij delete-session -- 'my s'`" in out
    assert _zellij_calls() == ""


def test_cmd_report_without_live_cache_says_unknown(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    assert cli.main(["report"]) == 0
    assert "## Exited zellij sessions (?)" in capsys.readouterr().out
    assert _zellij_calls() == ""


def test_cmd_report_does_not_recompute_live(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "fresh_lives", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fresh_lives")))
    assert cli.main(["report"]) == 0


def test_cmd_report_live_cache_without_field_is_unknown_not_none(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch)
    sync._write_json(sync._live_path("s-host"), {
        "schema": 1, "host": "s-host", "collected_at": time.time(), "sessions": [], "projects": {}})
    assert cli.main(["report"]) == 0
    out = capsys.readouterr().out.split("## Exited zellij sessions")[1]
    assert out.startswith(" (?)") and "- `s-host`: unknown (no live data)" in out
    assert "- none" not in out and "- s-host: live data" in out


def test_report_only_in_scope_hosts_entries_and_count():
    b = bd(exited("a", host="s-host") + exited("b", host="em") + exited("c", host="tm"))
    out = render(b, hosts=(ZELLIJ, REMOTE, TMUX),
                 lives=[lv("s-host", ["a"]), lv("em", ["b"]), lv("tm", ["c"])])
    sec = sec_of(out)
    assert "## Exited zellij sessions (1)\n" in out
    assert "delete-session a" in sec
    assert "delete-session b" not in sec and "delete-session c" not in sec


def test_report_no_lives_given_lists_no_commands():
    out = report.render([], [], NOW, board=bd(exited("a")), lives=None, cfg={"hosts": [ZELLIJ]})
    sec = sec_of(out)
    assert "delete-session" not in sec and "(?)" in out and "unknown" in sec
