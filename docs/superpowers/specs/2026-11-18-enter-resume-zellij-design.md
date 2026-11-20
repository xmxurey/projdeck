# Enter 打开会话选择器，并连接已经在跑的 zellij 会话

> 状态：已实现 / 已被取代；与代码不一致处以 README 和代码为准。

## 待你确认

无。

已裁定（用户 2026-11-18）：
- Enter 在项目目录里直接运行 `claude --resume`；
- 没有活会话时开普通 shell，~~不新建 zellij 会话~~；
- 只连接活会话，已退出的会话既不新建也不复活；
- 每台主机在配置里指定用哪种复用器，s-host 用 zellij，em-host 用 tmux。

后续 6 项也已裁定（用户 2026-11-18），都按建议执行，结论已写进正文：
- proj 在 zellij 里、项目已有活会话时，只提示（§3）；
- proj 在 zellij 里、没有活会话时，新开标签页（§3）；
- 多个活会话时，弹选择框（§3、§4）；
- 空会话也算活会话（§5）；
- ^R / ^S 不改（§6）；
- 遗留的 tmux 会话由你自己清理（§7）。

## 1. 目标与依据

`~/.cache/proj/usage.jsonl` 记录了 2026-11-10 至 11-18 的 63 次操作：打开 62 次，恢复只有 1 次。
11-18 晚上 s-host 上跑着的 4 个 claude 全是 `claude --resume`，都是打开项目之后在 shell 里手动输入的；其中 3 个在 zellij 会话里。

也就是说，实际用法是：Enter → 在 shell 里输入 `claude --resume` → 在 Claude 自带的选择器里挑会话。proj 的 ^R / ^S 没有被用上，而且 proj 看不到你正在用的 zellij 会话。

这一版要做到：

- Enter 省掉手动输入 `claude --resume` 这一步；
- 项目已经有活会话时，先连那个会话，不再起第二个 claude；
- 列表里能看到 zellij 会话。

## 2. 配置

主机配置新增 `"mux"`，取值 `"zellij"`、`"tmux"` 或 `false`。

- 不写时沿用现在的 `"tmux"` 键：`"tmux": false` 等于 `"mux": false`，其余情况等于 `"mux": "tmux"`。旧配置不用改。
- 两个键都写时，以 `"mux"` 为准。
- `"tmux": "always"`（找不到就新建 tmux 会话）只在 `mux` 是 tmux 时生效，行为不变。
- 本机配置改成：s-host 写 `"mux": "zellij"`，em-host 不改（默认 tmux）。

zellij 只支持 Linux 主机，因为找会话要读 `/proc`（§4）。在 macOS 上配置 `"mux": "zellij"` 时，proj 在列表顶部报一次配置错误，并当作 `false` 处理。

## 3. Enter 的行为

`claude --resume` 在项目目录里运行，退出后留在登录 shell 里（和现在 `resume_inner` 外面那层一样：`"$SHELL" -lic 'claude --resume'; exec "$SHELL" -l`）。

| # | proj 在哪里运行 | 项目有没有活会话 | 做什么 |
|---|---|---|---|
| 1 | 不在 zellij 里（例如直接 ssh 进 s-host） | 有 | `exec zellij attach <会话名>`，proj 退出 |
| 2 | 在 zellij 里 | 有 | 不打开任何东西，列表顶部提示 `s-host:invest-research is already open in zellij session research: press Ctrl-o w to switch`（界面文字沿用英文），proj 留着（用户 2026-11-18 裁定：zellij 0.44 没有切换会话的命令，嵌套一层 zellij 快捷键会被外层截走） |
| 3 | 不在 zellij 里 | 没有 | ~~就地运行 `claude --resume`，proj 退出~~ **被用户 2026-11-19 裁定 D14、D15 取代**：本机 `mux` 为 zellij 时，新建一个名为 `proj-<项目名>` 的 zellij 会话，在里面运行 `claude --resume`。同名的 `proj-` 会话已经退出时，先 `zellij delete-session` 再建；你自己起名的会话一律不删除、不复活。zellij 不可用时，仍按原来的做法就地运行。详见 README 的 zellij 一段 |
| 4 | 在 zellij 里 | 没有 | `zellij action new-tab --cwd <目录> -- "$SHELL" -lic …` 在当前会话新开标签页，proj 留着，和 iTerm 开新标签一样（用户 2026-11-18 裁定；开标签页不算新建会话） |

- 有没有活会话，打开前在本机重新查一遍（§4，只要几毫秒），不用同步缓存里的结果，因为缓存最多可能是 30 分钟前的。
- 第 1 行连接前再确认一次会话还活着。`zellij attach` 碰到已退出的会话会把它复活，这正是已裁定不要的行为。确认方法是 `zellij list-sessions -n` 里这一行不带 `EXITED`。
- **远端 tmux 主机（em-host）**：沿用现在的脚本，有活的 `proj-*` tmux 会话就连进去，没有就开 shell；不同的是这个 shell 会先运行 `claude --resume`。
- **Mac 本机有 iTerm2 时**：新开 iTerm2 标签页，在里面运行 `claude --resume`，其余不变。
- 一个项目有多个活会话时，先弹一个小 fzf 选择框，列出会话名和各自的 claude 个数，选中的那个再按上表处理（用户 2026-11-18 裁定）。
- 使用日志照旧记 `open`；连进活会话时多记一个字段 `"attach": "<会话名>"`，以后看报告能分清打开了多少次、连接了多少次。

## 4. 找到属于某个项目的 zellij 会话

zellij 的会话名是你自己起的（例如 `research` 对应 invest-research），所以按名字匹配不可靠，要看会话里各个进程的工作目录。

1. 读 `/proc/*/cmdline`，找出 `zellij --server <套接字目录>/<会话名>` 这样的进程。每个活会话对应一个这样的服务进程，会话名就是路径的最后一段。
2. 读 `/proc/*/stat` 拿到父进程关系，收集每个服务进程下面的全部子孙进程（窗格里的 shell、claude 等），读它们的 `/proc/<pid>/cwd`。
3. 每个工作目录用现有的 `owner()` 归到项目，嵌套项目按最长前缀匹配，和会话记录的归属规则一样。一个 zellij 会话的窗格分在几个项目里时，就同时算作这几个项目的活会话。
4. 同时数一下子孙进程里有几个 `claude`（`/proc/<pid>/comm`）。

结果是每个项目一个列表 `live_zellij: [{"name": "research", "claude": 2}]`。

- 读不了的进程（属于别的用户、扫描期间退出）直接跳过，不报错。
- 已退出的会话没有服务进程，自然不会被列出来，不需要单独排除。
- 11-18 在 s-host 上实测：`research` → invest-research（2 个 claude）、`poly-quant` → quant-poly（1 个）、`multiagent` → dev/process-core（1 个），和 `zellij action dump-layout` 给出的结果一致。

**在哪里查**

- 同步时由 agent 查一遍，写进缓存，给列表的 ▶ 标记用。这和现在收集 `live_tmux` 是同一个位置，tmux 那部分原样保留。
- 按 Enter 时，本机主机在 proj 进程里直接再查一遍。
- 远端主机这一版不支持 zellij：em-host 没装 zellij，没有需求。~~远端主机配 `"mux": "zellij"` 时按配置错误处理。~~

不升级 `SCHEMA`，`live_zellij` 是新增的可选字段，旧缓存里没有时按空列表处理。

## 5. 列表与预览

- ▶ 标记合并统计 zellij 和 tmux 会话：只有一个 zellij 会话时写会话名 `▶research`，其余情况（多个会话，或只有 tmux 会话）写个数，如 `▶2`；tmux 会话名是 `proj-…-哈希`，写出来没有意义。只剩 shell、没有 claude 的会话也算活会话，照样连进去，标记写成 `▶research·0`，一眼能看出是空的（用户 2026-11-18 裁定）。
- 预览里加一行 `▶ zellij: research (2 claude)`，和现有的 `▶ live tmux: …` 那行并列。
- 页头的按键说明 `Enter open` 改成 `Enter resume`，表示 Enter 会弹出会话选择器。

## 6. 不改的部分

- ^R（恢复最近的会话）、~~^S~~（挑一个会话恢复）、^F（搜索提示）、`proj sessions`、`proj grep`：行为不变（用户 2026-11-18 裁定）。proj 判断不出活会话里跑的是哪个 Claude 会话：用选择器启动的 claude，命令行里没有会话 id。所以这一版只在预览里提示「这个项目有活会话 research」，提醒你别把同一个会话再恢复一次。
- ~~不新建 zellij 会话~~，不复活已退出的会话，不改 zellij 的配置。
- 远端主机不支持 zellij（§4）。

## 7. 遗留 tmux 会话

s-host 上留下 3 个只剩空 bash 的 tmux 会话：`proj-fin-dc35`、`proj-dev-9179`、`3`。由你自己运行一次 `tmux kill-server` 清掉（用户 2026-11-18 裁定）。代码不做处理：s-host 的 `mux` 改成 zellij 之后，proj 不再看 s-host 上的 tmux。

## 8. 测试

- **找 zellij 会话**：`/proc` 读取封装成可替换的函数，测试里喂假的进程表，覆盖以下情况：
  - 两个会话；
  - 一个会话的窗格跨两个项目；
  - 嵌套项目；
  - 读不了的进程；
  - 进程表里没有服务进程（全部已退出）。

  另外加一条在真机上跑的用例：本机有 zellij 服务进程时，结果要和 `zellij action dump-layout` 解析出的工作目录一致；没有时跳过。
- **打开时的命令**：§3 表格的 4 行，再加 em-host 的 tmux 有会话、无会话两种，各断言生成的命令；确认第 1 行会先检查会话还活着，碰到已退出的会话会走第 3 行。
- **配置**：`mux` 和旧 `tmux` 键的组合；macOS 上配 zellij、远端主机配 zellij 时都报配置错误。
- **界面**：▶ 标记、预览行、页头按键说明。
- ~~现有 144 项测试~~全部保持通过。
