"""T30: systemd unit and README 'proj serve' section."""
import re
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UNIT = ROOT / "contrib" / "systemd" / "proj-serve.service"
README = ROOT / "README.md"

def unit_text():
    return UNIT.read_text(encoding="utf-8")


def serve_section():
    text = README.read_text(encoding="utf-8")
    m = re.search(r"^## proj serve[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, "README has no '## proj serve' section"
    return m.group(1)


def test_unit_exists():
    assert UNIT.is_file()


def test_unit_key_lines():
    lines = unit_text().splitlines()
    assert "Restart=always" in lines
    assert "RestartSec=10" in lines
    # PATH must let the service find claude (~/.local/bin), git, ssh, tailscale.
    path_lines = [l for l in lines if l.startswith("Environment=PATH=")]
    assert len(path_lines) == 1
    dirs = path_lines[0][len("Environment=PATH="):].split(":")
    assert "%h/.local/bin" in dirs
    assert "/usr/bin" in dirs
    assert "ExecStart=/usr/bin/python3 -m projdeck serve" in lines


def test_unit_no_network_online():
    t = unit_text()
    assert "network-online.target" not in t
    assert not re.search(r"^(After|Wants|Requires)=", t, re.M)


@pytest.mark.skipif(os.environ.get("PROJ_LIVE_TESTS") != "1",
                    reason="set PROJ_LIVE_TESTS=1 to run against the real systemd user manager")
def test_unit_systemd_verify_clean(real_which):
    r = subprocess.run([real_which("systemd-analyze"), "--user", "verify", str(UNIT)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stderr.strip() == "", r.stderr


def code_block_lines(section):
    """Stripped lines inside ``` fences; prose and comments never match."""
    lines, inside = [], False
    for raw in section.splitlines():
        if raw.strip().startswith("```"):
            inside = not inside
        elif inside:
            lines.append(raw.strip())
    return lines


@pytest.mark.parametrize("command", [
    "systemctl --user enable --now proj-serve",
    "systemctl --user restart proj-serve",
])
def test_readme_key_commands_are_code_block_lines(command):
    assert command in code_block_lines(serve_section())


def test_code_block_lines_rejects_comment_and_prose():
    cmd = "systemctl --user enable --now proj-serve"
    commented = "```bash\n# " + cmd + "\n```\n"
    prose = cmd + "\n```bash\necho hi\n```\n"
    assert cmd not in code_block_lines(commented)
    assert cmd not in code_block_lines(prose)
    assert cmd in code_block_lines("```bash\n" + cmd + "\n```\n")


@pytest.mark.parametrize("needle", [
    "journalctl --user -u proj-serve",
    "SSH_AUTH_SOCK",
    "只能用 IP、MagicDNS 全名或 Tailscale 节点名访问",
    "scripts/update_webkit.py",
    "`serve.",
    "dashboard_url",
])
def test_readme_serve_section_acceptance_2(needle):
    assert needle in serve_section()


@pytest.mark.parametrize("needle", [
    "serve.bind", "serve.port", "dashboard_url",
    "loopback", "100.64.0.0/10", "1-65535", "--port",
])
def test_readme_serve_section_config_keys_ruling_1(needle):
    assert needle in serve_section()


def test_readme_config_values_documented_with_defaults():
    s = serve_section()
    assert "8787" in s
    assert "null" in s
    assert "退出码 2" in s


@pytest.mark.parametrize("needle", ["proj-<项目名>", "已退出", "删掉"])
def test_readme_zellij_session_rule_ruling_3(needle):
    assert needle in serve_section()


def test_readme_section_scoping_is_bounded():
    # The extractor must stop at the next "## " heading: other sections'
    # content must not leak into the serve section.
    s = serve_section()
    assert "## " not in s
    assert "proj clean HOST:PATH" not in s


def _flat(t):
    return re.sub(r"\s+", " ", t)


def test_readme_no_stale_zellij_claim():
    t = _flat(README.read_text(encoding="utf-8")).lower()
    assert "never creates" not in t
    assert "never creates or resurrects" not in t
    assert "resurrects zellij" not in t


def test_readme_no_in_place_resume_when_no_live_session():
    # T31: with no live session Enter creates proj-<project>; it only runs
    # `claude --resume` in place when zellij is unavailable.
    t = _flat(README.read_text(encoding="utf-8"))
    assert not re.search(r"Without one,? Enter runs `?claude --resume", t)
    assert not re.search(r"没有活会话[^。\n]{0,20}(就地|直接)运行", t)


def test_stale_guards_fire_on_old_readme():
    # Self-check of the guard patterns against the 1268444 wording.
    old = _flat("""Without one, Enter runs `claude --resume` in a plain shell, or in a new
    tab when proj itself runs inside zellij. proj never creates or resurrects zellij sessions.""")
    assert "never creates" in old.lower()
    assert re.search(r"Without one,? Enter runs `?claude --resume", old)


NEGATIONS = ("不要", "不能", "不可", "别")


def _sentences(text):
    return [x for x in re.split(r"[。\n]", text) if x.strip()]


def check_ssh_advice(s):
    """Round-2 ruling: probe with systemd-run, persistent env, reload+restart,
    set-environment / import-environment only in negated sentences."""
    assert "SSH_AUTH_SOCK" in s
    assert "systemd-run --user -P ssh -o BatchMode=yes" in s
    assert "systemctl --user edit proj-serve" in s or "environment.d" in s
    tail = s[s.index("systemd-run --user -P"):]
    assert any("daemon-reload" in x and "restart proj-serve" in x
               for x in _sentences(tail))
    for word in ("set-environment", "import-environment"):
        hits = [x for x in _sentences(s) if word in x]
        if word == "set-environment":
            assert hits, "set-environment should be mentioned (as a warning)"
        for sent in hits:
            assert any(n in sent for n in NEGATIONS), (word, sent)


def check_whitelist_ip_only(s):
    flat = _flat(s)
    sents = _sentences(flat.replace("。", "。\n"))
    assert any("只认" in x and "IP" in x and "白名单" in x for x in sents)
    assert any("恢复" in x and "restart proj-serve" in x for x in sents)


def test_readme_ssh_auth_sock_advice_ruling_I3():
    check_ssh_advice(serve_section())


def test_readme_whitelist_ip_only_ruling_I4():
    check_whitelist_ip_only(serve_section())


# Fixed copy of the ssh-advice paragraph from an older README (it used
# set-environment and never probed with systemd-run). Kept inline so the test
# does not depend on git history; host names are fictional.
OLD_SERVE_SECTION = """\
如果 ssh 需要 agent，而 systemd 环境里没有 `SSH_AUTH_SOCK`，远端主机（如 em-host）会显示采集失败。处理：

1. 先看 user manager 里有没有常驻的 agent socket：`systemctl --user show-environment | grep SSH_AUTH_SOCK`
   （例如 s-host 上已有 `/run/user/1000/gnupg/S.gpg-agent.ssh`）。有就不用动。
2. 没有的话：`systemctl --user set-environment SSH_AUTH_SOCK=<常驻 agent 的 socket>`，
   或在 drop-in 里写 `Environment=SSH_AUTH_SOCK=...`；也可以给本机配一把不需要 agent 的密钥。
3. 不要在 ssh 会话里 `import-environment` 转发来的 socket：断线后它就失效了。
4. 改完要 `systemctl --user restart proj-serve` 才生效。
"""


def test_guards_discriminate_on_mutated_sections():
    good = serve_section()
    check_ssh_advice(good)
    check_whitelist_ip_only(good)
    bad_ssh = [
        good.replace("不要用 `set-environment`", "可以用 `set-environment`"),
        good.replace("不要在 ssh 会话里 `import-environment`", "在 ssh 会话里 `import-environment`"),
        good.replace("systemd-run --user -P ssh -o BatchMode=yes", "ssh"),
        good.replace("daemon-reload && ", ""),
    ]
    for b in bad_ssh:
        assert b != good
        with pytest.raises(AssertionError):
            check_ssh_advice(b)
    bad_wl = [
        good.replace("只认 IP", "只认 ip"),
        good.replace("Tailscale 恢复后要 `systemctl --user restart proj-serve`", "Tailscale 恢复后自动好"),
    ]
    for b in bad_wl:
        assert b != good
        with pytest.raises(AssertionError):
            check_whitelist_ip_only(b)


def test_ssh_guard_rejects_old_readme_fragment():
    with pytest.raises(AssertionError):
        check_ssh_advice(OLD_SERVE_SECTION)


def test_readme_linger_ruling_I5():
    s = serve_section()
    assert "loginctl enable-linger" in s
    assert "linger" in s


def test_readme_key_table_updated_ruling_2():
    t = README.read_text(encoding="utf-8")
    for frag in ("^Y", "^L", "^V"):
        assert frag in t
    assert "^S" not in t


@pytest.mark.skipif(os.environ.get("PROJ_LIVE_TESTS") != "1",
                    reason="set PROJ_LIVE_TESTS=1 to check the live-test path lookup")
def test_live_lookup_skips_stand_in_dirs(real_which):
    for prog in ("systemd-analyze", "zellij"):
        found = real_which(prog)
        assert os.path.dirname(found) not in os.environ["PROJ_STUB_DIRS"].split(os.pathsep), found
