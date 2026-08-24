# Changelog — MCP 会话池任务亲和性修复（anyio cancel scope 跨任务退出违约）

> 状态：**已上线**。修复 2026-08-24 生产事故：MCP 会话池在 lifespan 任务中打开会话、
> 在请求任务中关闭会话，违反 anyio cancel scope / task group 的任务亲和性约束，
> 导致关闭报错与 lifespan 被误杀。核心层 `app/core/mcp_client.py` 重写为
> **per-server session-worker 所有权模型**，公开 API 零变化。

## 1. 线上症状（2026-08-24）

1. `mcp_session_close_failed` + `RuntimeError: Attempted to exit cancel scope in a
   different task than it was entered in` —— TTL 回收 / 失效重建 / 关机时，请求任务
   调用会话 CM 的 `__aexit__`，而该 cancel scope 是 lifespan（预热）任务 enter 的；
2. 作用域取消误杀 lifespan：关闭投递的 `CancelledError` 击中 lifespan 的
   `await receive()`，停机清理逻辑（缓存失效、优雅关闭）被跳过；
3. 跨任务关闭中途抛错遗留未回收的 task group 子任务（stdio 子进程 / SSE 连接泄漏）。

## 2. 根因

anyio 的 cancel scope / task group 要求 **enter 与 exit 发生在同一 asyncio task**
（`_asyncio.py` 的 `current_task() is not self._host_task` 校验）。而池化会话是
进程级单例：打开（`main.py` lifespan 预热）与关闭（请求任务 TTL 回收 / 工具调用
失效重建 / 关机）天然跨任务，违约不可避免。

## 3. 修复：per-server session-worker 所有权模型

每个池化会话由一个专属长驻 worker 任务 `mcp-session-{server}` 持有，会话 CM 的
enter / exit 只发生在该任务内。不变式（I1–I5，详见 `app/core/mcp_client.py` 模块
docstring）：

- **I1** 会话 CM 的 enter/exit 只发生在其专属 worker 任务内（打开失败时以真实异常
  在 worker 任务内自然展开退出）；
- **I2** 每个 worker 最终要么入池、要么被 stop —— 即使所有等待者在 build 期间被取消
  （finalizer 兜底收养，无孤儿 worker / 会话）；
- **I3** 打开失败语义保留：task group 自然展开产生根因 `ExceptionGroup`
  （Exception 子类 → tenacity 可重试、可降级）；
- **I4** 真实外部取消语义保留：`CancelledError` 穿透等待者不被降级；
- **I5** 工具调用路径不变：调用方仍跨任务直连 session（JSON-RPC 调用 task-agnostic），
  不新增代理层。

关闭路径统一为「优雅停机」：设置 stop 事件 → 有界等待
（`MCP_SESSION_STOP_TIMEOUT`，默认 10s）→ 超时兜底 `worker.cancel()`
（取消仍在 worker 任务内投递，亲和性不破）。

## 4. 行为变更（向后兼容）

| 项 | 旧行为 | 新行为 |
|---|---|---|
| 池化会话关闭 | 跨任务直接 `__aexit__`（违约，随机炸） | worker 内优雅退出 + 有界超时兜底 cancel |
| lifespan 误杀 | 关闭投递的取消可击中 lifespan | 不再发生（关闭投递被隔离在 worker 内） |
| 冷加载 singleflight | 等待者取消即杀 build | build 跨等待者取消存活，finalizer 兜底入池 |
| 公开 API（`load_server_tools` / `shutdown_mcp_sessions` / 调试端点） | — | 零变化 |

## 5. 新增配置与指标

| 项 | 值 |
|---|---|
| `MCP_SESSION_STOP_TIMEOUT`（env，默认 `10`） | 关闭 / 关机时等待 worker 优雅退出的宽限秒数，超时兜底 cancel |
| `mcp_session_stop_total{outcome}`（Prometheus Counter） | `graceful` / `timeout_cancelled` / `crashed` / `cancelled` / `foreign_loop` |

新日志事件：`mcp_session_stop_timeout`（超时兜底）、`mcp_session_worker_cancelled`（worker 被取消收尾）；
`mcp_session_close_failed` 语义不变（关闭阶段异常，never raise）。

## 6. 影响面

| 层 | 文件 |
|---|---|
| 核心层（重写） | `app/core/mcp_client.py`（worker 所有权模型；`_session_worker` / `_SessionBuild` / `_building` / `_finalize_build` / `_close_session` / `_ensure_session` / `shutdown_mcp_sessions`） |
| 配置 | `app/core/config.py`（`MCP_SESSION_STOP_TIMEOUT`）、`.env.example` |
| 指标 | `app/core/metrics.py`（`mcp_session_stop_total`） |
| 单元测试 | `tests/unit/core/test_mcp_client.py`（含真实 anyio task group 亲和性回归测试）、`tests/unit/agents/test_mcp_manager.py`（池复用测试改单 loop 编排） |
| 集成测试 | `tests/integration/agents/conftest.py`（fake_mcp teardown 同步清理 `_building` / `_finalize_tasks`） |
| 文档 | `docs/mcp-manual-testing.md`（第 6 节 worker 模型 + 新指标） |

不改动：`app/main.py` 预热流程、`app/services/agents/mcp_manager.py`、全部 API 层
——存量进程重启后即生效。

## 7. 验证

- 单元：`tests/unit/core/test_mcp_client.py` 37 用例（含 TTL 回收 / 关机的
  anyio 亲和性回归、等待者取消 singleflight、超时兜底 cancel、跨 loop 重建、
  关机取消在途 build）；`tests/unit/agents/test_mcp_manager.py` 全绿。
- 手工冒烟：`make dev` 预热建池 → TTL 过期 / 触发子代理测试 → 日志无
  `mcp_session_close_failed`、无 lifespan `CancelledError`，`reason=recovered` 重建正常
  → Ctrl-C 关机清理完整执行，`mcp_session_stop_total{outcome="graceful"}` 递增。
