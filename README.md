# projdeck (`proj`)

One place to see every project and Claude Code session on your Mac and SSH hosts,
jump into one in a new iTerm2 tab, and clean stray untracked files reversibly.

## Install (Mac)

```bash
brew install fzf          # needs fzf >= 0.36
git clone git@github.com:xmxurey/projdeck.git ~/dev/projdeck
ln -s ~/dev/projdeck/bin/proj /usr/local/bin/proj   # or any dir on PATH
```
Remote hosts need only `python3` (3.8 or newer, the agent is piped over ssh) and `git`.
The local side is tested on Python 3.12.

On Linux, `sudo apt install fzf` works only if the packaged version is >= 0.36
(`fzf --version`). Older distro packages (for example the one in Ubuntu 22.04's apt, 0.29)
are too old; install from the official release instead:

```bash
git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf && ~/.fzf/install --bin
ln -s ~/.fzf/bin/fzf ~/.local/bin/fzf   # any dir on PATH; or download a binary from the fzf releases page
```

## Configure `~/.config/proj/config.json`

```json
{
  "hosts": [
    {"name": "mac", "local": true, "roots": ["~/dev"]},
    {"name": "s-host",  "ssh": "s-host",   "roots": ["~"]}
  ]
}
```
`ssh` is an alias from `~/.ssh/config` (key auth, `BatchMode`). Optional keys
(defaults in parentheses):

- `tiers_days` (`{"active": 14, "stale": 90}`): positive, finite numbers (not booleans) with
  `active < stale`;
- `scan_depth` (`2`): an integer >= 1 (not a boolean, not `2.0`);
- `include_plain` (`true`): a boolean;
- `auto_sync_minutes` (`30`), `ssh_timeout` (`20`, seconds; raise it if many slow
  repositories make a whole host time out);
- `trash_dir` (`~/.proj-trash`);
- `suspicious_patterns`, `serve`, `dashboard_url` (see "proj serve");
- per host: `roots` (default `["~"]`; a non-empty list of non-empty strings), `tmux`
  (`true`; `true`, `false` or `"always"`: `true` re-attaches a proj tmux session that is
  already running, `"always"` creates one when missing, `false` uses a plain shell), `mux`
  (`"tmux"`; only `"zellij"`, `"tmux"` or `false`), `local` (a boolean), `ssh` (a non-empty
  alias; required unless `local` is true), `brief` (`false`; a boolean only, `null` is
  refused: `true` shows this host's sessions in brief, see "Reading the dashboard").

Environment variables: `PROJ_CONFIG` (config file, default `~/.config/proj/config.json`) and
`PROJ_CACHE` (cache directory, default `~/.cache/proj`).

**Values that do not match the type and value rules above are rejected**, even if they
used to work through Python truthiness or numeric coercion. In general, any value of the
wrong type is refused. Examples that used to load and are now errors:

- `roots: []` (used to fall back to `~`), `roots: null`, `hosts: null`;
- `tmux` other than `true`, `false`, `"always"` (it used to count as true), including `null`;
- `mux: 0` or `mux: 1` (only `"zellij"`, `"tmux"`, `false` are accepted);
- `include_plain: 0` or `1`;
- `scan_depth: 0`, `scan_depth: 2.0`, `scan_depth: "3"`;
- `tiers_days` with `active >= stale`;
- a host whose `local` is not a boolean, or whose `ssh` is an empty string.

Any config error makes every subcommand exit with code 2. The message names the key (for
hosts as `hosts[<i>].<key>`) and never prints the host's values.

`--older` is a command-line argument, not a config key: when it is not a positive, finite
number of days (for example `abc`, `nan` or `inf`), proj reports an error naming `--older`
and exits with code 1.

## Use

| command | what |
|---|---|
| `proj` | all projects; Enter resume (Claude's session picker, or join the project's live zellij session) · ^R resume latest session · ^Y pick session · ^F search prompts · ^X clean · ^U sync · ^L backlog · ^V dashboard |
| `proj sessions` | search all sessions by title, resume |
| `proj grep [words]` | search everything you typed to Claude (all hosts), resume that session |
| `proj sync [host…]` | refresh (auto in background after 30 min); `--live` refreshes only the live layer, `--quiet` prints nothing. Names match `hosts[].name` exactly (case-sensitive); an unknown name is an error |
| `proj report` | markdown: unpushed, no upstream, junk, abandoned, deleted |
| `proj clean HOST:PATH` | move untracked junk to `~/.proj-trash` (never ignored files) |
| `proj restore [host]` | put a batch back |
| `proj trash [--purge --older 30d] [host]` | list / permanently delete old batches |

`proj` groups projects by host (in config order), newest first within each host. With
iTerm2 the list stays open after opening a tab, so you can jump between tasks; Esc quits.

Project names are shown relative to home (`daily/digest`). If a host has `~/PROJECTS.md`,
each `## Tag` section's table rows (first cell `` `rel/path/` ``) tag those projects, and
the list colors names by tag in muted tones, with a legend in the header. With a registry,
only registered projects and those with your own sessions are listed. A 🚩 before the path
(`| 🚩 `quant-poly/` | … |`) flags a project: flagged ones come first within their host. Headless `claude -p`
runs (cron jobs) are never listed. Sessions outside home are listed when they belong to a
scanned project.

The preview shows where you left off in the latest session (your last prompts and
Claude's last reply).

Remote projects: by default (`"tmux": true`) Enter re-attaches a proj tmux session
(`proj-<name>-<hash>` per project, one more per resumed session) only when one is already
running; otherwise it opens a plain login shell running claude, which ends with the
connection. Set `"tmux": "always"` on a host to create the session when missing, so claude
keeps running when the connection drops and opening again re-attaches (this costs memory on
the host). `"tmux": false` always uses a plain shell. Hosts without tmux fall back to the
plain shell. `▶name` marks a project with one live zellij session (`▶name·0` when no claude
runs in it); with several sessions, or any tmux session, it shows `▶n`, the count. Detach
from tmux with `Ctrl-b d`.
Local projects follow the same rule when there is no iTerm2 (e.g. proj run over ssh on a
Linux box): with `"tmux": true` only an already running proj tmux session is joined
(inside tmux proj switches to it instead of nesting), otherwise a plain shell runs in place;
`"always"` creates the session when missing. With iTerm2, local projects open in a new tab
with a plain `cd`.

On a local Linux host with `"mux": "zellij"`, Enter behaves by situation:

- proj runs inside zellij: if a live session works in the project, proj only tells you its
  name (it cannot switch you; Ctrl-o w); otherwise it opens a new tab in the current session;
- not inside zellij, and a live session already works in the project (found by the working
  directories of its processes; names can be anything): Enter joins it;
- not inside zellij, no live session: Enter creates a session named `proj-<project>`
  (the project's path relative to home, e.g. `daily` becomes `proj-daily`; a name
  containing `/` gets a hash, e.g. `proj-dev-process-core-<4-char hash>`). A 4-character
  hash of the project's absolute path (`proj-<name>-<4-char hash>`)
  is appended in three cases: the name contains `/`, the project is outside home, or
  sanitizing or truncating the name dropped characters. An exited `proj-` session of the
  same name is deleted first; a live one is joined;
- zellij unavailable: `claude --resume` runs in place.

Upgrading from the older naming rule: an exited `proj-` session made under the old rule may
be replaced by a new session of the same name and then deleted. That loses nothing, since
proj never resurrects sessions. A `proj-` session still alive under the old rule may be
joined by a different new project that now maps to the same name, so quit old `proj-`
sessions after upgrading. Exited sessions whose name no longer matches any project are not
deleted automatically; `proj report` lists the delete command for each.

Sessions you named yourself are never deleted or resurrected; exited ones are only listed
in the board and in `proj report`. `"mux"` is `"zellij"`, `"tmux"` (default) or `false`; the
older `"tmux"` key still works. Elsewhere Enter also runs `claude --resume` (in the iTerm2 tab, or after
re-attaching a live proj tmux session on SSH hosts).

Git is read with a 5 second limit per command; a large repository may show as a git read
failure (`git?` in the list, "git 读取失败" on the dashboard) when git takes longer.

`proj report` ends with a summary of your own usage (opens, resumes, cleans) from the
local log `~/.cache/proj/usage.jsonl`; nothing leaves the machine.

`proj clean` works on repository roots only (not subdirectories or plain folders).
On macOS, iTerm2 asks for Automation permission the first time proj opens a tab.

Status line: `contrib/statusline-waiting.sh` defines `proj_waiting_segment <session_id>`,
which prints a "sessions waiting for you" segment. Source it from your Claude Code
`statusline.sh` (bash, needs `jq`) and call it with the session id; for example on a host
you reach as s-host (the clone is in `~/dev/projdeck`):

```bash
#!/usr/bin/env bash
input=$(cat)                                  # Claude Code passes JSON on stdin
sid=$(printf '%s' "$input" | jq -r '.session_id // ""')
source ~/dev/projdeck/contrib/statusline-waiting.sh
echo "my status line $(proj_waiting_segment "$sid")"
```

Point `statusLine.command` in `~/.claude/settings.json` at that script. It reads
`$PROJ_CACHE/live/statusline.state` (default `~/.cache/proj/live/`), which `proj serve`
keeps updated locally and pushes to remote hosts (`serve.push_statusline`). It prints
nothing when the file is missing or broken, or when its timestamp differs from the clock
by more than 120 seconds, so keep remote clocks synchronized (NTP).

Tip: set `"cleanupPeriodDays": 365` in Claude Code settings, otherwise transcripts older
than 30 days are deleted and can't be listed.

## Reading the dashboard

Timeline cells (web and terminal): blue is Claude working, green a sub-agent, orange idle and
waiting for input. "Still waiting for you right now" has the same orange and is told apart by
texture: diagonal hatching on the web, `▒` instead of `▆` in the terminal. Heat cells are
purple, light to dark for 1–2, 3–9, 10–29 and 30+ commits; grey is 0 commits, a hollow cell
means the read failed.

Task bar: `■` done (green), `▣` in progress (blue), a red `!` stuck, grey not started. Stuck
is also a shape: on the web that cell is taller than the others. Over 16 tasks the bar
shortens to `12/21 ▣T13`, and to `12/21 !T13` when the current task is stuck.

A host with `"brief": true` in its config entry (for example `em-host`) is shown in brief.
Its busy and idle sessions fold into one summary line per host (`em-host · 进行中 4 · 空闲 1`);
sessions waiting for you, sessions whose state could not be read and sessions without an id
stay listed one by one. In the terminal the row of a project holding such an unreadable session
ends with `⚠ 状态读取失败`, whichever session the row shows. If the host's data is
getting old the summary line turns yellow and says when it was taken. On the web the summary is
an expandable block and the host's lanes sit in a collapsed block; in the terminal there is no
new key: put the cursor on the summary line (its path field is `brief:`) and the preview lists
the folded sessions and their timelines. Enter, `^X`, `^R` and `^Y` do nothing on that line.
The status-line push to the host is unchanged.

## proj serve

`proj serve` 起一个只读网页看板，只在 Tailscale（或本机回环）地址上监听。下面的安装步骤针对 Linux（用户级 systemd）。

访问：只能用 IP、MagicDNS 全名或 Tailscale 节点名访问；用其他 Host 访问会得到 403。
终端里按 `^V` 打开看板。
启动时读不到 `tailscale status` 的话，Host 白名单只认 IP，用名字访问会 403。白名单在启动时就定死，
Tailscale 恢复后要 `systemctl --user restart proj-serve`；journal 里有对应的提示。

本机用 `"mux": "zellij"`、proj 不在 zellij 里、项目没有活会话时，Enter 会新建名为 `proj-<项目名>`
的 zellij 会话（详见上文 zellij 一段）；已退出的 `proj-` 同名会话会被删掉再建。
你自己起名的会话不会被删除或复活，只在看板和 `proj report` 里提示。

配置键（`~/.config/proj/config.json`）：

- `serve.bind`：默认 `null`（用本节点的 Tailscale IPv4）。必须是字符串，为 `null` 或
  loopback / 100.64.0.0/10 内的 IP。命令行参数 `--bind` 可以覆盖。
- `serve.port`：默认 `8787`，取值 1-65535。命令行参数 `--port` 可以覆盖。
- `serve.push_statusline`：默认 `true`；必须是 true 或 false；设成 `false` 就不向远端推送状态栏文件。
- `dashboard_url`：默认 `null`；必须是字符串或 null，`^V` 打开的地址，不设则用本节点 Tailscale IP 加 `serve.port`。

配置不合规时，proj 的所有子命令都会以退出码 2 失败，报错里会点名配置键。

安装（用户级 systemd 单元，默认代码在 `~/dev/projdeck`）：

```bash
mkdir -p ~/.config/systemd/user
cp ~/dev/projdeck/contrib/systemd/proj-serve.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now proj-serve
loginctl enable-linger "$USER"   # 查看：loginctl show-user "$USER" -p Linger
```

需要 linger：不开的话，断开最后一个 ssh 会话后用户级 systemd 会停掉服务，开机也不会自启。

更新代码后重启：

```bash
systemctl --user restart proj-serve
```

卸载：

```bash
systemctl --user disable --now proj-serve
rm ~/.config/systemd/user/proj-serve.service
systemctl --user daemon-reload
```

开机时 Tailscale 地址可能还没就绪，绑定会失败；单元用 `Restart=always` 每 10 秒重试。
用户级 systemd 里没有 `network-online.target`，所以单元里不写它。单元的 `PATH` 需要能找到
`claude`（`~/.local/bin`）、`git`、`ssh`、`tailscale`。

排错：`journalctl --user -u proj-serve`（加 `-f` 跟随）。

如果远端主机（如 em-host）显示采集失败，可能是 ssh 在服务环境里需要 agent，而 systemd 环境里没有 `SSH_AUTH_SOCK`：

1. 先在服务环境下实测：`systemd-run --user -P ssh -o BatchMode=yes <别名> true`。返回 0 就不用动
   （用 `~/.ssh/id_*` 密钥文件的主机不走 agent）。
2. 连不上、确实需要 agent 时，用持久的方式指定一个常驻 agent 的 socket：
   `systemctl --user edit proj-serve`，在 `[Service]` 下写 `Environment=SSH_AUTH_SOCK=<socket>`；
   或写进 `~/.config/environment.d/*.conf`。不要用 `set-environment`，user manager 重启后它就失效了。
3. 不要在 ssh 会话里 `import-environment` 转发来的 socket：断线后它就失效了。
4. 改完执行 `systemctl --user daemon-reload && systemctl --user restart proj-serve`。

网页用的 webkit（`~/dev/webkit`）以拷贝形式放在 `projdeck/vendor/webkit/`，只能由
`scripts/update_webkit.py` 写入（会记录来源提交和每个文件的 sha256）：

```bash
python3 scripts/update_webkit.py [--from DIR]   # 更新拷贝
python3 scripts/update_webkit.py --check         # 只校验，不改动
```

## Tests

```bash
python3 -m pytest -q tests
```

The mutation meta-tests (the `tests/test_*_spec.py` files that break one line in a copy of the
tree and require a named test to fail) copy the whole tree and start a child pytest each, so
they are skipped by default; run them with `PROJ_MUTATION_TESTS=1 python3 -m pytest -q tests`.

A test that runs longer than `PROJ_TEST_TIMEOUT` seconds (default 300, `0` turns it off) is
taken as hung: every thread's stack is printed and the run ends with exit code 1.

Probes and debug scripts run outside pytest go through `scripts/sandbox.py`, which gives them
a temp `HOME`, `PROJ_CACHE` and `PROJ_CONFIG` and stand-ins for ssh, zellij, tmux, claude, fzf
and systemctl, so they never read your real config or reach a real host:

```bash
python3 scripts/sandbox.py [--config fixture.json] -- python3 -m projdeck report
```
