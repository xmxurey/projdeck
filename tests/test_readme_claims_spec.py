"""Independent spec tests for T27: what README.md says must be what the program does.

The incompatibility list is parsed out of README.md (the bullets under "Examples that used to
load"). Items written as `key: value` or `proj trash ... --older X` are parsed; items written
in prose (e.g. "`tmux` other than ..., including `null`") cannot be parsed and are listed in
PROSE with a README anchor that must still be present.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

import pytest

from projdeck import config, opener

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def readme_path():
    """The README under test: always the repo's. The cases are built at collection time, so
    an environment variable could not be undone by a fixture and an outer shell would swap
    the README under every test."""
    return os.path.join(ROOT, "README.md")


def readme():
    """Read at call time, never at import: a README edit must fail only the tests about it."""
    with open(readme_path(), encoding="utf-8") as f:
        return f.read()


class ParseError(Exception):
    """README text a test needs was not found (reported as a failure of that test only)."""


def need(m, what):
    if not m:
        raise ParseError("README has no %s (looked in %s)" % (what, readme_path()))
    return m


def cases(fn):
    """Parametrize values from the README, computed at collection WITHOUT ever raising: if the
    README cannot be parsed the single case is the error, and the test fails with its message."""
    try:
        out = list(fn())
    except Exception as e:  # noqa: BLE001 - any failure becomes one failing test
        return [pytest.param(ParseError("cannot parse README for %s: %s: %s"
                                        % (fn.__name__, type(e).__name__, e)), id="unparseable")]
    return out or [pytest.param(ParseError("README gave no examples for %s" % fn.__name__),
                                id="none-found")]


def unwrap(case):
    if isinstance(case, ParseError):
        pytest.fail(str(case), pytrace=False)
    return case

TOP_KEYS = {"hosts", "scan_depth", "include_plain", "tiers_days", "auto_sync_minutes",
            "ssh_timeout"}


def incompat_bullets():
    text = readme()
    head = need(re.search(r"Examples that used to load", text), "incompatibility list heading").start()
    out = []
    for line in text[head:].splitlines()[1:]:
        if line.startswith("- "):
            out.append(line)
        elif out and line.strip() == "":
            break
    return need(out, "bullets under the incompatibility list")


def parsed_examples():
    """(span, key, value) for every `key: json-value` code span in the list."""
    ex = []
    for b in incompat_bullets():
        for span in re.findall(r"`([^`]+)`", b):
            m = re.fullmatch(r"([a-z_]+): (.+)", span)
            if m:
                ex.append((span, m.group(1), json.loads(m.group(2))))
    return ex


def older_examples():
    """Examples from the README paragraph on `--older` (not part of the config list)."""
    m = need(re.search(r"`--older` is a command-line argument.*?\n\n", readme(), re.S),
             "paragraph on --older")
    para = " ".join(m.group(0).split())
    if "exits with code 1" not in para or "naming `--older`" not in para:
        raise ParseError("--older paragraph does not state exit code 1 and naming --older")
    m2 = need(re.search(r"for example (.*?)\)", para), "'for example ...' list in the --older paragraph")
    return re.findall(r"`(\w+)`", m2.group(1))


# prose items: (id, host-or-top, mutation, key to name, README anchor)
PROSE = [
    ("tmux-null", "host", {"tmux": None}, "hosts[0].tmux", "including `null`"),
    ("tmux-other", "host", {"tmux": "yes"}, "hosts[0].tmux", "`tmux` other than"),
    ("tiers-order", "top", {"tiers_days": {"active": 90, "stale": 14}}, "tiers_days",
     "`active >= stale`"),
    ("local-not-bool", "host", {"local": "yes"}, "hosts[0].local", "`local` is not a boolean"),
    ("ssh-empty", "host", {"ssh": "", "local": False}, "hosts[0].ssh", "empty string"),
]


def run_proj(tmp_path, cfg, *args):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(cfg))
    env = dict(os.environ, HOME=str(home), PROJ_CACHE=str(tmp_path / "cache"),
               PROJ_CONFIG=str(cfgp), PYTHONPATH=ROOT)
    env.pop("PROJ_LIVE_TESTS", None)
    p = subprocess.run([sys.executable, "-m", "projdeck"] + list(args), cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=60)
    return p


def good_cfg(tmp_path):
    (tmp_path / "root").mkdir(exist_ok=True)
    return {"hosts": [{"name": "loc", "local": True, "roots": [str(tmp_path / "root")]}]}


def build_cfg(tmp_path, where, mut):
    cfg = good_cfg(tmp_path)
    (cfg["hosts"][0] if where == "host" else cfg).update(mut)
    return cfg


# ---------- the incompatibility list ----------

def test_list_is_parseable_and_nonempty():
    try:
        keys = {k for _, k, _ in parsed_examples()}
        olders = set(older_examples())
        bullets = incompat_bullets()
    except ParseError as e:
        pytest.fail(str(e), pytrace=False)
    assert {"roots", "hosts", "mux", "include_plain", "scan_depth"} <= keys
    assert {"nan", "inf"} <= olders
    assert not [b for b in bullets if "--older" in b]


@pytest.mark.parametrize("case", cases(parsed_examples),
                         ids=lambda x: str(x[0])[:30] if isinstance(x, tuple) else None)
def test_readme_example_is_rejected_with_exit_2(tmp_path, case):
    span, key, val = unwrap(case)
    where = "top" if key in TOP_KEYS else "host"
    cfg = build_cfg(tmp_path, where, {key: val})
    named = key if where == "top" else "hosts[0].%s" % key
    p = run_proj(tmp_path, cfg, "report")
    assert p.returncode == 2, (span, p.stdout, p.stderr)
    assert named in p.stderr, (span, p.stderr)
    assert "Traceback" not in p.stderr


@pytest.mark.parametrize("ident,where,mut,named,anchor", PROSE, ids=[x[0] for x in PROSE])
def test_readme_prose_example_is_rejected_with_exit_2(tmp_path, ident, where, mut, named, anchor):
    assert anchor in readme(), "README lost the phrase %r" % anchor
    p = run_proj(tmp_path, build_cfg(tmp_path, where, mut), "report")
    assert p.returncode == 2, p.stderr
    assert named in p.stderr, p.stderr


@pytest.mark.parametrize("case", cases(older_examples))
def test_readme_older_example_is_rejected_with_exit_1(tmp_path, case):
    val = unwrap(case)
    p = run_proj(tmp_path, good_cfg(tmp_path), "trash", "--purge", "--older", val)
    assert p.returncode == 1, (p.returncode, p.stderr)
    assert "--older" in p.stderr


def test_good_config_is_quiet(tmp_path):
    p = run_proj(tmp_path, good_cfg(tmp_path), "report")
    assert p.returncode == 0, p.stderr


@pytest.mark.parametrize("mut", [{"tmux": True}, {"tmux": False}, {"tmux": "always"},
                                 {"mux": False}, {"mux": "tmux"}, {"mux": "zellij"},
                                 {"roots": ["~"]}])
def test_readme_allowed_host_values_accepted(tmp_path, mut):
    p = run_proj(tmp_path, build_cfg(tmp_path, "host", mut), "report")
    assert p.returncode == 0, p.stderr


def test_error_does_not_echo_host_values(tmp_path):
    cfg = build_cfg(tmp_path, "host", {"roots": [], "ssh": "secret-alias-xyz"})
    p = run_proj(tmp_path, cfg, "report")
    assert p.returncode == 2
    assert "secret-alias-xyz" not in p.stderr and "hosts[0].roots" in p.stderr


# ---------- defaults ----------

def test_defaults_match_readme(tmp_path):
    cfgp = tmp_path / "c.json"
    cfgp.write_text(json.dumps({"hosts": [{"name": "loc", "local": True}]}))
    cfg = config.load_config(str(cfgp))
    assert cfg["tiers_days"] == {"active": 14, "stale": 90}
    assert cfg["scan_depth"] == 2
    assert cfg["include_plain"] is True
    assert cfg["auto_sync_minutes"] == 30
    assert cfg["ssh_timeout"] == 20
    assert cfg["trash_dir"] == "~/.proj-trash"
    assert cfg["hosts"][0].get("roots", ["~"]) == ["~"]
    assert cfg["hosts"][0]["mux"] == "tmux"  # tmux defaults to true, mux to "tmux"


def test_readme_states_the_defaults():
    for frag in ['`{"active": 14, "stale": 90}`', "`scan_depth` (`2`)", "`include_plain` (`true`)",
                 "`auto_sync_minutes` (`30`)", "`ssh_timeout` (`20`", "`trash_dir` (`~/.proj-trash`)",
                 '`true`, `false` or `"always"`', "PROJ_CONFIG", "PROJ_CACHE", "--live",
                 "--quiet", "proj trash [--purge --older 30d] [host]"]:
        assert frag in readme(), frag


def test_readme_default_config_path_and_cache_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("PROJ_CONFIG", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.config_path() == str(tmp_path / ".config/proj/config.json")
    text = readme()
    assert "~/.config/proj/config.json" in text and "`~/.cache/proj`" in text


def test_readme_proj_cache_is_honoured(tmp_path):
    p = run_proj(tmp_path, good_cfg(tmp_path), "sync", "--quiet")
    assert (tmp_path / "cache").is_dir()
    assert p.stdout == ""


# ---------- proj sync <name> ----------

@pytest.mark.parametrize("name", ["LOC", "Loc", "nosuchhost", ""])
def test_sync_name_must_match_exactly(tmp_path, name):
    p = run_proj(tmp_path, good_cfg(tmp_path), "sync", name)
    assert p.returncode != 0, (p.stdout, p.stderr)
    assert "Traceback" not in p.stderr
    if name:  # word boundary: "loc" (the real host) must not satisfy a check for "LOC"
        assert re.search(r"(?<![\w.-])%s(?![\w.-])" % re.escape(name), p.stderr), p.stderr
    else:
        assert p.stderr.strip(), p.stderr


def test_sync_exact_name_works(tmp_path):
    p = run_proj(tmp_path, good_cfg(tmp_path), "sync", "loc")
    assert p.returncode == 0, p.stderr


def test_sync_one_bad_name_among_good_fails(tmp_path):
    p = run_proj(tmp_path, good_cfg(tmp_path), "sync", "loc", "LOC")
    assert p.returncode != 0
    assert re.search(r"(?<![\w.-])LOC(?![\w.-])", p.stderr), p.stderr


# ---------- session naming ----------

def h4(path):
    return hashlib.sha1(path.encode("utf-8", "surrogateescape")).hexdigest()[:4]


def test_name_plain_project_has_no_hash():
    assert opener.proj_session_name("foo", "/home/u/foo", "/home/u") == "proj-foo"


def test_name_with_slash_gets_hash():
    path = "/home/u/dev/process-core"
    got = opener.proj_session_name("dev/process-core", path, "/home/u")
    assert got == "proj-dev-process-core-" + h4(path)


def test_name_outside_home_gets_hash():
    assert opener.proj_session_name("foo", "/srv/foo", "/home/u") == "proj-foo-" + h4("/srv/foo")


def test_name_empty_home_gets_hash():
    assert opener.proj_session_name("foo", "/home/u/foo", "") == "proj-foo-" + h4("/home/u/foo")


def test_name_dropped_characters_get_hash():
    path = "/home/u/a b"
    got = opener.proj_session_name("a b", path, "/home/u")
    assert got == "proj-a-b-" + h4(path)
    long = "x" * 40
    got = opener.proj_session_name(long, "/home/u/" + long, "/home/u")
    assert got.endswith("-" + h4("/home/u/" + long))


def test_name_always_matches_charset():
    for n, p, hm in [("dev/process-core", "/home/u/dev/process-core", "/home/u"),
                     ("a b", "/home/u/a b", "/home/u"), ("x", "/x", "")]:
        assert re.fullmatch(r"proj-[A-Za-z0-9._-]+", opener.proj_session_name(n, p, hm))


def test_readme_session_name_examples():
    """Plain example: `daily` -> `proj-daily`. Slash example: `proj-dev-process-core-<hash>`,
    where <hash> is a placeholder or a literal 4-hex value (then only its shape is checked,
    the README gives no path to recompute it from)."""
    m = need(re.search(r"e\.g\. `([^`\s]+)` becomes `([^`\s]+)`", readme()), "plain name example")
    assert opener.proj_session_name(m.group(1), "/home/u/" + m.group(1), "/home/u") == m.group(2)
    m = need(re.search(r"e\.g\. `(proj-dev-process-core-[^`]+)`", readme()), "slash name example")
    got = opener.proj_session_name("dev/process-core", "/home/u/dev/process-core", "/home/u")
    shown = m.group(1)
    suffix = shown[len("proj-dev-process-core-"):]
    if re.fullmatch(r"<[^>]+>", suffix):
        assert got.startswith("proj-dev-process-core-")
    else:
        assert re.fullmatch(r"[0-9a-f]{4}", suffix), shown
        assert got.startswith("proj-dev-process-core-") and len(got) == len(shown)


# ---------- proj report lists delete commands for old-name exited sessions ----------

def test_report_lists_delete_command_for_old_name_exited_session(tmp_path):
    import time
    now = int(time.time())
    host = {"name": "s-host", "local": True, "mux": "zellij"}
    cache = tmp_path / "cache"
    (cache / "live").mkdir(parents=True)
    (cache / "s-host.json").write_text(json.dumps(
        {"schema": 1, "host": "s-host", "collected_at": now - 60, "synced_at": now - 60,
         "stale_since": None, "last_error": None, "projects": []}))
    (cache / "live" / "s-host.json").write_text(json.dumps(
        {"schema": 1, "host": "s-host", "collected_at": now - 10, "source": "claude agents",
         "since": now - 86400, "sessions": [], "projects": {},
         "zellij_exited": ["proj-oldrule"]}))
    p = run_proj(tmp_path, {"hosts": [host]}, "report")
    assert p.returncode == 0, p.stderr
    assert "zellij delete-session -- proj-oldrule" in p.stdout
    assert "`proj report`" in readme() and "delete command" in readme()


# ---------- other README claims ----------

def test_readme_mentions_required_items():
    text = readme()
    for frag in ["3.8", "contrib/statusline-waiting.sh", "jq", "120 seconds", "5 second",
                 "case-sensitive"]:
        assert frag in text, frag


def test_readme_states_fzf_minimum_version():
    versions = [int(x) for x in re.findall(r"fzf[^\n]{0,40}?>=\s*0\.(\d+)", readme())]
    assert versions and all(v == 36 for v in versions), "README must say fzf >= 0.36"


def test_readme_live_marker_line_covers_zellij():
    lines = [ln for ln in readme().splitlines() if "\u25b6" in ln]
    assert lines, "README has no line explaining the live marker"
    assert any("zellij" in ln for ln in lines), lines


def test_statusline_script_exists_and_has_the_function():
    s = open(os.path.join(ROOT, "contrib/statusline-waiting.sh")).read()
    assert "proj_waiting_segment" in s and "statusline.state" in s
    assert "jq" in s


def test_git_timeout_is_five_seconds():
    from projdeck import agent
    assert agent.GIT_TIMEOUT == 5


def test_outer_environment_cannot_swap_the_readme(monkeypatch):
    monkeypatch.setenv("PROJ_README_FOR_TESTS", "/nonexistent")
    assert readme_path() == os.path.join(ROOT, "README.md")
    assert "proj serve" in readme()
