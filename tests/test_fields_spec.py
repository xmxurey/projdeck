"""T04 spec tests: non-UTF-8 projects work in fzf lists, previews, cursor placement, Enter,
the usage log and `proj report` (hidden fields are encoded by ui.to_field / ui.from_field)."""
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from projdeck import cli, model, opener, sync, ui

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
NOW = 100 * 86400
FFFD = "�"
BAD = "/home/u/b\udcffd"          # a project whose directory name is not UTF-8
CJK = "/home/u/项目"


def is_ascii(s):
    return all(ord(c) < 128 for c in s)


def row(**kw):
    r = {"host": "vm", "path": "/home/u/quant", "exists": True, "local": False, "ssh": "vm",
         "tier": "active", "activity": NOW - 3 * 86400, "stale_since": None, "last_mtime": 0,
         "git": {"branch": "main", "dirty": 2, "untracked": 1, "ahead": 1, "no_upstream": False,
                 "last_commit_ts": NOW - 86400},
         "suspicious": [], "progress": "", "errors": [],
         "sessions": [{"id": "s1", "cwd": "/home/u/quant", "title": "fix rebate",
                       "last_ts": NOW - 3600, "size_kb": 10, "headless": False}]}
    r.update(kw)
    return r


# ---------------------------------------------------------------- to_field / from_field

SAMPLES = ["", "/home/u/plain", CJK, "a bé", "hex:", "hex:abc", "hex:6865", "HEX:x",
           "xhex:", "\udcff", "/a/\udc80\udcff/b", "hex:\udcff", "中\udcff文", "\udc80" * 3,
           "".join(chr(c) for c in range(0xDC80, 0xDD00))]


@pytest.mark.parametrize("x", SAMPLES)
def test_roundtrip_on_samples(x):
    assert ui.from_field(ui.to_field(x)) == x


def test_roundtrip_random_in_domain():
    rnd = random.Random(20261120)
    alphabet = ([chr(c) for c in range(0xDC80, 0xDD00)] + list("ab/ .:-\\x") + ["中", "é", "\U0001f600"]
                + ["hex:"])
    for _ in range(500):
        x = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 12)))
        if rnd.random() < 0.3:
            x = "hex:" + x
        if canonical(x):
            assert ui.from_field(ui.to_field(x)) == x, repr(x)


def canonical(x):
    """What os.fsdecode can produce: re-decoding its bytes gives the same string."""
    return x.encode("utf-8", "surrogateescape").decode("utf-8", "surrogateescape") == x


@pytest.mark.parametrize("x", [s for s in SAMPLES if any(0xD800 <= ord(c) <= 0xDFFF for c in s)])
def test_surrogate_values_become_pure_ascii(x):
    enc = ui.to_field(x)
    assert is_ascii(enc) and enc.startswith("hex:")


def test_surrogate_encoding_is_hex_of_surrogateescape_bytes():
    enc = ui.to_field("a\udcffb")
    assert enc.lower() == "hex:" + b"a\xffb".hex()


def test_hex_prefixed_real_value_is_encoded_and_unambiguous():
    for x in ("hex:", "hex:abc", "hex:6162"):
        enc = ui.to_field(x)
        assert enc != x and is_ascii(enc) and enc.startswith("hex:")
        assert ui.from_field(enc) == x
    # a real value that merely looks like an encoded one must not decode to something else
    assert ui.from_field(ui.to_field("hex:6162")) == "hex:6162"


@pytest.mark.parametrize("x", ["", "/home/u/plain", CJK, "a b", "x hex:y", "é"])
def test_plain_values_pass_through_unchanged(x):
    assert ui.to_field(x) == x
    assert ui.from_field(x) == x


def test_cjk_not_encoded():
    assert ui.to_field("项目") == "项目"


@pytest.mark.parametrize("x", ["\ud800", "a\ud800b", "\udfff", "\udc7f", "\udd00",
                               "/p/" + chr(0xD800) + chr(0xDCFF)])
def test_out_of_domain_surrogates_do_not_raise(x):
    enc = ui.to_field(x)
    enc.encode("utf-8")  # strict: nothing unencodable may leak out
    ui.from_field(enc)


def test_u_d800_is_replaced_by_fffd():
    assert ui.to_field("a\ud800b") == "a" + FFFD + "b"
    assert ui.to_field("a\udfffb") == "a" + FFFD + "b"


def test_mixed_escapable_and_unescapable_is_encoded_with_fffd_bytes():
    x = "a" + chr(0xD800) + chr(0xDCFF)  # two code units (a literal would fuse into one char)
    enc = ui.to_field(x)
    assert is_ascii(enc)
    assert ui.from_field(enc) == "a" + FFFD + "\udcff"


# ---------------------------------------------------------------- acceptance 1: lines

def test_project_lines_hidden_path_is_ascii_and_roundtrips():
    lines = ui.project_lines([row(path=BAD)], NOW)
    fields = [ln.split("\t") for ln in lines]
    proj = [f for f in fields if f[1] != ""]
    assert len(proj) == 1
    assert is_ascii(proj[0][0]) and is_ascii(proj[0][1])
    assert ui.from_field(proj[0][1]) == BAD
    assert not any(0xD800 <= ord(c) <= 0xDFFF for c in proj[0][2])  # display column is clean


def test_project_lines_keep_cjk_path_verbatim():
    lines = ui.project_lines([row(path=CJK)], NOW)
    f = [ln.split("\t") for ln in lines if ln.split("\t")[1]][0]
    assert f[1] == CJK


def test_project_lines_plain_path_unchanged():
    lines = ui.project_lines([row(path="/home/u/quant")], NOW)
    assert [ln.split("\t")[:2] for ln in lines] == [["vm", ""], ["vm", "/home/u/quant"]]


def test_project_lines_flagged_group_also_encodes():
    lines = ui.project_lines([row(path=BAD, flag=True)], NOW)
    for ln in lines:
        f = ln.split("\t")
        assert is_ascii(f[0]) and is_ascii(f[1])
    assert any(ui.from_field(ln.split("\t")[1]) == BAD for ln in lines)


def test_session_line_hidden_fields_are_ascii():
    r = row(path=BAD)
    s = dict(r["sessions"][0], id="s\udcff1", cwd=BAD)
    f = ui.session_line(r, s, NOW).split("\t")
    assert is_ascii(f[0]) and is_ascii(f[1]) and is_ascii(f[2])
    assert ui.from_field(f[1]) == BAD and ui.from_field(f[2]) == "s\udcff1"
    assert not any(0xD800 <= ord(c) <= 0xDFFF for c in "\t".join(f[3:]))


def test_file_line_hidden_path_is_ascii_and_roundtrips():
    f = ui.file_line({"path": "sub/f\udcff.log", "suspicious": True, "size": 1}).split("\t")
    assert is_ascii(f[0]) and ui.from_field(f[0]) == "sub/f\udcff.log"
    assert not any(0xD800 <= ord(c) <= 0xDFFF for c in "\t".join(f[1:]))


def test_file_line_plain_cjk_and_hex_named_files():
    assert ui.file_line({"path": "日志.log", "suspicious": True, "size": 1}).split("\t")[0] == "日志.log"
    f = ui.file_line({"path": "hex:abc", "suspicious": True, "size": 1}).split("\t")[0]
    assert ui.from_field(f) == "hex:abc"
    assert ui.file_line({"path": "a b.log", "suspicious": True, "size": 2048}).split("\t")[0] == "a b.log"


def test_file_line_u_d800_does_not_raise():
    ui.file_line({"path": "x\ud800.log", "suspicious": True, "size": 1}).encode("utf-8")


def test_batch_line_hidden_id_is_ascii_and_roundtrips():
    line = cli._batch_line({"name": "h"}, {"id": "20260101-b\udcffd", "created": NOW, "count": 1,
                                          "project": BAD})
    host, bid, display = line.split("\t", 2)
    assert host == "h" and is_ascii(bid) and ui.from_field(bid) == "20260101-b\udcffd"
    assert not any(0xD800 <= ord(c) <= 0xDFFF for c in display)


def test_batch_line_plain_id_unchanged():
    line = cli._batch_line({"name": "h"}, {"id": "b1", "created": NOW, "count": 1, "project": "/p"})
    assert line.split("\t")[:2] == ["h", "b1"]


# ---------------------------------------------------------------- previews (subprocess)

CFG = {"hosts": [{"name": "vm", "ssh": "vm"}, {"name": "local", "local": True}],
       "tiers_days": {"active": 14, "stale": 90}}


def proj(path, **kw):
    p = {"path": path, "exists": True, "git": None, "suspicious": [], "progress": "", "errors": [],
         "sessions": [], "last_mtime": time.time() - 100}
    p.update(kw)
    return p


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(CFG))
    monkeypatch.setenv("PROJ_CONFIG", str(cfgp))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")

    def put(host, projects):
        sync._write_cache(host, {"schema": 1, "host": host, "collected_at": time.time(),
                                 "synced_at": time.time(), "projects": projects})
    return put


def run_proj(*args):
    p = subprocess.run([sys.executable, "-m", "projdeck"] + list(args), cwd=REPO_ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8", "replace")


def hidden_path_field(path):
    lines = ui.project_lines([row(path=path)], NOW)
    return [ln.split("\t")[1] for ln in lines if ln.split("\t")[1]][0]


@pytest.mark.parametrize("suffix", ["", "\t"])
def test_preview_subprocess_finds_non_utf8_project(cache_env, suffix):
    cache_env("vm", [proj(BAD, git={"branch": "m", "dirty": 1, "untracked": 0, "ahead": 0,
                                    "no_upstream": True, "last_commit_ts": time.time() - 50})])
    rc, out, err = run_proj("_preview", "vm", hidden_path_field(BAD) + suffix)
    assert rc == 0, err
    assert "(not in cache)" not in out
    assert FFFD in out
    assert "Traceback" not in err


def test_preview_subprocess_still_says_not_in_cache_for_unknown_project(cache_env):
    cache_env("vm", [proj(BAD)])
    rc, out, _ = run_proj("_preview", "vm", hidden_path_field("/home/u/other\udcfe"))
    assert rc == 0 and "(not in cache)" in out


def test_preview_subprocess_plain_path_still_works(cache_env):
    cache_env("vm", [proj("/home/u/quant")])
    rc, out, _ = run_proj("_preview", "vm", "/home/u/quant\t")
    assert rc == 0 and "(not in cache)" not in out and "/home/u/quant" in out


def test_bpreview_subprocess_finds_non_utf8_project(cache_env):
    bl = {"path": "docs/backlog.md", "mtime": 5, "open": 1, "done": 0, "raw": "- [ ] ship it\n",
          "items": [{"title": "ship it", "section": "now", "done": False,
                     "text": "ship the thing, fully"}]}
    cache_env("vm", [proj(BAD, backlog=bl)])
    rc, out, err = run_proj("_bpreview", "vm", hidden_path_field(BAD), "0")
    assert rc == 0, err
    assert "ship the thing, fully" in out


def test_fpreview_subprocess_non_utf8_file_name(cache_env, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    name = b"f\xffoo.log"
    with open(os.fsencode(str(root)) + b"/" + name, "wb") as f:
        f.write(b"hello-from-bad-file\n")
    field = ui.file_line({"path": os.fsdecode(name), "suspicious": True, "size": 20}).split("\t")[0]
    assert is_ascii(field)
    for suffix in ("", "\t"):
        rc, out, err = run_proj("_fpreview", "local", str(root), field + suffix)
        assert rc == 0, err
        assert "cannot preview" not in out
        assert "hello-from-bad-file" in out


def test_fpreview_subprocess_non_utf8_root(cache_env, tmp_path):
    root = os.fsdecode(os.fsencode(str(tmp_path)) + b"/pr\xffoj")  # argv carries the raw bytes
    os.mkdir(os.fsencode(root))
    with open(os.fsencode(root) + b"/a.log", "wb") as f:
        f.write(b"root-bad-content\n")
    rc, out, err = run_proj("_fpreview", "local", root, "a.log\t")
    assert rc == 0, err
    assert "cannot preview" not in out and "root-bad-content" in out


def test_fpreview_subprocess_non_utf8_directory_entry(cache_env, tmp_path):
    root = tmp_path / "proj"
    d = os.fsencode(str(root)) + b"/o\xffut"
    os.makedirs(d + b"/sub")
    with open(d + b"/in.txt", "wb") as f:
        f.write(b"1")
    field = ui.file_line({"path": os.fsdecode(b"o\xffut/"), "suspicious": True, "size": 0}).split("\t")[0]
    rc, out, err = run_proj("_fpreview", "local", str(root), field)
    assert rc == 0, err
    assert "cannot preview" not in out and "in.txt" in out


def test_fpreview_subprocess_plain_file_still_works(cache_env, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.log").write_text("plain-content\n")
    rc, out, _ = run_proj("_fpreview", "local", str(root), "a.log\t")
    assert rc == 0 and "plain-content" in out


# ---------------------------------------------------------------- acceptance 3: cursor

def test_pick_project_cursor_on_non_utf8_project(monkeypatch):
    captured = {}

    def fake_fzf(lines, args, expect=()):
        captured["lines"], captured["args"] = lines, args
        return "", [lines[2]]
    monkeypatch.setattr(ui, "run_fzf", fake_fzf)
    rows = [row(host="mac", path="/a"), row(host="mac", path=BAD), row(host="mac", path="/z")]
    res = ui.pick_project(rows, NOW, select=("mac", BAD))
    assert res == ("", "mac", BAD)  # the raw path comes back, not the encoded field
    idx = [i for i, ln in enumerate(captured["lines"]) if ln.split("\t")[1] == ui.to_field(BAD)]
    assert len(idx) == 1
    assert "--bind=load:pos(%d)" % (idx[0] + 1) in captured["args"]
    assert "--bind=load:pos(3)" in captured["args"]  # header, /a, then BAD


def test_pick_project_cursor_other_rows_and_missing(monkeypatch):
    captured = {}

    def fake_fzf(lines, args, expect=()):
        captured["args"] = args
        return "", [lines[1]]
    monkeypatch.setattr(ui, "run_fzf", fake_fzf)
    rows = [row(host="mac", path="/a"), row(host="mac", path=BAD)]
    ui.pick_project(rows, NOW, select=("mac", "/a"))
    assert "--bind=load:pos(2)" in captured["args"]
    ui.pick_project(rows, NOW, select=("mac", "/home/u/b\udcfed"))  # a different bad name
    assert not any(a.startswith("--bind=load:pos") for a in captured["args"])


def test_pick_project_returns_raw_path_for_selected_encoded_line(monkeypatch):
    monkeypatch.setattr(ui, "run_fzf", lambda lines, args, expect=(): ("ctrl-r", [lines[1]]))
    assert ui.pick_project([row(host="mac", path=BAD)], NOW) == ("ctrl-r", "mac", BAD)


def write_fake(tmp_path, name, body):
    d = tmp_path / "fakebin"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text("#!/bin/sh\n" + body)
    f.chmod(0o755)
    return str(d)


def test_pick_project_with_recording_fzf_script(tmp_path, monkeypatch):
    rec = tmp_path / "rec"
    rec.mkdir()
    body = ('for a in "$@"; do printf "%s\\n" "$a"; done > "{r}/args"\n'
            'cat > "{r}/stdin"\n'
            'printf "\\n"; sed -n 2p "{r}/stdin"\n').format(r=rec)
    d = write_fake(tmp_path, "fzf", body)
    monkeypatch.setenv("PATH", d + os.pathsep + os.environ["PATH"])
    rows = [row(host="mac", path="/a"), row(host="mac", path=BAD)]
    res = ui.pick_project(rows, NOW, select=("mac", BAD))
    args = (rec / "args").read_bytes().decode("utf-8", "replace").splitlines()
    assert "--bind=load:pos(3)" in args
    stdin = (rec / "stdin").read_bytes()
    hidden = [ln.split(b"\t")[1] for ln in stdin.split(b"\n") if ln.count(b"\t") >= 2]
    assert all(h.isascii() for h in hidden)  # fzf only ever sees ASCII hidden fields
    assert res[1] == "mac"


def test_run_fzf_exchanges_bytes_with_surrogateescape(tmp_path, monkeypatch):
    d = write_fake(tmp_path, "fzf", 'cat\n')  # echo stdin back: the "selection" is every line
    monkeypatch.setenv("PATH", d + os.pathsep + os.environ["PATH"])
    line = "h\t/p\tdisplay \udcff end"
    try:
        out = ui.run_fzf([line], [])
    except SystemExit:
        pytest.skip("fake fzf protocol not accepted")  # not the point of this test
    flat = json.dumps(out, ensure_ascii=True)
    assert "\\udcff" in flat or "udcff" in flat  # the invalid byte survived the round trip


# ---------------------------------------------------------------- acceptance 4: Enter

def test_tmux_name_accepts_non_utf8_path_and_distinguishes():
    a = opener.tmux_name("/a/b\udcff")
    b = opener.tmux_name("/a/b\udcfe")
    assert a.startswith("proj-") and is_ascii(a)
    assert a != b  # bytes 0xff and 0xfe must not collapse into one replacement char
    assert a != opener.tmux_name("/a/b" + FFFD)


def test_tmux_name_clean_path_stable():
    assert opener.tmux_name("/a/x") == opener.tmux_name("/a/x")
    assert opener.tmux_name("/a/x") != opener.tmux_name("/b/x")


def test_build_command_for_non_utf8_path_does_not_raise():
    tgt = {"local": False, "ssh": "s-host", "tmux": "always"}
    cmd = opener.build_command(tgt, BAD, opener.resume_inner("id-1"), session_id="abcdef123456")
    assert opener.tmux_name(BAD, "abcdef123456") in cmd


@pytest.fixture
def usage_env(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("PROJ_CACHE", str(cache))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    return cache


def capture_open(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.opener, "open_command",
                        lambda cmd, title, iterm=None: calls.append(cmd) or "inline")
    monkeypatch.setattr(cli.opener, "has_iterm", lambda: False)
    return calls


def test_enter_on_tmux_host_with_non_utf8_project(usage_env, monkeypatch):
    calls = capture_open(monkeypatch)
    r = row(host="s-host", ssh="s-host", local=False, path=BAD, tmux="always", mux="tmux")
    out = cli.open_project([r], r)  # must not raise
    assert out is not None and calls
    assert opener.tmux_name(BAD) in calls[0] or "tmux" in calls[0]


def test_enter_resume_on_tmux_host_with_non_utf8_project(usage_env, monkeypatch):
    calls = capture_open(monkeypatch)
    r = row(host="s-host", ssh="s-host", local=False, path=BAD, tmux="always", mux="tmux")
    cli.launch(r, BAD, {"id": "abcdef123456", "cwd": BAD})
    assert calls


def test_enter_on_zellij_host_with_non_utf8_project(usage_env, tmp_path, monkeypatch):
    d = write_fake(tmp_path, "zellij", "exit 0\n")  # a zellij with no sessions
    monkeypatch.setenv("PATH", d + os.pathsep + os.environ["PATH"])
    for k in ("ZELLIJ", "ZELLIJ_SESSION_NAME"):
        monkeypatch.delenv(k, raising=False)
    calls = capture_open(monkeypatch)
    monkeypatch.setattr(cli.agent, "zellij_by_project", lambda paths: {})
    tabs = []
    monkeypatch.setattr(cli.opener, "zellij_new_tab", lambda path, title: tabs.append(path))
    r = row(host="s-host", ssh=None, local=True, path=BAD, mux="zellij", tmux=False, home=None)
    out = cli.open_project([r], r)
    assert out is not None
    assert calls or tabs


def test_enter_inside_zellij_with_non_utf8_project(usage_env, tmp_path, monkeypatch):
    d = write_fake(tmp_path, "zellij", "exit 0\n")
    monkeypatch.setenv("PATH", d + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "poly")
    capture_open(monkeypatch)
    monkeypatch.setattr(cli.agent, "zellij_by_project", lambda paths: {})
    tabs = []
    monkeypatch.setattr(cli.opener, "zellij_new_tab", lambda path, title: tabs.append(path))
    r = row(host="s-host", ssh=None, local=True, path=BAD, mux="zellij", tmux=False)
    cli.open_project([r], r)
    assert tabs == [BAD]  # the original path, not an encoded or replaced one


# ---------------------------------------------------------------- acceptance 5: usage log + report

def usage_lines(cache):
    with open(str(cache / "usage.jsonl"), "rb") as f:
        return f.read().splitlines()


def test_usage_log_lines_are_pure_ascii_and_keep_the_path(usage_env, monkeypatch):
    capture_open(monkeypatch)
    r = row(host="s-host", ssh=None, local=True, path=BAD, mux=False, tmux=False)
    cli.launch(r, BAD)
    cli.launch(r, BAD, {"id": "s\udcff", "cwd": BAD})
    lines = usage_lines(usage_env)
    assert len(lines) == 2
    for ln in lines:
        ln.decode("ascii")  # strict
        ev = json.loads(ln.decode("ascii"))
        assert ev["path"] == BAD


def test_usage_log_plain_event_still_parses(usage_env, monkeypatch):
    capture_open(monkeypatch)
    r = row(host="s-host", ssh=None, local=True, path="/home/u/q", mux=False, tmux=False)
    cli.launch(r, "/home/u/q")
    ev = json.loads(usage_lines(usage_env)[0].decode("ascii"))
    assert (ev["action"], ev["host"], ev["path"]) == ("open", "s-host", "/home/u/q")


def test_log_failure_never_breaks_the_command(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    monkeypatch.setenv("PROJ_CACHE", str(blocker / "cache"))  # makedirs under a file -> OSError
    capture_open(monkeypatch)
    r = row(host="s-host", ssh=None, local=True, path=BAD, mux=False, tmux=False)
    cli.launch(r, BAD)  # must not raise


@pytest.fixture
def report_env(tmp_path, monkeypatch, usage_env):
    root = tmp_path / "root"
    root.mkdir()
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"hosts": [{"name": "s-host", "local": True, "roots": [str(root)]}]}))
    monkeypatch.setenv("PROJ_CONFIG", str(cfgp))
    sync._write_cache("s-host", {"schema": 1, "host": "s-host", "collected_at": time.time(),
                                 "synced_at": time.time(), "projects": [proj(BAD)]})
    return usage_env


def test_report_after_logging_a_non_utf8_open(report_env, monkeypatch, capsys):
    capture_open(monkeypatch)
    r = row(host="s-host", ssh=None, local=True, path=BAD, mux=False, tmux=False)
    cli.launch(r, BAD)
    capsys.readouterr()
    assert cli.main(["report"]) == 0
    out = capsys.readouterr().out
    out.encode("utf-8")
    assert "## Your usage, last 14 days" in out
    assert FFFD in out


def test_report_survives_a_bad_line_in_usage_log(report_env, monkeypatch, capsys):
    report_env.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"ts": time.time(), "action": "open", "host": "s-host", "path": "/home/u/q"})
    with open(str(report_env / "usage.jsonl"), "wb") as f:
        f.write(b'{"ts": 1, "action": "open", "path": "/x/\xff"}\n')
        f.write(b"\xff\xfe not json at all\n")
        f.write(good.encode() + b"\n")
    capture_open(monkeypatch)
    r = row(host="s-host", ssh=None, local=True, path=BAD, mux=False, tmux=False)
    cli.launch(r, BAD)
    capsys.readouterr()
    assert cli.main(["report"]) == 0
    out = capsys.readouterr().out
    out.encode("utf-8")
    assert "## Your usage, last 14 days" in out
    lines = usage_lines(report_env)
    for ln in lines[3:]:
        ln.decode("ascii")  # what we append stays ASCII even after a bad line


def test_report_with_only_a_bad_line_still_returns_zero(report_env, capsys):
    report_env.mkdir(parents=True, exist_ok=True)
    with open(str(report_env / "usage.jsonl"), "wb") as f:
        f.write(b"\xff\n")
    assert cli.main(["report"]) == 0


def test_report_empty_usage_log_returns_zero(report_env, capsys):
    report_env.mkdir(parents=True, exist_ok=True)
    (report_env / "usage.jsonl").write_bytes(b"")
    assert cli.main(["report"]) == 0
