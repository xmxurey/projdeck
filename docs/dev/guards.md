# 守卫清单（现行态）

建于 2026-11-21 第一轮 /retro 的守卫盘点。「档位」：L4 = 结构上让错误无法发生（统一出口、隔离入口），L3 = 能自动检查出来（测试）。
「响得了测试」指喂坏数据它会失败的那条用例。只在本机有 `.git` 时才跑、或者要开关才跑的，在「运行条件」里写明：远端全量（无 `.git`、不能传环境变量）对它们零覆盖。

| # | 名称 | 档位 | 位置 | 阈值与触发行为 | 响得了测试 | 运行条件 |
|---|---|---|---|---|---|---|
| G01 | 测试进程隔离 | L4 | `tests/conftest.py` 的 autouse 夹具；替身清单在 `tests/_stubs.py` | 每条测试的 HOME、PROJ_CACHE、PROJ_CONFIG 都指向 tmp；`REAL_PROGRAM_STUBS` 里的程序排在 PATH 最前面，调用会被记录 | `tests/test_isolation_spec.py` | 默认 |
| G02 | 探针隔离入口 | L4 | `scripts/sandbox.py` | pytest 之外的命令：临时 HOME、cache、config，加上替身，保留 PYTHONUSERBASE，去掉 PROJ_LIVE_TESTS | `tests/test_sandbox.py`（2026-11-21 有 4 处忠实变异，都会被抓住） | 默认 |
| G03 | 新鲜度单一判定 | L4 | `projdeck/board.py` 的 `host_freshness()` | 本机 60 s 标黄、120 s 过期；远端 300 s / 600 s（R-002） | `tests/test_host_freshness_spec.py`、`tests/test_freshness_outlets_spec.py` | 默认 |
| G04 | git 读取失败标记 | L3 | `projdeck/agent.py` 的 git 信息采集 | 超时或失败时标 `git?`，不标 `non-git`，项目不会从 `proj report` 里消失 | `tests/test_git_failed_spec.py` | 默认 |
| G05 | 配置严格校验 | L3 | `projdeck/config.py` 的 `load_config()` | 类型不对就以退出码 2 退出，报错点名到主机序号和键名 | `tests/test_config_validation_spec.py` | 默认 |
| G06 | 非 UTF-8 字段编码 | L4 | `projdeck/ui.py` 的 `to_field`/`from_field`；`projdeck/termtext.py` 的 `no_surrogates`、`has_surrogates` | 不保留原字节的字段编码成 `hex:`；给人看的输出先去掉代理字符 | `tests/test_fields_spec.py`、`tests/test_sanitize_outputs.py` | 默认 |
| G07 | 会话名防撞 | L4 | `projdeck/opener.py` 的 `proj_session_name()` | 三种有损情况都加 sha1 前 4 位（R-003） | `tests/test_session_names_spec.py` | 默认 |
| G08 | 子代理时间段 | L3 | `projdeck/agent.py` 的 `subagent_spans` | 子代理时间段的终点取最后一次写入（mtime） | `tests/test_subagent_spans_spec.py` | 默认 |
| G09 | 变异元测试 | L3 | `tests/_mutation.py`，以及 6 个 `*_spec.py` 元测试 | 在树的拷贝上注入一处错误，要求点名的那条用例失败 | 它们本身就是「响得了」的证明；2026-11-21 本机跑过一遍：50 条通过 | 只在 `PROJ_MUTATION_TESTS=1` 时运行，远端跑不了 |
| G10 | serve 慢连接与连接数 | L3 | `projdeck/serve.py` 的 `_DeadlineReader`、`REQUEST_TIMEOUT`、`MAX_CONNECTIONS` | 整个读请求阶段共用 30 s 时限，最多 32 个连接 | `tests/test_serve_deadline_spec.py` | 默认 |
| G11 | 私有文件权限 | L4 | `projdeck/fsutil.py` 的 `write_atomic`、`append_line`、`makedirs_private` | 文件 0600、目录 0700；已存在的 0644 文件写入后会被收紧 | `tests/test_fsutil_spec.py`、`tests/test_outlets.py::test_log_event_tightens_an_existing_0664_file` | 默认 |
| G12 | 仓库隐私 | L3 + L4 | `tests/test_repo_privacy.py`；本机的 pre-push hook（不进 git） | 进 git 的文件里不得出现真实地址、tailnet 名、个人路径（R-001） | `tests/test_repo_privacy.py` 自带反例用例 | 只在本机有 `.git` 时运行；远端全量会把整个模块跳过 |
| G13 | 测试卡死自报 | L3 | `tests/conftest.py` 的 `hang_watchdog` | 单条测试超过 `PROJ_TEST_TIMEOUT` 秒（默认 300）就打印所有线程的调用栈，并以退出码 1 结束整个运行 | `tests/test_hang_watchdog.py`（2026-11-21 变异「不设定时器」后，用例失败） | 默认 |
| G14 | 一处定义 | L3 | `tests/test_one_definition.py` | 同一概念一套实现：置顶标记、active 档、zellij 范围（`config.zellij_in_scope`）、会话标题，各出口必须调用同一个函数，结果一致 | `tests/test_one_definition.py` | 默认；依赖理顺的守卫目前用 hasattr 只查 4 个模块，改成 AST 全仓扫描的方案在 backlog 里 |
| G15 | 配色校验 | L3 | `tests/test_palette.py` | 网页浅色值与终端暗色值对外部配色校验脚本的阈值：色度下限 0.1、正常视力 ΔE 至少 15、色盲模拟 ΔE 至少 6、对比度至少 3.0；热力格逐档单调、与 0 次提交的灰色可分；网页须有斜线纹理和卡住形状，HTML 不得内联 `fill="#`。阈值与锚点数字抄自外部校验脚本的输出，脚本本身不在仓库里 | 同一文件里喂旧配色、旧热力格、非单调色阶、过近色、缺形状或内联 fill 的用例，各自必须报错（`test_old_terminal_timeline_fails_band_chroma_and_normal`、`test_original_heat_ramp_fails_light_end`、`test_encoding_fails_without_stuck_shape_or_hatch_or_with_inline_fill` 等） | 默认 |
| G16 | 折叠判定单一出口 | L3 | `tests/test_one_definition.py`（G16 一节）、`tests/test_brief_chain.py` | 精简展示（R-007）的折叠判定只有 `board.folded`，只看 `board["brief"]["keys"]`。测三个渲染出口：网页卡片、终端项目行、终端预览，要求它们折叠的恰是 keys 里的会话、摘要文字一致。泳道分流不在这三处，而在 `board.build_lanes` 构建时经 `board.folded` 完成，由 `tests/test_brief_fold.py` 钉住。「画不画摘要」与「摘要的数据时间」也各只有一个判定：`board.brief_shown`、`board.brief_host_age`（找不到主机就抛 LookupError），网页与终端都调它们。链路测试从 agent 的 `state_error` 一路测到折叠结果 | `test_g16_every_outlet_asks_board_folded`（把 `board.folded` 换成永远 False，三个出口的结果必须变，原断言必须失败）；`tests/test_brief_chain.py` 的三条用例；`test_web_and_terminal_draw_the_summaries_board_brief_shown_allows`、`test_web_terminal_and_preview_show_the_age_board_brief_host_age_gives`（替换这两个函数，两边一起变）；`test_g16_import_check_flags_names_imported_from_board`（喂按名导入的源码必须报出） | 默认；用到这些函数的模块必须经 `board.` 调用（写作 `board.folded(...)`），不得 `from projdeck.board import …` 直接导入，否则替换型测试抓不到；`test_g16_outlets_ask_the_board_module_not_imported_names` 查 `web.py`、`ui.py`、`cli.py` |
