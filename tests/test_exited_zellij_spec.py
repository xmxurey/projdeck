"""Independent spec tests for T32: exited zellij sessions in the terminal header line 1 and in
`proj report` (## Exited zellij sessions (N))."""
import copy
import json
import os
import re
import shlex
import subprocess
import sys
import time

from projdeck import board as board_mod
from projdeck import model, report, ui

NOW = 1795000000
ZHOST = {"name": "s-host", "local": True, "mux": "zellij"}
CFG = {"hosts": [ZHOST], "tiers_days": {"active": 14, "stale": 90}}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAD = "## Exited zellij sessions"
SUFFIX = "已退出 zellij 2（proj report 看清理命令）"
CTRL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def cache(now=NOW):
    return {"schema": 1, "host": "s-host", "collected_at": now - 60, "synced_at": now - 60,
            "stale_since": None, "last_error": None, "projects": []}


def live(exited, now=NOW, key=True):
    d = {"schema": 1, "host": "s-host", "collected_at": now - 10, "source": "claude agents",
         "since": now - 86400, "sessions": [], "projects": {}}
    if key:
        d["zellij_exited"] = list(exited)
    return d


_LIVES = {}


def mk(exited, key=True):
    lives = [live(exited, key=key)]
    b = board_mod.build([cache()], lives, CFG, NOW)
    _LIVES[id(b)] = lives
    return b


def md_of(b, now=NOW, cfg=CFG, lives=None):
    caches = [cache()]
    if lives is None:
        lives = _LIVES.get(id(b), [])
    return report.render(model.rows(caches, cfg, now), caches, now, board=b, lives=lives, cfg=cfg)


def section(md):
    assert HEAD in md
    tail = md.split(HEAD, 1)[1]
    head_line, _, rest = tail.partition("\n")
    body = re.split(r"\n## ", rest, 1)[0]
    return head_line, [ln for ln in body.split("\n") if ln.strip()]


def without_section(md):
    if HEAD not in md:
        return md
    pre, tail = md.split(HEAD, 1)
    parts = re.split(r"\n## ", tail, 1)
    return pre + ("\n## " + parts[1] if len(parts) > 1 else "")


# ---- acceptance 1: header ----

def line1(b):
    return ui.strip_ansi(ui.header_lines(b, NOW)[0])


def test_header_suffix_when_two_exited():
    b = mk(["dao", "my s"])
    assert b["summary"]["zellij_exited_n"] == 2
    assert line1(b).endswith(SUFFIX)
    assert len(ui.header_lines(b, NOW)) == 2


def test_header_count_follows_n():
    assert line1(mk(["a", "b", "c"])).endswith("已退出 zellij 3（proj report 看清理命令）")
    assert line1(mk(["a"])).endswith("已退出 zellij 1（proj report 看清理命令）")


def test_header_zero_has_no_segment_and_equals_baseline():
    b0 = mk([])
    assert b0["summary"]["zellij_exited_n"] == 0
    l0 = ui.header_lines(b0, NOW)
    assert "已退出" not in l0[0] and "zellij" not in ui.strip_ansi(l0[0])
    # same board as an old cache without the part (keys removed): identical output
    legacy = copy.deepcopy(b0)
    legacy["summary"].pop("zellij_exited", None)
    legacy["summary"].pop("zellij_exited_n", None)
    assert ui.header_lines(legacy, NOW) == l0
    # a live cache without the field at all
    assert ui.header_lines(mk([], key=False), NOW) == l0


def test_header_only_line1_changes_and_prefix_is_baseline():
    base = ui.header_lines(mk([]), NOW)
    got = ui.header_lines(mk(["dao", "my s"]), NOW)
    assert got[1:] == base[1:]
    p0, p2 = ui.strip_ansi(base[0]), ui.strip_ansi(got[0])
    assert p2.startswith(p0.rstrip())
    assert "· " + SUFFIX in p2


def test_header_hostile_names_do_not_leak():
    b = mk(["a\x1b[31mb", "x`y"])
    l1 = ui.header_lines(b, NOW)[0]
    assert "\x1b[31mb" not in l1 and "x`y" not in l1
    assert line1(b).endswith(SUFFIX)


# ---- acceptance 2: render ----

UNSAFE = "- `s-host`: 名字含控制字符，先用 `zellij list-sessions` 核对再手动删除"
UNKNOWN = "- `s-host`: unknown (no live data)"
CMD = re.compile(r"`+ ?(zellij delete-session -- .*?) ?`+(?: [^`]*)?$")


def entries(lines):
    """Lines of the section after the live-age line(s): those starting with '- `'."""
    return [ln for ln in lines if ln.startswith("- `")]


def cmd_names(lines):
    """For every line that gives a delete command: the session name shlex parses out of it."""
    out = []
    for ln in lines:
        m = CMD.search(ln)
        if m:
            parts = shlex.split(m.group(1))
            # F11: options end with "--" before the name
            assert parts[:3] == ["zellij", "delete-session", "--"] and len(parts) == 4, ln
            out.append(parts[3])
    return out


def test_render_section_two_lines_in_order():
    md = md_of(mk(["dao", "my s"]))
    assert "## Exited zellij sessions (2)" in md
    head, lines = section(md)
    assert entries(lines) == ["- `s-host`: `zellij delete-session -- dao`",
                              "- `s-host`: `zellij delete-session -- 'my s'`"]


def test_render_live_age_line_under_heading():
    md = md_of(mk(["dao"]))
    body = md.split(HEAD, 1)[1].split("\n", 1)[1].lstrip("\n")
    first = body.split("\n", 1)[0]
    assert "s-host" in first and not first.startswith("- `s-host`: `zellij")
    assert "dao" not in first


def test_render_none_only_when_checked_and_empty():
    md = md_of(mk([]))
    head, lines = section(md)
    assert "(0)" in head and "- none" in lines
    assert "unknown" not in md.split(HEAD, 1)[1].split("\n## ", 1)[0]


def test_render_unknown_without_live():
    b = board_mod.build([cache()], [], CFG, NOW)
    head, lines = section(md_of(b, lives=[]))
    assert head.strip() == "(?)"
    assert UNKNOWN in lines and "- none" not in lines


TWO = {"hosts": [ZHOST, {"name": "em", "local": True, "mux": "zellij"}]}


def test_render_mixed_known_and_unknown_hosts():
    cfg = TWO
    caches = [cache(), dict(cache(), host="em")]
    lives = [live(["a"])]
    b = board_mod.build(caches, lives, cfg, NOW)
    md = report.render(model.rows(caches, cfg, NOW), caches, NOW, board=b, lives=lives, cfg=cfg)
    head, lines = section(md)
    assert "?" not in head
    assert "- `s-host`: `zellij delete-session -- a`" in lines
    assert "- `em`: unknown (no live data)" in lines


def test_render_proj_prefixed_name_gets_note():
    # the note needs a project whose current session name is proj-foo
    b = mk(["proj-foo", "mine"])
    row = dict(model.ROW_DEFAULTS, host="s-host", path="/home/u/foo", home="/home/u",
               tier="active", activity=NOW)
    md = report.render([row], [cache()], NOW, board=b, lives=_LIVES.get(id(b), []), cfg=CFG)
    _, lines = section(md)
    by = {("proj-foo" in ln): ln for ln in entries(lines)}
    assert "proj 下次在该项目按 Enter 时会自动删除" in by[True]
    assert "自动删除" not in by[False]
    assert "zellij delete-session -- proj-foo" in by[True]


def test_render_multiple_hosts_names_each_host():
    cfg = TWO
    lives = [live(["a"]), dict(live(["b"]), host="em")]
    caches = [cache(), dict(cache(), host="em")]
    b = board_mod.build(caches, lives, cfg, NOW)
    md = report.render(model.rows(caches, cfg, NOW), caches, NOW, board=b, lives=lives, cfg=cfg)
    _, lines = section(md)
    assert "## Exited zellij sessions (2)" in md
    assert "- `s-host`: `zellij delete-session -- a`" in lines
    assert "- `em`: `zellij delete-session -- b`" in lines


UNSAFE_NAMES = ["bad\x1b[31mname", "nl\nx", "tab\there", "bell\x07\x00z", "c1\x85y",
                "a\x1b]0;pwn\x07b"]


def test_render_unsafe_names_get_no_command_and_no_name():
    md = md_of(mk(UNSAFE_NAMES))
    head, lines = section(md)
    assert "(6)" in head
    ents = entries(lines)
    assert ents == [UNSAFE] * 6  # one line per name, a newline in a name cannot split or forge
    assert cmd_names(lines) == []
    assert "\x1b" not in md and not CTRL.search(md) and "\x85" not in md
    for frag in ("bad", "nl", "tab", "bell", "c1", "pwn"):
        assert frag not in md.split(HEAD, 1)[1].split("\n## ", 1)[0].replace("list-sessions", ""), frag


def test_render_every_given_command_names_the_original_session():
    names = ["dao", "my s", "x`y", "`; rm -rf ~; `", "it's", "a b'c\"d", "$(id)", "proj-p",
             "e\u00e9\u4e2d", "semi;colon", "-dash"] + UNSAFE_NAMES
    _, lines = section(md_of(mk(names)))
    given = cmd_names(lines)
    safe = [n for n in names if n not in UNSAFE_NAMES]
    assert given == safe  # exactly the unchanged names, in order, each round-tripping exactly
    assert len(entries(lines)) == len(names)


def test_render_backtick_name_cannot_break_inline_code():
    b = mk(["x`y", "`; rm -rf ~; `"])
    head, lines = section(md_of(b))
    ents = entries(lines)
    assert len(ents) == 2 and "(2)" in head
    assert cmd_names(ents) == ["x`y", "`; rm -rf ~; `"]
    for ln in ents:
        rest = ln[len("- `s-host`: "):]
        if rest.startswith("`") and not rest.startswith("``"):
            assert "`" not in rest[1:-1], ln
        if "`" in rest.strip("`").strip():
            assert rest.startswith("``") and rest.rstrip().endswith("``"), ln


def test_render_remote_and_tmux_hosts_not_listed():
    cfg = {"hosts": [ZHOST, {"name": "em", "ssh": "em"},
                     {"name": "tm", "local": True, "mux": "tmux"}]}
    caches = [cache(), dict(cache(), host="em"), dict(cache(), host="tm")]
    lives = [live(["a"]), dict(live(["b"]), host="em"), dict(live(["c"]), host="tm")]
    b = board_mod.build(caches, lives, cfg, NOW)
    md = report.render(model.rows(caches, cfg, NOW), caches, NOW, board=b, lives=lives, cfg=cfg)
    sec = md.split(HEAD, 1)[1].split("\n## ", 1)[0]
    assert "- `s-host`: `zellij delete-session -- a`" in sec
    for host in ("em", "tm"):
        assert "`%s`" % host not in sec and "- %s:" % host not in sec
    assert "unknown" not in sec


def test_render_remote_host_without_live_not_unknown():
    cfg = {"hosts": [ZHOST, {"name": "em", "ssh": "em"}]}
    caches = [cache(), dict(cache(), host="em")]
    lives = [live(["a"])]
    b = board_mod.build(caches, lives, cfg, NOW)
    md = report.render(model.rows(caches, cfg, NOW), caches, NOW, board=b, lives=lives, cfg=cfg)
    sec = md.split(HEAD, 1)[1].split("\n## ", 1)[0]
    assert "em" not in sec.replace("delete-session", "")


def test_render_no_zellij_host_in_scope():
    cfg = {"hosts": [{"name": "s-host", "ssh": "s-host"}, {"name": "tm", "local": True, "mux": "tmux"}]}
    caches = [cache(), dict(cache(), host="tm")]
    lives = [live(["a"]), dict(live(["b"]), host="tm")]
    b = board_mod.build(caches, lives, cfg, NOW)
    for kw in (dict(lives=lives, cfg=cfg), dict(lives=None, cfg=cfg), dict(lives=lives, cfg=None)):
        md = report.render(model.rows(caches, cfg, NOW), caches, NOW, board=b, **kw)
        head, lines = section(md)
        assert "- none (no zellij hosts)" in lines
        assert "delete-session" not in md and "unknown" not in md.split(HEAD, 1)[1].split("\n## ", 1)[0]


def test_render_without_lives_in_scope_host_is_unknown():
    b = mk(["dao"])
    md = report.render(model.rows([cache()], CFG, NOW), [cache()], NOW, board=b, cfg=CFG)
    head, lines = section(md)
    assert head.strip() == "(?)" and UNKNOWN in lines


def test_render_rest_of_report_unchanged():
    plain_md = report.render(model.rows([cache()], CFG, NOW), [cache()], NOW)
    for b in (mk([]), mk(["dao", "my s"])):
        assert without_section(md_of(b)).rstrip() == without_section(plain_md).rstrip()


def test_render_board_none_still_works():
    md = report.render(model.rows([cache()], CFG, NOW), [cache()], NOW, board=None)
    assert isinstance(md, str) and "delete-session" not in md


# ---- acceptance 3: proj report CLI ----

def run_report(tmp_path, live_doc, write_live=True):
    home = tmp_path / "home"
    cache_dir = tmp_path / "cache"
    (cache_dir / "live").mkdir(parents=True)
    home.mkdir()
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"hosts": [ZHOST]}))
    now = int(time.time())
    (cache_dir / "s-host.json").write_text(json.dumps(cache(now)))
    if write_live:
        (cache_dir / "live" / "s-host.json").write_text(json.dumps(live_doc(now)))
    env = dict(os.environ, HOME=str(home), PROJ_CACHE=str(cache_dir), PROJ_CONFIG=str(cfgp))
    calls = os.environ["PROJ_STUB_CALLS"]
    env["PROJ_STUB_CALLS"] = calls
    r = subprocess.run([sys.executable, "-m", "projdeck", "report"], cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                       timeout=60)
    return r, calls


def zellij_calls(path):
    if not os.path.exists(path):
        return []
    return [ln for ln in open(path).read().splitlines() if ln.startswith("zellij")]


def test_cli_report_lists_exited_and_never_calls_zellij(tmp_path):
    r, calls = run_report(tmp_path, lambda now: live(["dao", "my s"], now))
    assert r.returncode == 0, r.stderr
    head, lines = section(r.stdout)
    assert "(2)" in head
    assert entries(lines) == ["- `s-host`: `zellij delete-session -- dao`",
                              "- `s-host`: `zellij delete-session -- 'my s'`"]
    assert zellij_calls(calls) == []


def test_cli_report_without_live_cache_is_unknown(tmp_path):
    r, calls = run_report(tmp_path, None, write_live=False)
    assert r.returncode == 0, r.stderr
    head, lines = section(r.stdout)
    assert head.strip() == "(?)" and UNKNOWN in lines and "- none" not in lines
    assert zellij_calls(calls) == []


def test_cli_report_live_without_field_is_unknown(tmp_path):
    r, _ = run_report(tmp_path, lambda now: live([], now, key=False))
    assert r.returncode == 0, r.stderr
    head, lines = section(r.stdout)
    assert head.strip() == "(?)" and UNKNOWN in lines


def test_cli_report_checked_and_empty_says_none(tmp_path):
    r, _ = run_report(tmp_path, lambda now: live([], now))
    assert r.returncode == 0, r.stderr
    head, lines = section(r.stdout)
    assert "(0)" in head and "- none" in lines and "unknown" not in "\n".join(lines)


def test_cli_report_hostile_name_is_clean(tmp_path):
    r, calls = run_report(tmp_path, lambda now: live(["a\x1b]0;pwn\x07b", "q`z", "ok"], now))
    assert r.returncode == 0, r.stderr
    assert "\x1b" not in r.stdout and not CTRL.search(r.stdout)
    assert "pwn" not in r.stdout
    head, lines = section(r.stdout)
    assert len(entries(lines)) == 3
    assert UNSAFE in lines
    assert cmd_names(lines) == ["q`z", "ok"]
    assert zellij_calls(calls) == []


def test_cli_report_live_field_none_is_unknown(tmp_path):
    def doc(now):
        d = live([], now)
        d["zellij_exited"] = None
        return d
    r, _ = run_report(tmp_path, doc)
    assert r.returncode == 0, r.stderr
    head, lines = section(r.stdout)
    assert head.strip() == "(?)" and UNKNOWN in lines and "- none" not in lines
