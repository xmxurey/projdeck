# projdeck —— 跨主机的 Claude Code 项目看板（终端 `proj` + 只读网页 `proj serve`）

知识库地图见 docs/README.md：缺陷模式 docs/dev/bugbook/ · 口径裁定 docs/rulings.md ·
守卫清单 docs/dev/guards.md · 运行手册 docs/ops/runbook.md · 待办 docs/backlog.md。
每轮开发完成后、合并前跑 /retro。

## 铁律（封顶 10 条，一进一出；每条须对应本项目实际发生过的失败，增补走 /retro 呈批）

<!-- 通用条款（来自 process-core 模板，scaffold 起点；此后本项目自治）： -->
1. 同一概念只准一套实现；测试必须调用生产同一函数；校验两侧不得同源——恒等式不是校验。
2. 新守卫/告警/校验必须同一提交交付「喂坏数据它真的响」的测试；响不了的守卫等于没有。
3. 一切失败必须留痕（结构化日志）；禁止静默 return、`|| true`、吞异常。
4. 持久化写必须原子；重复摄入必须幂等——读侧去重与写侧 upsert 的键口径必须一致。
5. 算了就要用、写了就要读：新字段必须有消费者，新台账必须有 reader。

<!-- 本地条款（从 2026-11-21 第一轮 /retro 开始生长；增补须用户裁定）： -->
6. pytest 之外跑任何会读配置、缓存或调用外部程序的命令（探针、调试脚本、`python3 -m projdeck …`），一律经 `scripts/sandbox.py`；真实配置只做只读校验，不打印内容。（用户 2026-11-21 裁定；来源 docs/dev/bugbook/03-real-env-in-tests.md）
