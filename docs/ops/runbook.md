# 运行手册

安装步骤见 README 的「proj serve」一节。这里只记跑起来以后的事：怎么确认它活着、坏了去哪看、改代码后怎么安全地重启。

## proj-serve

- 用户级 systemd 单元 `proj-serve.service`，`WorkingDirectory=%h/dev/projdeck`，所以 main 上的代码一合入，下次重启就生效。
- 默认监听本节点的 Tailscale IPv4（`serve.bind` 为 `null`），端口 8787。
  **本机 127.0.0.1 上同一个端口可能是别的应用**，探测 127.0.0.1:8787 得到 404，不代表 proj-serve 坏了。2026-11-21 就因此误判过一次。探测要用它实际绑定的地址：
  ```bash
  ss -ltnp | grep 8787          # 看哪个进程绑在哪个地址
  curl -s -o /dev/null -w '%{http_code}\n' http://<绑定地址>:8787/api/state.json
  ```
- 日志：`journalctl --user -u proj-serve -n 100`。正常运行时不应出现 Traceback；SIGTERM 时的 EBADF 已经静默，只在 SIGTERM 之后。
- 状态：`systemctl --user status proj-serve`，再看 `NRestarts`（`systemctl --user show -p NRestarts proj-serve`）。

## 改代码后重启（顺序不能换）

1. **先用新代码只读校验真实配置**。配置校验变严之后，旧配置可能被拒绝，服务会以退出码 2 退出，终端的 proj 也打不开：
   ```bash
   python3 -c 'from projdeck import config; c = config.load_config(); print("ok", len(c["hosts"]), "hosts")'
   ```
   只打印主机数量；失败时只看异常类型和报错里的键路径（报错只点名 `hosts[i].key`，不带值）。**不要打印配置内容**（R-001）。
2. `systemctl --user restart proj-serve`
3. 依次探测 `/`、`/projects`、`/fragment/home`、`/api/state.json`，都应返回 200；再看 journal 里没有 Traceback。

## 测试在哪里跑

- 全量可以放到远端（em-host）跑，省本机内存。远端没有 `.git`，也传不了环境变量，所以下面两类测试必须在本机跑：
  - `tests/test_repo_privacy.py`（G12）：远端会把整个模块跳过；
  - 变异元测试（G09）：要 `PROJ_MUTATION_TESTS=1`。
- 本机内存紧：只跑相关的测试文件，全量在提交前用 `nice` 跑一次，不要同时跑多个全量。
- 单条测试超过 `PROJ_TEST_TIMEOUT`（默认 300 秒）会自报调用栈并结束运行（G13），不会再无声地挂几个小时。
- pytest 之外的探针一律用 `python3 scripts/sandbox.py -- <命令>`（G02）。

## 数据可再生性

- `~/.cache/proj/` 下的缓存都可以再生：删掉后跑 `proj sync` 即可。
- `usage.jsonl` 不可再生，它是 `proj report` 的唯一来源：不要删，不要让测试或探针写它。
- `~/.config/proj/config.json` 是真实主机配置的唯一来源，不进 git（R-001）。
