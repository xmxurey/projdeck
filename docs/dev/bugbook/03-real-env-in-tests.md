# 模式 03：测试或探针碰真实环境

## 模式卡
- **所属通用类型**：T14 synthetic-as-real（主，方向相反：不是假数据进了活数据，而是测试动作落到了真环境上）、T02
- **一句话定义**：测试、评审或调试时跑的命令，读了真实配置、写了真实缓存，或者调用了真实的 zellij、tmux、ssh、systemctl、claude。
- **本仓计数**：5（截至 2026-11-21），其中 3 次发生在本模式已被点名之后，触发了完整复盘。
- **为什么在这个仓库反复发生**：
  - proj 的默认行为就是读 `~/.config/proj/config.json`、写 `~/.cache/proj`、调外部程序；
  - pytest 里有 conftest 隔离，但 pytest 之外跑的探针，例如 `python3 -c`、scratch 目录里的脚本、`python3 -m projdeck report`，过去没有任何隔离；
  - 手动改 HOME 又会让装在用户目录的 pytest 消失，结果有人宁可不改。
- **写代码时如何预防**：
  - pytest 之外的命令，一律经 `scripts/sandbox.py` 运行；
  - 新增外部程序依赖时，同时在 `tests/_stubs.py` 的 `REAL_PROGRAM_STUBS` 里加替身；
  - 真机用例（`PROJ_LIVE_TESTS=1`）只由用户或经用户同意后运行。
- **评审时怎么查**：
  - diff 里新增 `subprocess` 或 `shutil.which` 调用的程序名，查它在不在 `REAL_PROGRAM_STUBS` 里；
  - 新增读写 `~` 下路径的代码，查它是否经过 `PROJ_CACHE`、`PROJ_CONFIG` 或 HOME；
  - 派发给子代理的探针命令，查它前面有没有 `scripts/sandbox.py`。
- **现有守卫**：guards.md G01（conftest 隔离）、G02（sandbox）。
- **相关铁律**：CLAUDE.md 第 6 条（用户 2026-11-21 裁定）。
- **相邻模式**：04。

## 案例

### 案例 03-1：测试真的调用了 tmux、zellij、systemctl
- **commit**：`d98c61f`
- **日期**：2026-11-19 发现
- **症状**：
  - 测试真的调用 tmux，共 25 次；
  - 有一条测试会对正在使用的 zellij 会话执行 `dump-layout`；
  - 会真的调用 `systemctl`。
- **根因**：conftest 只替换了部分程序，没有一份统一的清单。
- **量化影响**：只读调用，未发现副作用；但每次全量测试都会碰到用户正在用的会话。
- **为什么测试没拦住**：没有「零真实调用」的守卫。
- **钉住它的测试**：`tests/test_isolation_spec.py`
- **同根因第几次**：第 1 次
- **状态**：已修

### 案例 03-2：架构师的探针往真实 usage.jsonl 追加了一行
- **commit**：无（流程事件）
- **日期**：2026-11-19
- **症状**：设计阶段的只读探针调用了 `cli.log_event`，往真实的 `~/.cache/proj/usage.jsonl` 追加了一行，事后已删除。
- **根因**：探针在 pytest 之外运行，没有设置 `PROJ_CACHE`。
- **量化影响**：1 行，已还原。
- **为什么测试没拦住**：守卫只在 pytest 里，不在探针的路径上。
- **钉住它的测试**：`tests/test_sandbox.py`（2026-11-21 起探针有了隔离入口）
- **同根因第几次**：第 2 次（复发：此时 P1-10 已经点名）
- **状态**：已修（L4 入口）

### 案例 03-3：实现者打开 PROJ_LIVE_TESTS=1，调用了真实 zellij
- **commit**：无（流程事件）
- **日期**：2026-11-19（T01）
- **症状**：实现者为了验证一个问题，打开 `PROJ_LIVE_TESTS=1`，调用了真实 zellij 的 `dump-layout` 和 `systemd-analyze`。
- **根因**：真机开关对任何人都开放，派发词里没有禁止。
- **量化影响**：只读调用，无影响。
- **为什么测试没拦住**：开关本身就是绕过守卫的设计。
- **钉住它的测试**：`tests/test_sandbox.py::test_probe_sees_only_the_sandbox`（sandbox 会删掉 PROJ_LIVE_TESTS）
- **同根因第几次**：第 3 次
- **状态**：已修（入口）＋流程规则：真机用例只在终验时、经用户同意后运行。

### 案例 03-4：调试脚本没有设置 PROJ_CONFIG
- **commit**：无（流程事件）
- **日期**：2026-11-21（终审修复 F3）
- **症状**：实现者在 scratch 目录里调试 `ui.lines_of` 的脚本没有设置 `PROJ_CONFIG`，可能只读访问过真实配置文件。输出里只有夹具值。
- **根因**：同 03-2。
- **量化影响**：没有写入；是否读过真实配置无法排除。
- **为什么测试没拦住**：同 03-2。
- **钉住它的测试**：`tests/test_sandbox.py`
- **同根因第几次**：第 4 次
- **状态**：已修（入口）

### 案例 03-5：子代理改了 HOME，误以为本机没有 pytest
- **commit**：无（流程事件）
- **日期**：2026-11-20（T19）
- **症状**：子代理为了隔离，把 HOME 指向临时目录，之后找不到装在用户目录的 pytest，就报告「本机没有 pytest」，并据此跳过了验证。
- **根因**：Python 的用户 site 目录跟着 HOME 走。
- **量化影响**：一次验证被误跳过，事后补跑。
- **为什么测试没拦住**：隔离做对了，副作用没人处理。2026-11-21 /retro 写 `tests/test_hang_watchdog.py` 时，又在自己身上重现了一次。
- **钉住它的测试**：`tests/test_sandbox.py::test_user_site_survives_the_moved_home`
- **同根因第几次**：第 5 次
- **状态**：已修。sandbox 保留 PYTHONUSERBASE；`tests/_mutation.py` 和 `tests/test_hang_watchdog.py` 的子进程也都设置了它。
