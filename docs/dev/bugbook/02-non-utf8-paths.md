# 模式 02：非 UTF-8 路径跨边界

## 模式卡
- **所属通用类型**：T12 escaping-injection（主）、T01 silent-failure
- **一句话定义**：文件名不一定是合法的 UTF-8。Python 用 surrogateescape 把这类字节读成代理字符，字符串一旦跨过只接受合法 UTF-8 的边界，要么抛 UnicodeEncodeError，要么被换成 U+FFFD，再也对不回原路径。这类边界包括：严格 utf-8 编码、`json.dumps(ensure_ascii=False)` 后写文件、fzf 的 `{n}` 占位符、哈希、HTTP 响应。
- **本仓计数**：3（截至 2026-11-21）
- **为什么在这个仓库反复发生**：项目路径来自扫描磁盘，不受控制，而同一个 path 字符串会流向很多出口：fzf 隐藏字段、preview 子进程、usage.jsonl、tmux 和 zellij 会话名、网页、JSON API。新出口默认写 `.encode("utf-8")`，开发机上又几乎没有非 UTF-8 的文件名。
- **写代码时如何预防**：
  - path 进 fzf 隐藏字段用 `ui.to_field`，读回用 `ui.from_field`；
  - 求哈希用 `encode("utf-8", "surrogateescape")`；
  - 写 JSON 行用 `ensure_ascii=True`；
  - 给人看的输出过 `termtext.no_surrogates`。
- **评审时怎么查**：
  - grep diff 里新增的 `.encode("utf-8")`、`.encode()`、`ensure_ascii=False`、`errors="strict"`；
  - grep 携带 path 的 fzf 字段和 `{1}`、`{2}` 占位符；
  - 每个命中都问一句：path 带 `\udcff` 时会发生什么。
- **现有守卫**：guards.md G06。
- **相关铁律**：无。
- **相邻模式**：01。

## 案例

### 案例 02-1：一个非 UTF-8 目录名让 `proj` 每次启动都崩溃，网页项目页 500
- **commit**：`d98c61f`
- **日期**：2026-11-19 发现
- **症状**：项目根下只要有一个非 UTF-8 的目录名，`proj` 每次启动都崩溃，网页的项目页返回 500。
- **根因**：路径字符串直接以严格 utf-8 编码输出。设计阶段挑战者又找出 3 个同族出口：
  - `cli.log_event` 用 `ensure_ascii=False` 写 usage.jsonl，遇到代理字符会以退出码 2 退出；
  - fzf 的 `{n}` 占位符把原始字节换成 U+FFFD，preview 显示 `(not in cache)`；
  - `opener.tmux_name` 用严格 utf-8 求哈希。
- **量化影响**：只要存在一个这样的目录，`proj` 就完全不可用。
- **为什么测试没拦住**：fixture 不含真实形态，所有夹具路径都是 ASCII。另外用来测试的假 fzf 原样回显，测不出真 fzf 的替换行为。
- **钉住它的测试**：`tests/test_fields_spec.py`，以及 F6 补的 `from_field` 喂坏用例。
- **同根因第几次**：第 1 次
- **状态**：已修。往返定义域是「`os.fsdecode` 能产出的字符串」。

### 案例 02-2：待办文件里有非 UTF-8 字节，整份待办被丢掉
- **commit**：`d98c61f`
- **日期**：2026-11-19 发现
- **症状**：`docs/backlog.md` 里只要有一个坏字节，这个项目的待办整份消失。
- **根因**：严格解码失败后，走了「没有待办」的分支（同时也属于模式 01）。
- **量化影响**：不可量化。
- **为什么测试没拦住**：新代码零测试（失败路径）。
- **钉住它的测试**：`tests/test_failure_coverage_spec.py`
- **同根因第几次**：第 2 次
- **状态**：已修。`read_untrusted` 改用 `errors="replace"` 解码。

### 案例 02-3：远端 C locale 下，preview 把中文文件名显示成 `?`
- **commit**：无
- **日期**：2026-11-20，T17 评审 M-4
- **症状**：远端 preview 用 `ls -Aq` 列目录。远端 locale 为 C 时，中文文件名显示成 `?`。
- **根因**：`ls -q` 按 locale 判断可打印字符。
- **量化影响**：只影响显示，不影响打开。
- **为什么测试没拦住**：fixture 不含真实形态。
- **钉住它的测试**：无，属于欠账，见 docs/backlog.md。
- **同根因第几次**：第 3 次
- **状态**：待修。备选方案是改由 agent 列目录。
