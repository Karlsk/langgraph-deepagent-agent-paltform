# spec-08 入口集成（Phase 8）

## 1. 元信息

| 项 | 值 |
| --- | --- |
| Phase / 里程碑 | Phase 8 / M7 端到端打通 |
| 人日估算 | **1.0**（含可选任务 TC3 的 0.25d；不实施 TC3 则按 0.75d 结算） |
| 前置 spec | spec-07（`WorkflowRegistry` / `load_definitions_from_dir` / `RunResult`）、spec-00（`logging_conf.setup_logging` 骨架）；无 EXP 门禁（不依赖 langgraph/langchain API 行为） |
| 后续依赖方 | spec-09（加固与交付） |
| 涉及编号 | K8、R1/R6、H6（落实收尾）、异常契约全家、AD-02/10/12 |

## 2. 目标

提供一个最小入口（**CLI 为主交付物**，FastAPI router 为可选扩展）把"加载 → 注册 → 执行 → 响应"串成端到端链路，并以统一响应信封输出；同时落地结构化日志的脱敏能力（H6 收尾）。

## 3. 前置依赖

- spec 间依赖：spec-07、spec-00。
- 代码库依赖：`app/workflow/registry.py`、`app/workflow/logging_conf.py`、`app/workflow/models.py`（异常族）；可选 api.py → `app/api/v1/api.py`（挂载点）、`app/core/limiter.py`（slowapi）、`app/core/logging.py`（structlog 已配置时幂等跳过）。
- 外部依赖：无网络（CLI 测试用 EchoNode 目录）。

## 4. 适配决策（本 Phase 涉及的 AD 条目）

- **AD-02**：脱敏实现为 **structlog processor**（`redact_processor`）而非原文档的 stdlib `RedactFilter`；`setup_logging` 幂等保持（FastAPI 集成时 `app.core.logging` 先行配置则幂等跳过）；行为等价性由 §7 测试锁定（caplog/capsys 断言）——**这是与原文档的显式偏差，以 CONTRACT §9 AD-02 及其 §4.11 修订说明为准**。
- **AD-02（修订 v2，2026-07-30）**：脱敏能力仍以 structlog processor（`redact_processor`）实现于 `app/workflow/logging_conf.py`，但**注册位置**区分场景：
  - FastAPI 集成场景——由**宿主组装点**（composition root：`app/main.py` 启动处，或 `app.core.logging` 暴露的注入钩子）将 `redact_processor` 注册进宿主的全局 processor 链，保证生产环境所有日志走同一条脱敏链；此时 `setup_logging` 幂等跳过。
  - CLI 独立场景——由 `setup_logging` 内部自行挂入 `redact_processor`（该函数已收窄为"最小幂等 bootstrap"，仅服务 CLI 独立入口与裸测试）。
  - 引擎内其余模块只 `structlog.get_logger(__name__)`，不配置日志、不 import `app.core.*`（红线不变）。详见 CONTRACT §9 AD-02（单点定义）与 §4.11 修订说明，以及 spec-00 §4 AD-02（修订 v2）。
- **AD-10**：CLI 主交付（`uv run python -m app.workflow run --workflow ... --input ...`）；`api.py` 为可选任务（0.25d），若实施必须遵守仓库戒律：slowapi rate limit 装饰器、DI 注入 registry、同步 `execute_workflow` 经 `run_in_threadpool` 包装、structlog。
- **AD-12**：示例 YAML 头部注释写明所需 env（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 等）。

## 5. 任务清单

- [ ] **TC1 logging_conf 脱敏补全（redact + processor + 幂等）（0.375d）**
  - 内容：常量 `SECRET_KEY_PATTERNS`；`redact(data, *, max_len=500)`（递归遍历 dict/list，键名命中模式——不区分大小写——的值替换为 `"***REDACTED***"`；超长字符串截断并标注 `...(truncated)`；不可 JSON 化对象走 `default=str`）；`redact_processor`（structlog processor：对 event_dict 的 `event` 与 kwargs 值做脱敏，命中密钥样式的 `key=value` / `"key": "..."` 片段替换为 `***`）；`setup_logging` 追加该 processor 且保持幂等
  - 修订备注【AD-02 v2，2026-07-30】："`setup_logging` 追加该 processor"仅面向 **CLI 独立场景**；FastAPI 集成场景的注册点在宿主组装点（`app/main.py` / `app.core.logging` 注入钩子），本卡交付物不变（实现仍在 `logging_conf.py`）
  - 产出文件：`app/workflow/logging_conf.py`
  - TDD 节奏：先写 §7 前 4 项（RED）→ 实现（GREEN）
- [ ] **TC2 cli.py + __main__.py + 响应信封（0.375d）**
  - 内容：`@dataclass ApiResponse`（`success`/`data`/`error`/`metadata` + `to_json(ensure_ascii=False)`）；`build_registry(directory)`（装配逻辑独立成函数，供 CLI 与可选 API 共用）；`build_parser()`（子命令 `run`，参数 `--dir` 默认 `app/workflow/config/examples`、`--workflow` 必填、`--input` 默认 `{}`、`--log-level` 默认 INFO、`--json-log` flag）；`main(argv)` 五步：解析参数 + `setup_logging` + `load_dotenv()` → `load_definitions_from_dir` 逐个 `register_workflow` → `execute_workflow` → 成功信封（`data=RunResult.output`，`metadata={"workflow_id","run_id","duration_ms","node_count"}`）→ 失败信封（`success=False`、`error` 为**脱敏后**异常摘要、不含完整 state/密钥）；输出 `print(response.to_json())`（CLI 面向用户输出是 G8/T20 的唯一例外，cli.py 已获 per-file-ignores 豁免），返回码 0/1；异常显式分层（R6）：`WorkflowEngineError` 族与其它未预期异常分别给出友好消息，except 必须有日志 + 信封输出；`__main__.py`：`raise SystemExit(main())`
  - 产出文件：`app/workflow/cli.py`（目标 < 200 行）、`app/workflow/__main__.py`
- [ ] **TC3 [可选] api.py FastAPI router（0.25d）**
  - 内容：`POST /workflows/{workflow_id}/execute` 复用同一 `ApiResponse` 信封与 `build_registry` 装配；slowapi rate limit 装饰器；registry 经 DI 提供；同步 `execute_workflow` 经 `run_in_threadpool` 包装；挂载到 `app/api/v1/api.py`
  - 产出文件：`app/workflow/api.py`（可选）、`app/api/v1/api.py`（追加挂载，既有文件改动白名单内）

## 6. 接口契约

见 CONTRACT §4.11（`setup_logging` / `SECRET_KEY_PATTERNS` / `redact` / `redact_processor`）与 §4.12（`ApiResponse` / `build_registry` / `build_parser` / `main` 签名）、§6 S15（日志摘要与脱敏形态）。

端到端信封形态（成功）：

```json
{
  "success": true,
  "data": {"greet_result": {"response": "hello"}, "response": "hello", "history": ["..."]},
  "error": null,
  "metadata": {"workflow_id": "demo_minimal", "run_id": "3f2a...", "duration_ms": 12.3, "node_count": 1}
}
```

## 7. TDD 测试要点

| 先写的测试 | 覆盖分支 | Mock/工具 |
| --- | --- | --- |
| `test_redact_secret_keys` | 嵌套 dict 含 `api_key`/`Authorization` → 值被替换 | 纯函数 |
| `test_redact_truncates_long` | 超长字符串截断且带标记 | 纯函数 |
| `test_redact_filter_on_log_record`【AD-02：锁定 processor 路径】 | 日志事件含 `api_key=sk-xxx` → 输出中不可见原值 | `caplog`/`capsys` 捕获 structlog 渲染结果 |
| `test_setup_logging_still_idempotent` | 挂 processor 后重复调用仍不叠加 handler/processor 链 | 配置内省 |
| `test_cli_run_success_envelope` | 注册一个 EchoNode 工作流目录 → `main(["run", ...])` 返回 0，stdout JSON `success=true`、metadata 齐 | `tmp_path` YAML + `capsys` + monkeypatch 目录 |
| `test_cli_bad_input_json` | `--input "{bad"` → 返回 1、`success=false`、error 友好 | `capsys` |
| `test_cli_unknown_workflow` | workflow_id 不存在 → 返回 1、信封 error 含 id | `capsys` |
| `test_cli_error_no_state_leak`（H6） | 节点抛出含哑密钥的异常 → stdout error 字段不含哑密钥与完整 state | 哑值断言 |
| `test_build_registry_loads_dir` | 目录 2 yaml → registry 含 2 workflow | `tmp_path` |

## 8. 验收标准 DoD

- [ ] 端到端手工验证：`uv run python -m app.workflow run --workflow demo_minimal --input '{"input":"hi"}'`（LLM 用 mock 或测试用 EchoNode 目录）返回规范信封
- [ ] 失败路径返回 `success=false` 信封且进程退出码为 1
- [ ] 脱敏测试全绿；`caplog`/`capsys` 证明密钥字样不出现在日志与输出中（H6 验证）
- [ ] CLI/装配代码无死 try/except（R6）
- [ ] `make lint`（cli.py 的 `T201` 豁免生效、其余文件零告警）、`ruff format --check .`、`make typecheck` 零问题
- [ ] 通用 DoD G1-G8 + 红线自检表（CONTRACT §12）逐项勾选

## 9. 本阶段涉及的隐患修复

- **H6（落实收尾）**：结构化日志 + `redact`/`redact_processor` 脱敏上线；错误响应的 error 字段脱敏；端到端链路不再有任何 print 调试与明文密钥落盘路径。

## 10. 交付物清单

- `app/workflow/logging_conf.py`（补全脱敏）
- `app/workflow/cli.py`、`app/workflow/__main__.py`
- `app/workflow/api.py`（可选）、`app/api/v1/api.py`（可选挂载）
- `tests/unit/workflow/test_logging_conf.py`、`tests/unit/workflow/test_cli.py`

## 11. 验收命令

```bash
uv run pytest tests/unit/workflow/test_logging_conf.py tests/unit/workflow/test_cli.py -m unit -v
uv run python -m app.workflow run --workflow demo_minimal --input '{"input":"hi"}'   # 端到端信封
uv run python -m app.workflow run --workflow not_exist --input '{}' ; echo "exit=$?"   # 期望 exit=1
make lint && ruff format --check . && make typecheck
wc -l app/workflow/cli.py   # 期望 < 200
```
