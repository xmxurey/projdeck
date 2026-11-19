# projdeck (`proj`)

One place to see every project and Claude Code session on your Mac and SSH hosts,
jump into one in a new iTerm2 tab, and clean stray untracked files reversibly.

## Install (Mac)

```bash
brew install fzf
git clone git@github.com:xmxurey/projdeck.git ~/dev/projdeck
ln -s ~/dev/projdeck/bin/proj /usr/local/bin/proj   # or any dir on PATH
```
Remote hosts need only `python3` and `git` (the agent is piped over ssh).

## Configure `~/.config/proj/config.json`

```json
{
  "hosts": [
    {"name": "mac", "local": true, "roots": ["~/dev"]},
    {"name": "s-host",  "ssh": "s-host",   "roots": ["~"]}
  ]
}
```
`ssh` is an alias from `~/.ssh/config` (key auth, `BatchMode`). Optional keys:
`tiers_days`, `scan_depth`, `include_plain`, `auto_sync_minutes`, `ssh_timeout`,
`suspicious_patterns`, `trash_dir`, `mux`, `serve`, `dashboard_url` (see "proj serve").

## Use

| command | what |
|---|---|
| `proj` | all projects; Enter resume (Claude's session picker, or join the project's live zellij session) · ^R resume latest session · ^Y pick session · ^F search prompts · ^X clean · ^U sync · ^L backlog · ^V dashboard |
| `proj sessions` | search all sessions by title, resume |
| `proj grep [words]` | search everything you typed to Claude (all hosts), resume that session |
| `proj sync [host…]` | refresh (auto in background after 30 min) |
| `proj report` | markdown: unpushed, no upstream, junk, abandoned, deleted |
| `proj clean HOST:PATH` | move untracked junk to `~/.proj-trash` (never ignored files) |
| `proj restore [host]` | put a batch back |
| `proj trash [--purge --older 30d]` | list / permanently delete old batches |

`proj` groups projects by host (in config order), newest first within each host. With
iTerm2 the list stays open after opening a tab, so you can jump between tasks; Esc quits.

Project names are shown relative to home (`daily/digest`). If a host has `~/PROJECTS.md`,
each `## Tag` section's table rows (first cell `` `rel/path/` ``) tag those projects, and
the list colors names by tag in muted tones, with a legend in the header. With a registry,
only registered projects and those with your own sessions are listed. A 🚩 before the path
(`| 🚩 `quant-poly/` | … |`) flags a project: flagged ones come first within their host. Headless `claude -p`
runs (cron jobs) and sessions outside home are never listed.

The preview shows where you left off in the latest session (your last prompts and
Claude's last reply).

Remote projects open inside tmux (`proj-<name>-<hash>` per project, one more per resumed
session), so claude keeps running when the connection drops; opening again re-attaches.
`▶n` marks projects with live proj tmux sessions. Detach with `Ctrl-b d`; set
`"tmux": false` on a host to use a plain shell. Hosts without tmux fall back automatically.
Local projects open in tmux too when there is no iTerm2 (e.g. proj run over ssh on a Linux
box); inside tmux proj switches to the project's session instead of nesting.

On a local Linux host with `"mux": "zellij"`, Enter behaves by situation:

- proj runs inside zellij: if a live session works in the project, proj only tells you its
  name (it cannot switch you; Ctrl-o w); otherwise it opens a new tab in the current session;
- not inside zellij, and a live session already works in the project (found by the working
  directories of its processes; names can be anything): Enter joins it;
- not inside zellij, no live session: Enter creates a session named `proj-<project>`
  (the project's path relative to home with `/` as `-`, e.g. `dev/process-core` becomes
  `proj-dev-process-core`; a 4-character hash is appended when the name had to be
  sanitized or shortened). An exited `proj-` session of the same name is deleted first;
  a live one is joined;
- zellij unavailable: `claude --resume` runs in place.

Sessions you named yourself are never deleted or resurrected; exited ones are only listed
in the board and in `proj report`. `"mux"` is `"zellij"`, `"tmux"` (default) or `false`; the
older `"tmux"` key still works. Elsewhere Enter also runs `claude --resume` (in the iTerm2 tab, or after
re-attaching a live proj tmux session on SSH hosts).

`proj report` ends with a summary of your own usage (opens, resumes, cleans) from the
local log `~/.cache/proj/usage.jsonl`; nothing leaves the machine.

`proj clean` works on repository roots only (not subdirectories or plain folders).
On macOS, iTerm2 asks for Automation permission the first time proj opens a tab.

Tip: set `"cleanupPeriodDays": 365` in Claude Code settings, otherwise transcripts older
than 30 days are deleted and can't be listed.

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
