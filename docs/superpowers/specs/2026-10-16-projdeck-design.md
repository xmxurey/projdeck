# projdeck（命令 `proj`）设计文档

日期：2026-10-16　状态：待评审

## 1. 背景与目标

同时在 Mac 和多台云机器上用 Claude Code 开发很多项目，遇到四个问题：

1. 重新打开时不记得上次的会话在哪台机器、哪个目录。
2. 不清楚每个项目进展到哪了。
3. 不知道哪些目录已经停滞或废弃。
4. 项目里不断积累 AI 生成的临时文件。

社区调研结论：单机的会话定位有成熟工具（Claude Code 自带的 `--resume` + Ctrl+A、claude-history），但没有工具能同时做到"跨机器汇总 + 项目状态 + 清理 + 一键进入远端项目"。所以自己开发。

**最终形态**是一个 Mac App（Tauri + 内嵌终端，v2）。**v1** 先做最快能用上的终端版本：数据层与界面解耦，v2 直接复用 v1 的采集层。

**v1 成功标准**

- 在 Mac 上一条命令看到所有主机的项目，按最后活动时间排序并分档。
- 两次按键内在 iTerm2 新标签页进入任意主机上的项目目录，或恢复其中某个 Claude 会话。
- 只记得会话主题时，能按标题跨主机搜到会话。
- 能安全地清理某个项目里的可疑临时文件，并且可以还原。
- 远端主机不需要安装任何东西。

## 2. 架构

```
collect.py（只读，只用标准库，输出 JSON）
   ├─ 本机：python3 collect.py
   └─ 远端：ssh <alias> python3 - < collect.py
          ↓
   proj sync：并行采集 → ~/.cache/proj/<host>.json
          ↓
   proj（fzf 界面）/ proj sessions / proj report / proj clean / proj restore
          ↓
   iTerm2 打开器（osascript 新建标签页并执行命令）
```

技术约束：

- Python ≥ 3.8，只用标准库。Mac 自带的 python3 是 3.9，Ubuntu VM 上是 3.12。
- 界面使用 fzf，Mac 上执行 `brew install fzf` 安装。
- 配置使用 JSON。3.8/3.9 没有 `tomllib`，避免为读 TOML 引入第三方依赖。

## 3. 组件

各模块位于 `projdeck/` 包内。每个模块只做一件事，可以单独测试。

| 模块 | 职责 | 依赖 |
|---|---|---|
| `collect.py` | 单文件、可独立运行的采集脚本。它会被原样通过 stdin 发送到远端执行，所以**不能 import 包内的其他模块**。 | 标准库、git |
| `config.py` | 读取 `~/.config/proj/config.json`，提供默认值 | — |
| `sync.py` | 并行在各主机运行 collect，带超时；写缓存；失败时保留旧缓存并标记 | collect.py、config |
| `model.py` | 读缓存，合并成统一的项目和会话列表；计算分档 | — |
| `ui.py` | 生成 fzf 行和预览文本，调用 fzf，处理快捷键 | fzf |
| `opener.py` | 拼出"进入目录 / 恢复会话"的 shell 命令，再通过 osascript 在 iTerm2 中打开。非 Mac 环境直接在当前终端执行 | osascript |
| `clean.py` | 列出候选文件；移入回收站；写 manifest；还原；清空 | git |
| `report.py` | 生成 Markdown 总览 | model |
| `cli.py` | 子命令分发（argparse） | 以上全部 |

清理和还原操作通过 SSH 在远端执行。实现方式：`clean.py` 中有一段同样只用标准库的独立脚本，和 collect 一样经 stdin 发送到远端执行。

## 4. 数据

### 4.1 配置 `~/.config/proj/config.json`

```json
{
  "hosts": [
    {"name": "mac", "local": true, "roots": ["~/dev", "~/code"]},
    {"name": "sg",  "ssh": "sg",   "roots": ["~"]}
  ],
  "tiers_days": {"active": 14, "stale": 90},
  "scan_depth": 2,
  "auto_sync_minutes": 30,
  "suspicious_patterns": ["test_*.py", "*_backup*", "*.bak", "*.log", "*SUMMARY*.md",
                          "*IMPLEMENTATION*.md", "*REPORT*.md", "crontab-backup*",
                          "tmp*", "scratch*", "*.orig", "*_v2.*", "*_old.*"]
}
```

- `ssh` 字段是 `~/.ssh/config` 中的主机别名，鉴权完全交给 OpenSSH 处理。
- `suspicious_patterns` 只匹配**文件名**。位于 `tests/` 目录下的 `test_*.py` 不算可疑。

### 4.2 采集输出（每台主机一份）

```json
{
  "schema": 1, "host": "sg", "collected_at": 1760600000,
  "projects": [{
    "path": "/home/ubuntu/quant", "exists": true,
    "git": {"branch": "main", "last_commit_ts": 0, "dirty": 2, "untracked": 0,
            "ahead": 0, "no_upstream": false},
    "last_mtime": 0,
    "suspicious": ["docs/migration/crontab-backup.txt"],
    "progress": "……",
    "sessions": [{"id": "uuid", "title": "……", "last_ts": 0, "n_msgs": 42}],
    "errors": []
  }]
}
```

**项目怎么发现。** 下面两类目录的并集：

1. 每个 root 下深度不超过 `scan_depth` 的目录中，含 `.git` 的目录。root 本身如果含 `.git` 也算。
2. `~/.claude/projects/*/*.jsonl` 中出现过的所有 `cwd`。如果某个 `cwd` 位于已发现仓库的子目录，就归到那个仓库下。目录已不存在的，`exists` 记为 false。

**会话怎么解析。**

- 逐行读 JSONL，取第一个出现的 `cwd` 字段，不反推编码后的目录名。
- 标题依次取：自定义名称（`/rename` 记录）→ 自动生成的摘要 → 第一条用户消息的前 80 个字符。
- 读不懂的行直接跳过。JSONL 是 Claude Code 的内部格式，随版本可能变化，解析逻辑要宽容。
- 大文件只读开头和结尾的若干 KB：标题信息在开头，最后时间在结尾。

**其他字段的取值。**

- `last_mtime`：工作区内最新的文件修改时间。跳过 `.git`、`node_modules`、`.venv`、`venv`、`target`、`dist`、`build`、`__pycache__`，最多遍历约 5000 个文件。
- `progress`：如果有 `PROGRESS.md`，取它前 10 行中的非空内容；否则取 `git log -3 --format=%s`。
- `git`：用 `git status --porcelain=v1 -b` 一次取完分支、ahead、dirty 和 untracked。每个命令超时 5 秒。

### 4.3 分档

- 最后活动时间 = max(最后提交时间, `last_mtime`, 最近会话时间)。
- 距今不到 14 天为 **活跃**，14 到 90 天为 **停滞**，超过 90 天为 **废弃**。
- 目录不存在的项目单独标为 **已删除**。

## 5. 交互

### 5.1 `proj`：项目总览

fzf 列表中每行显示：主机、分档图标、项目名、最后活动（如"3d"）、未提交数/未推送数、可疑文件数、会话数。默认按最后活动时间倒序排列，右侧是预览栏。

| 按键 | 动作 |
|---|---|
| Enter | 在 iTerm2 新标签页进入该项目目录。远端项目执行 `ssh -t <alias> 'cd <path> && exec $SHELL -l'` |
| Ctrl-R | 在新标签页恢复最近一次会话：`cd <path> && claude --resume <id>` |
| Ctrl-S | 进入该项目的会话列表，选中后恢复 |
| Ctrl-X | 对该项目打开清理助手 |
| Ctrl-U | 立即同步 |

如果缓存超过 `auto_sync_minutes`，打开界面时先使用旧缓存，同时在后台同步，下次打开时生效。

### 5.2 其他子命令

- `proj sessions`：所有主机的会话平铺显示，按标题模糊搜索。回车后恢复选中的会话。
- `proj sync [host…]`：同步全部主机或指定主机。
- `proj report`：输出 Markdown 到标准输出，内容包括废弃和已删除的项目、未推送的改动、可疑文件最多的项目、各主机数据的新旧程度。
- `proj clean <host>:<path>`：打开清理助手，与 Ctrl-X 相同。
- `proj restore [<host>]`：列出回收站中的批次，选中后还原。
- `proj trash --purge --older 30d [<host>]`：彻底删除超过指定时间的批次，执行前需要手动输入 `yes` 确认。

## 6. 清理助手

1. 在目标主机上执行 `git clean -nd`，得到"未跟踪且未被忽略"的文件和目录。**被 `.gitignore` 忽略的文件永远不在候选范围内。**
2. 在 fzf 中多选：匹配可疑规则的候选默认勾选，其余列出但不勾选。预览栏显示文件内容的前几行。
3. 用户确认后，在目标主机上执行以下步骤：
   - 再次确认每个文件仍然未被跟踪，已被跟踪的跳过。
   - 将文件移动到 `~/.proj-trash/<YYYYmmdd-HHMMSS>/<项目名>/<相对路径>`。
   - 写入 `manifest.json`，内容包括原路径、新路径和时间。
4. `restore` 按 manifest 把文件移回原处。如果原路径上已经有文件，跳过并报告。
5. 只有 `purge` 会真正删除文件。

不依赖 trash-cli，Mac 和 Linux 使用同一套机制。

## 7. 出错处理

- 单个仓库采集出错时，错误写进该仓库的 `errors` 字段，不影响其他仓库。
- 某台主机 SSH 失败或超时（默认 20 秒）时，保留旧缓存并标记 `stale_since`。界面上显示"⚠ 数据来自 Xh 前"。
- 缓存的 `schema` 版本不匹配时，提示重新同步。
- 清理操作在移动前重新检查文件状态；任何一步失败都不影响已经写入 manifest 的部分。
- 不在 Mac 上，或找不到 iTerm2 时，打开器退回为在当前终端执行命令。

## 8. 测试

使用 pytest，fixture 在临时目录中构造各种场景：

- 干净的仓库、有未提交改动的仓库、ahead 的仓库、没有 upstream 的仓库、非 git 目录。
- 一个假的 `~/.claude/projects`：包含正常、损坏、超大的 JSONL，以及 `cwd` 已不存在的会话。

覆盖范围：

- collect 的输出字段。
- 分档边界。
- 可疑规则：包括 `tests/` 下的例外。
- clean → restore 往返后文件与原来完全一致；purge 只删除过期的批次。
- 远端路径：`ssh localhost` 冒烟测试（环境可用时运行）。
- opener：只测试命令拼接，包括引号和空格转义。真实打开 iTerm2 标签页由用户在 Mac 上手动验证。

## 9. 不在 v1 范围内

- Mac App 和内嵌终端（v2：Tauri 加 xterm.js，复用 collect 和缓存格式）。
- 会话全文搜索、Codex 及其他 agent 的会话。
- 自动安装 Claude Code hooks，以及修改 CLAUDE.md 约定。

## 10. 相关但独立的改动

在 dotfiles 的 `base/claude-settings.json` 中设置 `"cleanupPeriodDays": 365`。否则超过 30 天的会话会被 Claude Code 删除，本工具也就无法找回。这项改动按 dotfiles 规则单独进行：先给用户看 diff，用户同意后再提交。
