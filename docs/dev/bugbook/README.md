# 缺陷模式索引（bugbook）

一个 bug 只归一个根因模式，症状相关的在案例末尾互链。通用类型 T## 见 process-core 的 defect-types.md。
建库于 2026-11-21 第一轮 /retro；案例来自 2026-11-19 的全面 review 与 run 2026-11-20-review-fixes，修复都在 d98c61f。

| # | 模式 | 主类型 | 计数 | 现有守卫 |
|---|---|---|---|---|
| 01 | [失败被显示成正常结果](01-failure-as-normal.md) | T01 / T04 | 4 | `host_freshness`、`git_failed` 标记；见 guards.md G03、G04 |
| 02 | [非 UTF-8 路径跨边界](02-non-utf8-paths.md) | T12 | 3 | `ui.to_field`/`from_field`、`termtext.no_surrogates`；G06 |
| 03 | [测试或探针碰真实环境](03-real-env-in-tests.md) | T14 | 5 | conftest 隔离夹具、`scripts/sandbox.py`；G01、G02 |
| 04 | [守卫测不出它要防的错](04-guard-cannot-fail.md) | T02 | 5 | 变异元测试（`PROJ_MUTATION_TESTS=1`）；G09 |
| 05 | [有损键导致撞名](05-lossy-keys.md) | T10 | 1 | `opener.proj_session_name`；G07 |
| 06 | [同一指标两种口径](06-metric-scope.md) | T05 | 1 | `subagent_spans`；G08 |
| 07 | [外部输入不校验类型](07-unchecked-input.md) | T06 / T11 | 2 | `config.load_config` 的严格校验；G05 |

## 复盘触发标准（覆盖默认）

- 默认：错误进入已交付产物、不可再生数据丢失、同根因第二次复发，三者满足其一就写完整复盘。
- 本库的「复发」：这个模式已经被命名之后（写进本 bugbook、写进某轮设计或修复已合入），同根因再出现一次。建库这一轮里，一次 review 批量发现的同类问题只算一次发现。
- 建库这一轮触发完整复盘的有 03、04、07：它们都是在本 run 已经点名、开始修之后，同一轮里又冒出新案例。三者共用一份完整复盘，见 [docs/dev/reviews/2026-11-21-review-fixes.md](../reviews/2026-11-21-review-fixes.md) 的「完整复盘」一节。

## 评审时的通用检法（本仓）

- 修复类改动先做同族横扫：grep 同一概念的姊妹函数、另一半路径、对称面，把横扫的命中写进设计或提交说明。本仓 03、04、07 三个模式都是「修了点名的那一处，漏了同族」。
