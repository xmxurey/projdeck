"""T13 (spec-derived): restore / trash / purge / ^X / new-tab failure paths give the right
conclusion and exit code (design 2.7). Nothing here reaches a real host: run_agent is faked,
zellij is a stand-in."""
import json
import os
import re
import time

import pytest

from projdeck import agent, cli, config, opener, sync, ui

OLD = 1_000_000_000          # far older than any --older 30d cutoff
HOSTS = ["s-host", "em-host"]
FAIL_RE = re.compile(r"fail|失败|读取|unread|could not|cannot|can't", re.I)


class Fake:
    """Fake agent: per host a list of batches, or an error to raise for a given action."""

    def __init__(self, batches=None, fail=None):
        self.batches = batches or {}
        self.fail = fail or {}      # (host, action) or host -> error text
        self.calls = []             # (host, action, req)
        self.purged = {}            # host -> [ids]

    def __call__(self, host, req, timeout=20):
        name, act = host["name"], req["action"]
        self.calls.append((name, act, req))
        msg = self.fail.get((name, act)) or self.fail.get(name)
        if msg:
            raise sync.AgentError(msg)
        if act == "batches":
            return {"batches": [dict(b) for b in self.batches.get(name, [])]}
        if act == "restore":
            return {"restored": ["/home/u/p/a.log"], "skipped": []}
        if act == "purge":
            if req.get("ids") is not None:
                ids = list(req["ids"])
            else:
                cut = (req.get("now") or time.time()) - float(req["older_days"]) * 86400
                ids = [b["id"] for b in self.batches.get(name, []) if b["created"] < cut]
            self.purged.setdefault(name, []).extend(ids)
            return {"purged": ids}
        raise AssertionError("unexpected action %r" % act)

    def acts(self, action):
        return [c for c in self.calls if c[1] == action]


def batch(bid, created=OLD, project="/home/u/p"):
    return {"id": bid, "created": created, "count": 1, "project": project}


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"hosts": [{"name": h, "ssh": h} for h in HOSTS],
                                "trash_dir": "/home/u/.proj-trash"}))
    monkeypatch.setenv("PROJ_CONFIG", str(cfgp))
    monkeypatch.setenv("PROJ_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(ui, "run_fzf", lambda lines, args, expect=(): ("", [lines[0]]))
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")

    def install(fake):
        monkeypatch.setattr(sync, "run_agent", fake)
        return fake
    return install


def run(argv, capsys):
    rc = cli.main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


# ---- AC1: restore, every host unreadable

def test_restore_all_hosts_fail(env, capsys):
    env(Fake(fail={"s-host": "boom-s", "em-host": "boom-e"}))
    rc, out, err = run(["restore"], capsys)
    assert rc == 1
    assert "trash is empty" not in out + err
    assert "s-host" in err and "em-host" in err
    assert "Traceback" not in out + err


def test_restore_named_host_fails(env, capsys):
    env(Fake(fail={"s-host": "boom-s"}))
    rc, out, err = run(["restore", "s-host"], capsys)
    assert rc == 1 and "trash is empty" not in out + err
    assert "s-host" in err and "Traceback" not in out + err


def test_restore_empty_but_readable_still_says_empty(env, capsys):
    env(Fake())
    rc, out, err = run(["restore"], capsys)
    assert rc == 0 and "trash is empty" in out + err


def test_restore_partial_failure_warns_and_still_restores(env, capsys):
    f = env(Fake(batches={"em-host": [batch("B1")]}, fail={"s-host": "boom-s"}))
    rc, out, err = run(["restore"], capsys)
    assert rc == 0
    assert [c[0] for c in f.acts("restore")] == ["em-host"]
    assert "s-host" in err and FAIL_RE.search(err) and "Traceback" not in err
    assert "trash is empty" not in out + err


# ---- AC2: trash listing

def test_trash_lists_readable_host_and_exits_1(env, capsys):
    env(Fake(batches={"em-host": [batch("B-em")]}, fail={"s-host": "boom-s"}))
    rc, out, err = run(["trash"], capsys)
    assert rc == 1
    assert "em-host" in out and "B-em" in out
    assert "s-host" in err and "Traceback" not in out + err


def test_trash_all_fail_exits_1(env, capsys):
    env(Fake(fail={"s-host": "x", "em-host": "y"}))
    rc, out, err = run(["trash"], capsys)
    assert rc == 1 and "s-host" in err and "em-host" in err
    assert "Traceback" not in out + err


def test_trash_no_failure_exits_0(env, capsys):
    env(Fake(batches={"em-host": [batch("B-em")], "s-host": [batch("B-s")]}))
    rc, out, err = run(["trash"], capsys)
    assert rc == 0 and "B-em" in out and "B-s" in out and err.strip() == ""


# ---- AC2: trash --purge

def test_purge_one_host_unreadable_still_purges_other_exit_1(env, capsys):
    f = env(Fake(batches={"em-host": [batch("OLD-em"), batch("NEW-em", created=int(time.time()))]},
                 fail={("s-host", "batches"): "boom-s"}))
    rc, out, err = run(["trash", "--purge", "--older", "30d"], capsys)
    assert rc == 1
    assert f.purged == {"em-host": ["OLD-em"]}
    assert "em-host: purged 1" in out.splitlines()
    assert "s-host" in err and "Traceback" not in out + err


def test_purge_nothing_old_and_one_failure_concludes_with_count(env, capsys):
    f = env(Fake(batches={"em-host": [batch("NEW-em", created=int(time.time()))]},
                 fail={("s-host", "batches"): "boom-s"}))
    rc, out, err = run(["trash", "--purge", "--older", "30d"], capsys)
    assert rc == 1 and f.acts("purge") == []
    text = out + err
    assert "s-host" in err
    assert re.search(r"\b1\b", text) and FAIL_RE.search(text)


def test_purge_all_hosts_unreadable_reports_both(env, capsys):
    f = env(Fake(fail={"s-host": "x", "em-host": "y"}))
    rc, out, err = run(["trash", "--purge", "--older", "30d"], capsys)
    assert rc == 1 and f.acts("purge") == []
    text = out + err
    assert re.search(r"\b2\b", text) and FAIL_RE.search(text)
    assert "s-host" in text and "em-host" in text and "Traceback" not in text


def test_purge_nothing_old_no_failure_exits_0(env, capsys):
    f = env(Fake(batches={"em-host": [batch("NEW", created=int(time.time()))]}))
    rc, out, err = run(["trash", "--purge", "--older", "30d"], capsys)
    assert rc == 0 and f.acts("purge") == [] and "Traceback" not in out + err


@pytest.mark.parametrize("bad,good", [("s-host", "em-host"), ("em-host", "s-host")])
def test_purge_delete_phase_failure_continues_with_next_host(env, capsys, bad, good):
    f = env(Fake(batches={"s-host": [batch("OLD-s")], "em-host": [batch("OLD-e")]},
                 fail={(bad, "purge"): "boom-delete"}))
    rc, out, err = run(["trash", "--purge", "--older", "30d"], capsys)
    assert rc == 1
    assert good in f.purged and bad not in f.purged
    assert "%s: purged 1" % good in out.splitlines()
    assert bad in err and "Traceback" not in out + err
    assert {c[0] for c in f.acts("purge")} == {"s-host", "em-host"}  # both were attempted


def test_purge_no_failure_exits_0(env, capsys):
    f = env(Fake(batches={"s-host": [batch("OLD-s")], "em-host": [batch("OLD-e")]}))
    rc, out, err = run(["trash", "--purge", "--older", "30d"], capsys)
    assert rc == 0 and set(f.purged) == {"s-host", "em-host"} and err.strip() == ""


# ---- AC3: ^X in the browser

BCFG = {"hosts": [{"name": "vm", "ssh": "vm"}], "tiers_days": {"active": 14, "stale": 90},
        "auto_sync_minutes": 30, "dashboard_url": None, "serve": {"port": 8787},
        "ssh_timeout": 5}
NOW = 1_800_000_000


def _browse(monkeypatch, clean_impl, picks):
    p = {"path": "/p", "exists": True, "git": None, "suspicious": ["a.log"], "progress": "",
         "errors": [], "sessions": [], "last_mtime": NOW - 100}
    cache = {"host": "vm", "projects": [p]}
    monkeypatch.setattr(cli, "fresh_caches", lambda cfg, now: [cache])
    monkeypatch.setattr(cli, "fresh_lives", lambda cfg, now: [])
    monkeypatch.setattr(cli.sync, "load_caches", lambda cfg: [cache])
    it = iter(picks)
    seen = []

    def fake_pick(rows, now, notice=None, select=None, board=None):
        seen.append({"notice": notice, "select": select})
        try:
            return next(it)
        except StopIteration:
            raise ui.Cancelled()
    monkeypatch.setattr(ui, "pick_project", fake_pick)
    monkeypatch.setattr(cli, "clean", clean_impl)
    return seen, cli.cmd_browse(dict(BCFG), None)


@pytest.mark.parametrize("exc", [sync.AgentError("ssh exploded"), ValueError("not a repo")])
def test_ctrl_x_clean_failure_returns_to_list_with_notice(monkeypatch, exc):
    def clean(*a, **k):
        raise exc
    seen, rc = _browse(monkeypatch, clean, [("ctrl-x", "vm", "/p")])
    assert rc == 0
    assert len(seen) == 2                      # the list came back
    assert "clean failed" in seen[1]["notice"]
    assert seen[1]["select"] == ("vm", "/p")


def test_ctrl_x_clean_success_has_no_failure_notice(monkeypatch):
    called = []
    seen, rc = _browse(monkeypatch, lambda *a, **k: called.append(a), [("ctrl-x", "vm", "/p")])
    assert rc == 0 and len(called) == 1 and len(seen) == 2
    assert "clean failed" not in (seen[1]["notice"] or "")


# ---- AC4: new tab inside zellij

ZROW = dict(local=True, ssh=None, mux="zellij", host="s-host", path="/home/u/quant",
            home="/home/u", exists=True)


def _in_zellij(monkeypatch, tmp_path, zellij_exit):
    bindir = tmp_path / "zbin"
    bindir.mkdir()
    z = bindir / "zellij"
    z.write_text("#!/bin/sh\nexit %d\n" % zellij_exit)
    z.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "poly")
    monkeypatch.setattr(agent, "zellij_by_project", lambda paths: {})
    return cli.open_project([dict(ZROW)], dict(ZROW))


def _usage(tmp_path):
    p = os.path.join(sync.cache_dir(), "usage.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(ln) for ln in open(p).read().splitlines() if ln.strip()]


def test_new_tab_failure_returns_hint_and_logs_nothing(monkeypatch, tmp_path):
    out = _in_zellij(monkeypatch, tmp_path, 1)
    assert out[0] is None
    assert "zellij new-tab failed" in out[1]
    assert _usage(tmp_path) == []


def test_new_tab_oserror_returns_hint_and_logs_nothing(monkeypatch, tmp_path):
    def boom(path, title):
        raise FileNotFoundError("zellij")
    monkeypatch.setattr(opener, "zellij_new_tab", boom)
    for k, v in (("ZELLIJ", "0"), ("ZELLIJ_SESSION_NAME", "poly")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(agent, "zellij_by_project", lambda paths: {})
    out = cli.open_project([dict(ZROW)], dict(ZROW))
    assert out[0] is None and "zellij new-tab failed" in out[1]
    assert _usage(tmp_path) == []


def test_new_tab_success_opens_and_logs_once(monkeypatch, tmp_path):
    out = _in_zellij(monkeypatch, tmp_path, 0)
    assert out[0] == "tab"
    events = _usage(tmp_path)
    assert len(events) == 1 and events[0]["path"] == "/home/u/quant"


# ---- extra: --older judged by config.positive_number

@pytest.mark.parametrize("v", ["abc", "nan", "inf", "-inf", "0", "0d", "-3d", "1e999", "", "d"])
def test_older_rejects_bad_values(v):
    with pytest.raises(SystemExit) as e:
        cli._days(v)
    assert "--older" in str(e.value)


@pytest.mark.parametrize("v,want", [("30d", 30), ("7", 7), ("1.5d", 1.5)])
def test_older_accepts_positive_numbers(v, want):
    assert cli._days(v) == want


def test_older_decision_comes_from_config_positive_number(monkeypatch):
    seen = []

    def no(x):
        seen.append(x)
        return False
    monkeypatch.setattr(config, "positive_number", no)
    with pytest.raises(SystemExit):
        cli._days("30d")
    assert seen and seen[0] == 30
    monkeypatch.setattr(config, "positive_number", lambda x: True)
    assert cli._days("30d") == 30


# ---- extra: non-UTF-8 names, clean then restore, bytes unchanged

BAD = b"\xff\xfe-r\x80"
CASES = [("repo", True, False), ("file", False, True), ("both", True, True)]


@pytest.mark.parametrize("label,bad_repo,bad_file", CASES, ids=[c[0] for c in CASES])
def test_clean_then_restore_non_utf8_names(tmp_path, monkeypatch, capsys, make_repo,
                                           label, bad_repo, bad_file):
    repo = os.fsdecode(os.fsencode(str(tmp_path)) + b"/" + (BAD if bad_repo else b"repo"))
    make_repo(repo)
    fname = os.fsdecode(b"junk-\xff\xfe.log") if bad_file else "junk.log"
    fpath = os.path.join(repo, fname)
    payload = b"\x00\xff\xfe junk \x80 bytes\n" * 3
    with open(os.fsencode(fpath), "wb") as f:
        f.write(payload)
    trash = str(tmp_path / "trash")
    cfgp = tmp_path / "cfg.json"
    cfgp.write_text(json.dumps({"hosts": [{"name": "local", "local": True, "roots": [str(tmp_path)]}],
                                "trash_dir": trash}))
    monkeypatch.setenv("PROJ_CONFIG", str(cfgp))
    shown = []

    def fzf(lines, args, expect=()):
        shown.extend(lines)
        return "", list(lines)
    monkeypatch.setattr(ui, "run_fzf", fzf)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    assert cli.main(["clean", "local:" + repo]) == 0
    assert not os.path.lexists(os.fsencode(fpath))
    assert cli.main(["restore"]) == 0
    with open(os.fsencode(fpath), "rb") as f:
        assert f.read() == payload
    assert cli.main(["trash"]) == 0
    cap = capsys.readouterr()
    assert shown
    for line in shown:                 # what fzf would be fed must be valid UTF-8
        line.encode("utf-8")
    (cap.out + cap.err).encode("utf-8")
