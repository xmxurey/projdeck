"""Long-lived privacy guard: tracked files must hold only fictional values.

Reads the working-tree copy of every file listed by `git ls-files` (skipping
docs/dev/runs) and rejects, by criterion rather than by known literal:
  - any mailbox address except: example.com, example.org, the exact address
    noreply@anthropic.com, the exact address git@github.com, and any domain
    under the RFC 2606 reserved TLDs .example / .invalid / .test;
  - any /home/<name> or /Users/<name> except the shared placeholder names
    (u, x, someone, someuser, ux, user2, s-host, em-host), plus (separately) the name
    of the user running the tests;
  - CGNAT (100.64.0.0/10) IPv4 addresses not in the exact fictional set, and
    any tailnet-prefixed IPv6 address;
  - *.ts.net names not in the fictional set.

Coverage note: without a .git directory (the rsync'd copies used by remote CI)
the whole module is skipped, so the remote full run gives ZERO coverage for
this guard. The authoritative run is the full suite on sg, which has .git.
"""
import getpass
import ipaddress
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP_PREFIX = "docs/dev/runs/"

# rsync'd copies (remote CI) carry no .git, so `git ls-files` cannot work there.
pytestmark = pytest.mark.skipif(not (ROOT / ".git").exists(),
                                reason="no .git in this copy of the tree")

# Built at runtime so this file never contains bad literals itself.
MAIL_MARK = "@" + "gmail"
HOME_PREFIX = "/home" + "/"

MAC_PREFIX = "/Users" + "/"
HOME_RE = re.compile(r"/(?:home|Users)/([A-Za-z0-9._-]+)")
HOME_OK = {"u", "x", "someone", "someuser", "ux", "user2", "s-host", "em-host"}

MAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
MAIL_DOMAIN_OK = {"example.com", "example.org"}
MAIL_OK = {"noreply@anthropic.com", "git@github.com"}
# RFC 2606 reserved top-level domains
MAIL_TLD_OK = {"example", "invalid", "test"}

IP_RE = re.compile(r"(?<![0-9.])(\d{1,3}(?:\.\d{1,3}){3})(?![0-9])")
CGNAT = ipaddress.ip_network("100.64.0.0/10")
IP_OK = {"100.64.0.0", "100.64.0.1", "100.64.0.9", "100.64.1.2",
         "100.100.1.1", "100.101.102.103", "100.101.102.104",
         "100.127.255.255"}
V6_PREFIX = "fd7a:115c:" + "a1e0"
V6_RE = re.compile(V6_PREFIX + r"[0-9a-f:]*", re.I)
V6_OK = {V6_PREFIX + "::1"}

TS_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.ts\.net\b")
TS_OK = {"demo-vm.tail0000.ts.net", "x.ts.net", "a.ts.net"}


def _j(*parts):
    """Join parts with dots, so bad example values never appear verbatim here."""
    return ".".join(parts)


def violations(name, text, user):
    """Return short, value-free descriptions of problems found in text."""
    out = []
    for m in MAIL_RE.finditer(text):
        dom = m.group(1).lower()
        if (m.group(0) in MAIL_OK or dom in MAIL_DOMAIN_OK
                or dom.rsplit(".", 1)[-1] in MAIL_TLD_OK):
            continue
        out.append("%s: mailbox address" % name)
    for m in HOME_RE.finditer(text):
        if m.group(1) not in HOME_OK:
            out.append("%s: non-placeholder home path" % name)
    if user and (HOME_PREFIX + user) in text:
        out.append("%s: current user's home path" % name)
    for m in IP_RE.finditer(text):
        s = m.group(1)
        try:
            ip = ipaddress.ip_address(s)
        except ValueError:
            continue
        if ip in CGNAT and s not in IP_OK:
            out.append("%s: non-fictional CGNAT address" % name)
    for m in V6_RE.finditer(text):
        if m.group(0).lower() not in V6_OK:
            out.append("%s: tailnet IPv6 address" % name)
    for m in TS_RE.finditer(text):
        if m.group(0) not in TS_OK:
            out.append("%s: non-fictional tailnet name" % name)
    return out


def tracked_files(root):
    res = subprocess.run(["git", "ls-files", "-z"], cwd=str(root),
                         stdout=subprocess.PIPE, check=True)
    names = [n.decode("utf-8", "surrogateescape")
             for n in res.stdout.split(b"\0") if n]
    return [n for n in names if not n.startswith(SKIP_PREFIX)]


def scan(root, user):
    found = []
    for name in tracked_files(root):
        p = Path(root) / name
        if not p.is_file():
            continue
        text = p.read_bytes().decode("utf-8", "replace")
        found.extend(violations(name, text, user))
    return found


def test_repo_has_no_private_values():
    found = scan(ROOT, getpass.getuser())
    assert found == [], "\n".join(found)


def test_guard_actually_scans_files():
    names = tracked_files(ROOT)
    assert "README.md" in names
    assert len(names) > 20


# --- both sides of each rule, on synthetic text -------------------------

@pytest.mark.parametrize("bad", [
    "a" + MAIL_MARK + ".com",
    "git" + "@" + "gitlab.com", "x" + "git" + "@github.com", "git" + "@github.com" + ".evil.org",
    "name" + "@" + "otherdomain.com", "name" + "@" + "example.net",
    HOME_PREFIX + "zed/x", HOME_PREFIX + "zzother/x", MAC_PREFIX + "zzother/x",
    V6_PREFIX + "::2", _j("100", "64", "0", "2"),
    _j("100", "101", "102", "105"),
    _j("100", "64", "5", "5"), _j("100", "99", "1", "1"),
    _j("100", "127", "255", "254"), _j("100", "101", "102", "110"),
    _j("100", "100", "1", "2"), _j("host-real", "tail1234", "ts", "net"),
    _j("box", "ts", "net"),
])
def test_bad_values_flagged(bad):
    assert violations("f", "x " + bad + " y", "someone")


def test_current_user_home_path_flagged_even_if_placeholder_listed():
    # runtime user rule is independent of the placeholder list
    assert violations("f", HOME_PREFIX + "u/x", "u")
    assert not violations("f", HOME_PREFIX + "u/x", "zed")
    assert not violations("f", HOME_PREFIX + "u/x", "")


@pytest.mark.parametrize("good", [
    "100.64.0.0/10", "100.64.0.1", "100.64.0.9", "100.64.1.2", "100.100.1.1",
    "100.101.102.103", "100.101.102.104", "100.127.255.255",
    "demo-vm.tail0000.ts.net", "x.ts.net", "a.ts.net",
    HOME_PREFIX + "u/x", HOME_PREFIX + "someone", HOME_PREFIX + "s-host",
    MAC_PREFIX + "someone", MAC_PREFIX + "u/x",
    V6_PREFIX + "::1",
    "git@github.com", "a@evil.example", "a@x.invalid", "a@y.test",
    "you@example.com", "a@example.org", "noreply@anthropic.com",
    "8.8.8.8", "192.168.1.1", "100.128.0.1", "100.63.255.255", "v" + _j("1100", "64", "5", "5"),
])
def test_good_values_quiet(good):
    assert violations("f", "x " + good + " y", "zed") == []


# --- end-to-end counter-example on a temp copy of the tree --------------

def _copy_repo(tmp_path):
    dst = tmp_path / "copy"
    for name in tracked_files(ROOT):
        src = ROOT / name
        if src.is_file():
            (dst / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(str(src), str(dst / name))
    subprocess.run(["git", "init", "-q"], cwd=str(dst), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(dst), check=True)
    return dst


@pytest.mark.parametrize("payload", [
    lambda u: "contact a" + MAIL_MARK + ".com\n",
    lambda u: "contact name" + "@" + "otherdomain.com\n",
    lambda u: "cd " + HOME_PREFIX + u + "/proj\n",
    lambda u: "cd " + HOME_PREFIX + "zzother/proj\n",
    lambda u: "cd " + MAC_PREFIX + "zzother/proj\n",
    lambda u: "ip " + _j("100", "99", "88", "77") + "\n",
    lambda u: "ip " + _j("100", "64", "0", "2") + "\n",
    lambda u: "h " + _j("real-box", "tail9999", "ts", "net") + "\n",
])
def test_counter_example_in_copy_fails(tmp_path, payload):
    user = getpass.getuser()
    dst = _copy_repo(tmp_path)
    base = scan(dst, user)
    with open(str(dst / "README.md"), "a", encoding="utf-8") as f:
        f.write(payload(user))
    assert len(scan(dst, user)) > len(base)   # tainted copy is caught


def test_runs_dir_is_not_scanned(tmp_path):
    user = getpass.getuser()
    dst = _copy_repo(tmp_path)
    base = scan(dst, user)
    d = dst / "docs" / "dev" / "runs"
    d.mkdir(parents=True)
    (d / "x.md").write_text("a" + MAIL_MARK + ".com\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(dst), check=True)
    assert scan(dst, user) == base
