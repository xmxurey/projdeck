# Enter 弹出会话选择器 + 连接活 zellij 会话 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 Enter 时，项目已有活的 zellij 会话就连进去，没有就在项目目录运行 `claude --resume`；列表上能看到 zellij 会话。

**Architecture:** agent 读 `/proc` 找出 zellij 服务进程及其子孙进程的工作目录，用现有 `owner()` 归到项目，结果写成 `live_zellij`。config 把每台主机归一成一个 `mux`（`"zellij"` / `"tmux"` / `False`）。cli 新增 `open_project()`：本机 zellij 主机在打开前重新查一遍活会话，再决定是连接、提示、新开标签页，还是交给 `launch()` 跑选择器。opener 负责拼命令。

**Tech Stack:** Python ≥3.8 标准库，fzf，pytest。

**Spec:** `docs/superpowers/specs/2026-11-18-enter-resume-zellij-design.md`

## Global Constraints

- `agent.py` 保持只用标准库、不 import projdeck（它会经 ssh 送到远端运行）。
- 不升级 `SCHEMA`；`live_zellij` 是可选字段，缺失时当空列表。
- 不新建 zellij 会话，不复活已退出的会话：调用 `zellij attach` 前必须确认会话仍然活着（`zellij list-sessions -n` 里那一行不带 `EXITED`）。
- zellij 只支持本机 Linux 主机；macOS 本机或远端主机配 `"mux": "zellij"` 时，记一条警告显示在列表顶部，并当作 `False`。
- 界面文字沿用现有英文。
- 现有测试全部保持通过；测试命令 `python3 -m pytest -q tests`。

## 文件

| 文件 | 改动 |
|---|---|
| `projdeck/agent.py` | 新增 `proc_table`、`zellij_sessions`、`zellij_by_project`；`collect` 写 `live_zellij` |
| `projdeck/config.py` | 新增 `resolve_mux`；`load_config` 归一 `mux` 并收集 `warnings` |
| `projdeck/model.py` | 行里带上 `mux` |
| `projdeck/opener.py` | 新增 `PICKER`、`_tmux_mode`、`zellij_attach`、`zellij_new_tab`；`build_command` 改用 `_tmux_mode` |
| `projdeck/ui.py` | ▶ 标记、预览行、页头、`pick_live` |
| `projdeck/cli.py` | 新增 `open_project`；`launch` 无会话时跑选择器；`cmd_browse` 改走 `open_project` 并显示配置警告 |
| `tests/test_agent_zellij.py` | 新建 |
| `tests/test_sync.py`、`tests/test_opener.py`、`tests/test_ui_cli.py` | 补用例 |
| `README.md` | 更新 Enter 与 zellij 的说明 |

---

### Task 1: agent 找出活 zellij 会话

**Files:**
- Modify: `projdeck/agent.py`（`tmux_sessions` 之后；`collect` 里 `live_tmux` 那段之后）
- Test: `tests/test_agent_zellij.py`

**Interfaces:**
- Produces: `agent.proc_table(proc="/proc") -> {pid: (ppid, comm, argv, cwd)}`；`agent.zellij_sessions(proc="/proc") -> [(name, [cwd, ...], n_claude)]`；`agent.zellij_by_project(paths, proc="/proc") -> {project_path: [{"name": str, "claude": int}]}`；collect 输出的项目可带 `live_zellij`。

- [ ] **Step 1: 写会红的测试** —— 在 tmp_path 下造假的 `/proc`：

```python
import os
from projdeck import agent


def fake_proc(root, procs):
    """procs: {pid: (ppid, comm, argv list, cwd or None)}"""
    for pid, (ppid, comm, argv, cwd) in procs.items():
        d = root / str(pid)
        d.mkdir()
        (d / "stat").write_text("%d (%s) S %d 1 1 0 -1\n" % (pid, comm, ppid))
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (d / "comm").write_text(comm + "\n")
        if cwd:
            os.symlink(cwd, str(d / "cwd"))
    (root / "self").mkdir()  # non-numeric entries are ignored
    return str(root)


SERVER = "/home/u/.local/bin/zellij"
SOCK = "/run/user/1000/zellij/contract_version_1/"


def test_zellij_sessions_maps_descendants(tmp_path):
    proc = fake_proc(tmp_path, {
        10: (1, "zellij", [SERVER, "--server", SOCK + "research"], "/home/u"),
        11: (10, "zsh", ["/usr/bin/zsh"], "/home/u/invest"),
        12: (11, "claude", ["claude", "--resume"], "/home/u/invest"),
        13: (10, "zsh", ["/usr/bin/zsh"], "/home/u/invest/sub"),
        20: (1, "zellij", [SERVER, "--server", SOCK + "empty"], "/home/u"),
        21: (20, "bash", ["bash"], "/home/u/fin"),
        30: (1, "zellij", [SERVER, "attach", "research"], "/home/u"),  # a client, not a server
        40: (1, "zsh", ["zsh"], None),  # cwd unreadable
    })
    got = sorted(agent.zellij_sessions(proc))
    assert got == [("empty", ["/home/u/fin"], 0),
                   ("research", ["/home/u/invest", "/home/u/invest/sub"], 1)]


def test_comm_with_spaces_and_parens(tmp_path):
    proc = fake_proc(tmp_path, {
        10: (1, "zellij", [SERVER, "--server", SOCK + "s"], "/"),
        11: (10, "a (b) c", ["x"], "/w"),
    })
    assert agent.zellij_sessions(proc) == [("s", ["/w"], 0)]


def test_zellij_by_project_uses_longest_owner(tmp_path):
    proc = fake_proc(tmp_path, {
        10: (1, "zellij", [SERVER, "--server", SOCK + "dev"], "/"),
        11: (10, "zsh", ["zsh"], "/home/u/dev/projdeck/tests"),
        12: (10, "zsh", ["zsh"], "/home/u/dev"),
        13: (10, "zsh", ["zsh"], "/tmp"),
    })
    out = agent.zellij_by_project(["/home/u/dev", "/home/u/dev/projdeck"], proc)
    assert out == {"/home/u/dev": [{"name": "dev", "claude": 0}],
                   "/home/u/dev/projdeck": [{"name": "dev", "claude": 0}]}


def test_no_proc_means_no_sessions(tmp_path):
    assert agent.zellij_sessions(str(tmp_path / "missing")) == []


def test_collect_marks_live_zellij(tmp_path, make_repo, monkeypatch):
    repo = make_repo("proj")
    monkeypatch.setattr(agent, "tmux_sessions", lambda: [])
    monkeypatch.setattr(agent, "zellij_sessions", lambda proc="/proc": [("research", [repo], 2)])
    out = agent.collect({"roots": [str(tmp_path)], "scan_depth": 1,
                         "claude_dir": str(tmp_path / "noclaude")})
    p = [p for p in out["projects"] if p["path"] == repo][0]
    assert p["live_zellij"] == [{"name": "research", "claude": 2}]
```

（`make_repo` 的签名以 `tests/conftest.py` 为准；照 `test_collect_marks_live_tmux_sessions` 的写法调用。）

- [ ] **Step 2: 跑测试，确认红**：`python3 -m pytest -q tests/test_agent_zellij.py` → `AttributeError: ... proc_table / zellij_sessions`。

- [ ] **Step 3: 实现**（`agent.py`，放在 `tmux_sessions` 之后）：

```python
def proc_table(proc="/proc"):
    """{pid: (ppid, comm, argv, cwd)} for the processes we can read; {} without /proc."""
    out = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        d = os.path.join(proc, n)
        try:
            with open(os.path.join(d, "stat"), encoding="utf-8", errors="replace") as f:
                stat = f.read()
            with open(os.path.join(d, "cmdline"), "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except OSError:
            continue  # gone, or not ours
        # "pid (comm) state ppid ...": comm may contain spaces and parens
        head, _, rest = stat.rpartition(")")
        comm = head.partition("(")[2]
        fields = rest.split()
        if len(fields) < 2 or not fields[1].isdigit():
            continue
        try:
            cwd = os.readlink(os.path.join(d, "cwd"))
        except OSError:
            cwd = None
        out[int(n)] = (int(fields[1]), comm, argv, cwd)
    return out


def zellij_sessions(proc="/proc"):
    """[(name, sorted cwds of its descendants, number of claude processes)] for every
    live zellij session: one `zellij --server <dir>/<name>` process each. Exited
    sessions have no server, so they never show up."""
    table = proc_table(proc)
    children = {}
    for pid, (ppid, _, _, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    out = []
    for pid, (_, _, argv, _) in table.items():
        if (len(argv) < 3 or os.path.basename(argv[0]) != "zellij"
                or argv[1] != "--server"):
            continue
        cwds, n_claude, todo = set(), 0, list(children.get(pid, []))
        while todo:
            c = todo.pop()
            _, comm, _, cwd = table[c]
            if cwd:
                cwds.add(cwd)
            if comm == "claude":
                n_claude += 1
            todo.extend(children.get(c, []))
        out.append((os.path.basename(argv[2].rstrip("/")), sorted(cwds), n_claude))
    return out


def zellij_by_project(paths, proc="/proc"):
    """{project path: [{"name", "claude"}]}: a session belongs to every project one of
    its processes works in (longest matching project wins, like session ownership)."""
    out = {}
    for name, cwds, n in sorted(zellij_sessions(proc)):
        owners = {owner(c, paths) or owner(os.path.realpath(c), paths) for c in cwds}
        for o in sorted(owners - {None}):
            out.setdefault(o, []).append({"name": name, "claude": n})
    return out
```

`collect` 里，在 `for name, path in tmux_sessions():` 那段之后加：

```python
    for o, live in zellij_by_project(list(projects)).items():
        projects[o]["live_zellij"] = live
```

注意：`zellij_by_project` 内部调用模块级 `zellij_sessions`，测试里 monkeypatch 能生效。

- [ ] **Step 4: 加一条真机对照用例**（同一测试文件）：

```python
import re
import shutil
import subprocess
import pytest


def test_matches_zellij_dump_layout_on_this_machine():
    live = agent.zellij_sessions()
    if not live or not shutil.which("zellij"):
        pytest.skip("no live zellij session here")
    for name, cwds, _ in live:
        r = subprocess.run(["zellij", "--session", name, "action", "dump-layout"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True, timeout=5)
        for top in re.findall(r'cwd "(/[^"]*)"', r.stdout):
            assert any(c == top or c.startswith(top.rstrip("/") + "/") for c in cwds), (name, top)
```

`dump-layout` 里的绝对 `cwd` 是标签页/窗格的起始目录，/proc 里一定有某个进程在它或它的子目录下（窗格 shell 本身）。

- [ ] **Step 5: 跑测试，确认全绿**：`python3 -m pytest -q tests`。

---

### Task 2: config 归一 `mux`

**Files:**
- Modify: `projdeck/config.py`
- Test: `tests/test_sync.py`（已有 `load_config` 用例的文件）

**Interfaces:**
- Produces: `config.resolve_mux(host, platform=sys.platform) -> (mux, warning or None)`；`load_config` 之后每个 host 都有 `h["mux"]`，`cfg["warnings"]` 是字符串列表。

- [ ] **Step 1: 写会红的测试**：

```python
import pytest
from projdeck import config


@pytest.mark.parametrize("host, want", [
    ({"name": "a", "local": True}, "tmux"),
    ({"name": "a", "local": True, "tmux": False}, False),
    ({"name": "a", "local": True, "tmux": "always"}, "tmux"),
    ({"name": "a", "local": True, "mux": "zellij"}, "zellij"),
    ({"name": "a", "local": True, "mux": False, "tmux": True}, False),  # mux wins
    ({"name": "a", "ssh": "a", "mux": "tmux"}, "tmux"),
])
def test_resolve_mux(host, want):
    assert config.resolve_mux(host, platform="linux") == (want, None)


def test_zellij_only_on_local_linux():
    mux, warn = config.resolve_mux({"name": "e", "ssh": "e", "mux": "zellij"}, platform="linux")
    assert mux is False and "e" in warn and "zellij" in warn
    mux, warn = config.resolve_mux({"name": "m", "local": True, "mux": "zellij"}, platform="darwin")
    assert mux is False and "Linux" in warn


def test_bad_mux_value_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        config.resolve_mux({"name": "a", "local": True, "mux": "screen"})


def test_load_config_fills_mux_and_warnings(tmp_path):
    cfg = config.load_config(write_cfg(tmp_path, {"hosts": [
        {"name": "s-host", "local": True, "mux": "zellij"},
        {"name": "em", "ssh": "em", "mux": "zellij"}]}))
    assert [h["mux"] for h in cfg["hosts"]] == ["zellij" if config.sys.platform.startswith("linux") else False, False]
    assert any("em" in w for w in cfg["warnings"])
```

（`write_cfg` 是 `tests/test_sync.py` 里已有的辅助函数。）

- [ ] **Step 2: 确认红**：`python3 -m pytest -q tests/test_sync.py` → `AttributeError: resolve_mux`。

- [ ] **Step 3: 实现**（`config.py`：`import sys`；`DEFAULTS` 加 `"warnings": []`；新增函数；`load_config` 的 host 循环末尾调用）：

```python
MUX_VALUES = ("zellij", "tmux", False)


def resolve_mux(h, platform=None):
    """The host's terminal multiplexer: "mux" if given, else the older "tmux" key.
    zellij works on local Linux hosts only (it is found through /proc)."""
    platform = platform or sys.platform
    mux = h["mux"] if "mux" in h else ("tmux" if h.get("tmux", True) else False)
    if mux not in MUX_VALUES:
        raise ValueError('host %r: "mux" must be "zellij", "tmux" or false' % h.get("name"))
    if mux == "zellij" and not h.get("local"):
        return False, 'host %s: "mux": "zellij" works on the local host only; using a plain shell' % h["name"]
    if mux == "zellij" and not platform.startswith("linux"):
        return False, 'host %s: "mux": "zellij" needs Linux (/proc); using a plain shell' % h["name"]
    return mux, None
```

在 `load_config` 的 host 校验循环里（`names.add(name)` 之后）：

```python
        h["mux"], warning = resolve_mux(h)
        if warning:
            cfg["warnings"].append(warning)
```

- [ ] **Step 4: 确认绿**：`python3 -m pytest -q tests`。

---

### Task 3: opener 拼命令，model 带上 mux

**Files:**
- Modify: `projdeck/opener.py`、`projdeck/model.py:35`
- Test: `tests/test_opener.py`

**Interfaces:**
- Consumes: 行里的 `mux`（Task 2 归一后的值）。
- Produces: `opener.PICKER = "claude --resume"`；`opener.zellij_attach(name, path) -> str`（sh 脚本）；`opener.zellij_new_tab(path, title, run=subprocess.run) -> None`；`build_command` 对 `mux` 为 zellij/False 的主机不再走 tmux。

- [ ] **Step 1: 写会红的测试**：

```python
ZELLIJ = {"local": True, "ssh": None, "mux": "zellij"}


def _fake_zellij(bindir, sessions):
    z = bindir / "zellij"
    z.write_text('#!/bin/sh\nif [ "$1" = list-sessions ]; then printf "%s"; exit 0; fi\n'
                 'for a in "$@"; do echo "[$a]"; done\n' % sessions)
    z.chmod(0o755)


def _sh(script, tmp_path, env_extra=None):
    env = {"SHELL": "/bin/sh", "PATH": str(tmp_path / "bin") + ":/usr/bin:/bin"}
    env.update(env_extra or {})
    return subprocess.run(["/bin/sh", "-c", script], env=env, stdout=subprocess.PIPE,
                          universal_newlines=True, input="").stdout.splitlines()


def test_zellij_attach_only_when_live(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (tmp_path / "bin").mkdir()
    _fake_zellij(tmp_path / "bin", "research [Created 2h ago] \\nold [Created 9d ago] (EXITED - attach to resurrect)\\n")
    assert _sh(opener.zellij_attach("research", str(d)), tmp_path) == ["[attach]", "[research]"]
    # exited (or vanished) session: never resurrect, run the picker in a plain shell
    out = _sh(opener.zellij_attach("old", str(d)).replace("claude --resume", "pwd"), tmp_path)
    assert out == [str(d)]


def test_picker_is_the_enter_command():
    assert opener.PICKER == "claude --resume"
    cmd = opener.build_command(dict(ZELLIJ), "/a", opener.PICKER, new_tab=False)
    assert "tmux" not in cmd and "claude --resume" in cmd


def test_mux_false_and_zellij_skip_tmux_but_tmux_mux_keeps_it():
    assert "tmux" not in opener.build_command({"local": False, "ssh": "e", "mux": False}, "/p", opener.PICKER)
    assert "tmux has-session" in opener.build_command({"local": False, "ssh": "e", "mux": "tmux"}, "/p", opener.PICKER)
    assert "new-session" in opener.build_command({"local": False, "ssh": "e", "mux": "tmux", "tmux": "always"}, "/p")


def test_zellij_new_tab_argv():
    calls = []
    opener.zellij_new_tab("/w/p q", "s-host:p", run=lambda argv, **k: calls.append(argv))
    argv = calls[0]
    assert argv[:7] == ["zellij", "action", "new-tab", "--cwd", "/w/p q", "--name", "s-host:p"]
    assert argv[7] == "--" and argv[9:] == ["-lic", 'claude --resume; exec "$SHELL" -l']
```

- [ ] **Step 2: 确认红**：`python3 -m pytest -q tests/test_opener.py`。

- [ ] **Step 3: 实现**（`opener.py`）：

```python
PICKER = "claude --resume"  # Claude's own session picker for this directory


def _tmux_mode(target):
    """What _remote_script gets as tmux=: only hosts whose multiplexer is tmux use it."""
    mux = target.get("mux", "tmux" if target.get("tmux", True) else False)
    if mux != "tmux":
        return False
    return "always" if target.get("tmux") == "always" else True


def _then_shell(inner):
    return '"$SHELL" -lic %s; %s' % (q(inner), LOGIN_SHELL)


def zellij_attach(name, path):
    """Join a live zellij session; if it has exited meanwhile, do not resurrect it:
    run the picker in a plain shell instead."""
    live = ("zellij list-sessions -n 2>/dev/null | "
            "awk -v n=%s '$1 == n && !/EXITED/ {f = 1} END {exit !f}'" % q(name))
    return "if %s; then exec zellij attach %s; else cd %s && %s; fi" % (
        live, q(name), q(path), _then_shell(PICKER))


def zellij_new_tab(path, title, run=subprocess.run):
    """Inside zellij: a new tab in the current session runs the picker; proj stays open."""
    shell = os.environ.get("SHELL") or "/bin/sh"
    run(["zellij", "action", "new-tab", "--cwd", path, "--name", title, "--",
         shell, "-lic", '%s; exec "$SHELL" -l' % PICKER], check=True)
```

`_remote_script` 里把 `rest = '"$SHELL" -lic %s; %s' % (q(inner), LOGIN_SHELL)` 改成 `rest = _then_shell(inner)`。`build_command` 里两处 `target.get("tmux", True)` 改成 `_tmux_mode(target)`：

```python
def build_command(target, path, inner=None, new_tab=True, session_id=None):
    tmux = _tmux_mode(target)
    if target.get("local"):
        if not new_tab:  # no iTerm2 tab: runs right here
            return _remote_script(path, inner, tmux=tmux, session_id=session_id)
        return "cd %s" % q(path) + (" && %s" % inner if inner else "")
    script = _remote_script(path, inner, tmux=tmux, session_id=session_id)
    return "ssh -t %s %s" % (q(target["ssh"]), q(script))
```

（原来 local、`not new_tab`、tmux 关闭时走 `inner = LOGIN_SHELL` 的分支：`_remote_script(path, None, tmux=False)` 生成的 `cd X && exec "$SHELL" -l` 与之等价，现有用例 `test_local_commands` 的断言不变。）

`model.py` 的 `r.update(...)` 加 `mux=h.get("mux", "tmux" if h.get("tmux", True) else False)`。

- [ ] **Step 4: 确认绿**：`python3 -m pytest -q tests`。

---

### Task 4: 界面与 Enter 流程

**Files:**
- Modify: `projdeck/ui.py`、`projdeck/cli.py`
- Test: `tests/test_ui_cli.py`

**Interfaces:**
- Consumes: `agent.zellij_by_project`（Task 1）、`opener.PICKER` / `zellij_attach` / `zellij_new_tab`（Task 3）、行里的 `mux`、`cfg["warnings"]`（Task 2）。
- Produces: `ui.live_flag(r) -> str`、`ui.pick_live(live, name) -> dict`、`cli.open_project(rows, r) -> (outcome, notice)`，outcome ∈ `"iterm"`、`"tab"`、`"inline"`、`None`。

- [ ] **Step 1: 写会红的测试**：

```python
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


def test_enter_without_live_session_runs_picker_via_launch(monkeypatch):
    out, calls = _open(monkeypatch, [])
    assert out == ("inline", None) and calls["launch"] == ["/home/u/quant"]


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
```

在已有的 `test_browse_stays_open_after_iterm_launch` 和 `test_browse_exits_after_inline_launch` 不用改：远端 `vm` 主机 `mux` 为 tmux，`open_project` 直接调 `launch`。

- [ ] **Step 2: 确认红**：`python3 -m pytest -q tests/test_ui_cli.py`。

- [ ] **Step 3: 实现 ui**：

```python
PROJECT_HEADER = ("Enter: resume · ^R: resume latest · ^S: pick session · ^F: search prompts · "
                  "^X: clean · ^U: sync")


def live_flag(r):
    """▶name for one zellij session (▶name·0 when no claude runs in it), else ▶count."""
    z, t = r.get("live_zellij") or [], r.get("live_tmux") or []
    if len(z) == 1 and not t:
        return "▶%s%s" % (z[0]["name"], "" if z[0]["claude"] else "·0")
    return "▶%d" % (len(z) + len(t)) if z or t else ""


def pick_live(live, name):
    lines = ["%s\t%d claude" % (s["name"], s["claude"]) for s in live]
    chosen = pick_line(lines, "%s has %d live zellij sessions: pick one" % (name, len(live)))
    return live[lines.index(chosen)]
```

`project_line` 里把

```python
    if r.get("live_tmux"):
        flags.append("▶%d" % len(r["live_tmux"]))
```

换成

```python
    if live_flag(r):
        flags.append(live_flag(r))
```

`preview_text` 里在 `live_tmux` 那行之前加：

```python
    if r.get("live_zellij"):
        out.append("▶ zellij: %s" % ", ".join("%s (%d claude)" % (s["name"], s["claude"])
                                              for s in r["live_zellij"]))
```

- [ ] **Step 4: 实现 cli**（`from projdeck import agent`；`launch` 改一行；新增 `open_project`；`cmd_browse` 改 Enter 分支与初始 notice）：

```python
def launch(row, path, session=None):
    new_tab = opener.has_iterm()
    inner = opener.resume_inner(session["id"]) if session else opener.PICKER
    ...  # 其余不变


def open_project(rows, r):
    """Enter on a project. A local zellij host joins the project's live session (looked
    up now, not from the cache); without one the picker runs. Returns (outcome, notice):
    "iterm"/"tab" keep proj open, "inline" means it ran here, None opened nothing."""
    name = "%s:%s" % (r["host"], os.path.basename(r["path"].rstrip("/")))
    if r.get("local") and r.get("mux") == "zellij":
        paths = [x["path"] for x in rows if x["host"] == r["host"]]
        live = agent.zellij_by_project(paths).get(r["path"], [])
        if len(live) > 1:
            try:
                live = [ui.pick_live(live, name)]
            except ui.Cancelled:
                return None, None
        here = os.environ.get("ZELLIJ_SESSION_NAME")
        if live:
            s = live[0]["name"]
            if here:  # zellij cannot switch a client to another session from the CLI
                where = "this session" if s == here else "zellij session %s" % s
                return None, "%s is already open in %s: press Ctrl-o w to switch" % (name, where)
            log_event("open", r["host"], r["path"], attach=s)
            return opener.open_command(opener.zellij_attach(s, r["path"]), name, iterm=False), None
        if here:
            log_event("open", r["host"], r["path"])
            opener.zellij_new_tab(r["path"], name)
            return "tab", "opened %s in a new zellij tab" % name
    return launch(r, r["path"]), None
```

`cmd_browse`：

- 开头 `notice = last = None` 改成 `notice, last = "; ".join(cfg.get("warnings") or []) or None, None`。
- 把末尾的

```python
        if launch(r, session["cwd"] if session else path, session) != "iterm":
            return 0
        notice = "opened %s%s in a new iTerm2 tab" % (name, " (resume)" if session else "")
```

换成

```python
        if session:
            outcome, msg = launch(r, session["cwd"], session), None
        else:
            outcome, msg = open_project(rows, r)
        if outcome == "inline":
            return 0
        notice = msg or ("opened %s%s in a new iTerm2 tab" % (name, " (resume)" if session else "")
                         if outcome == "iterm" else None)
```

- [ ] **Step 5: 确认绿**：`python3 -m pytest -q tests`。

---

### Task 5: 文档与本机配置

**Files:**
- Modify: `README.md`、`~/.config/proj/config.json`（本机配置，不在仓库里）

- [ ] **Step 1**：README「Use」表格里 `proj` 一行把 `Enter open` 改成 `Enter resume (Claude's session picker, or join the project's live zellij session)`；在 tmux 那段后面加一段：

```markdown
On a local Linux host with `"mux": "zellij"`, Enter joins the zellij session that already
works in the project (found by the working directories of its processes; session names
can be anything). Without one, Enter runs `claude --resume` in a plain shell, or in a new
tab when proj itself runs inside zellij. proj never creates or resurrects zellij sessions.
Inside zellij it cannot switch you to another session, so it tells you the name instead
(Ctrl-o w). `"mux"` is `"zellij"`, `"tmux"` (default) or `false`; the older `"tmux"` key
still works.
```

配置键列表 `Optional keys:` 加上 `mux`。

- [ ] **Step 2**：本机配置 s-host 那台加 `"mux": "zellij"`：

```json
{"name": "s-host", "local": true, "roots": ["~"], "mux": "zellij"}
```

- [ ] **Step 3**：真机走一遍：在 s-host 上 `proj`（不在 zellij 里），确认 invest-research 一行显示 `▶research`，预览有 `▶ zellij: research (2 claude)`；不按 Enter 连接（会占用当前终端），改用 `python3 -c` 调 `agent.zellij_by_project` 打印结果核对。

- [ ] **Step 4**：`python3 -m pytest -q tests` 全绿。

---

## Self-review

- 规格 §2（配置）→ Task 2、Task 3（model）；§3 表格 4 行 → Task 4 `open_project`（行 1：attach；行 2：提示；行 3：`launch` 跑选择器；行 4：新标签页），连接前确认存活 → Task 3 `zellij_attach`；远端 tmux 与 iTerm 跑选择器 → Task 4 `launch` 改 `PICKER`；多个活会话 → `pick_live`；日志 `attach` 字段 → `open_project`。§4 → Task 1；§5 → Task 4 ui；§6 不改 ^R/^S → `cmd_browse` 中 session 分支照旧调 `launch`；§8 测试 → 各 Task。
- 名称一致：`zellij_by_project`、`zellij_attach`、`zellij_new_tab`、`PICKER`、`live_flag`、`pick_live`、`open_project`、`resolve_mux` 在定义与使用处一致。
