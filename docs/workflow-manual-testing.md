# 工作流引擎手动测试指南

| 项 | 值 |
| --- | --- |
| 文档角色 | Phase 9（M8 交付就绪）手动验收指南，供人工端到端核对 |
| 依据文件 | `docs/workflow-reimpl-plan/spec/spec-09-加固与交付.md`、`spec/CONTRACT.md`、`app/workflow/` 实现、`README.md#workflow-engine` |
| 涉及编号 | S5/S6/S7/S9/S10/S11/S12、H1/H4/H6/H7、D3、R6、AD-03/AD-08/AD-10/AD-12、CONTRACT §4.10/§4.12/§5 |
| 适用版本 | `app.workflow.__version__ = 0.1.0` |

> 本指南只覆盖**人工手动测试**；自动化门禁（`uv run pytest`、`make lint`、`make typecheck`）见
> [spec-09 §11 验收命令](workflow-reimpl-plan/spec/spec-09-加固与交付.md#11-验收命令)。
> 引擎的快速开始与目录结构见仓库根 [README.md](../README.md#workflow-engine)。

---

## 1. 前置条件与环境变量

### 1.1 运行环境

| 项 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.13 | 见 `.python-version` / `pyproject.toml` |
| 包管理 | `uv` | 所有命令以 `uv run` 前缀执行 |
| 依赖安装 | `make install` | 等价 `uv sync` + 安装 pre-commit hooks |
| 版本锁定 | 以 `uv.lock` 为准 | langgraph 1.0.2 / langchain 1.0.5 / langchain-core 1.0.4 / langchain-openai 1.0.2（AD-06） |

### 1.2 环境变量（AD-12）

密钥一律走环境变量（R5/H6），引擎配置中**不存在**明文 `api_key` 字段。示例 YAML 头部注释已写明各自所需 env。

| 环境变量 | 是否必需 | 用途 | 缺失时行为 |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | `minimal.yaml` / `condition_branch.yaml` 必需 | `llm_type: openai` 默认密钥 env | 抛 `ConfigError`（消息含 env 名，不含密钥值） |
| `OPENAI_BASE_URL` | 可选 | OpenAI 兼容网关地址（`llm_type: openai` 默认 base_url env） | 未设置时用 SDK 默认端点 |
| `ANTHROPIC_API_KEY` | 仅 `llm_type: anthropic` 时必需 | `llm_type: anthropic` 默认密钥 env（`.env.example` 中为空值占位） | 抛 `ConfigError` |

准备步骤：

```bash
cp .env.example .env        # 然后填入 OPENAI_API_KEY（LLM 示例需要）
```

> `http_demo.yaml` **零 env、零网络**：它以 `mock_enabled: true` 演示（S9），不发起任何真实请求，
> 可用于验证"安装是否成功"而无需任何密钥。

### 1.3 启动后端（仅 HTTP API 测试需要）

CLI 测试无需启动后端。HTTP API 测试需要宿主进程注入注册表：

```bash
make dev                    # 本地开发服务器（端口 8000，热重载）
# 或
make docker-up              # Docker：API + DB
```

宿主启动时在组合根（`app/main.py`）构建并注入注册表：

```python
app.state.workflow_registry = build_registry(DEFAULT_CONFIG_DIR)   # DEFAULT_CONFIG_DIR = app/workflow/config/examples
```

引擎模块自身**不持有任何模块级缓存 / 可变全局**（H4/G7）；`api.py` 经 `app.state` 读取注入的注册表。

### 1.4 限流说明

HTTP `execute` 端点受 slowapi 限流（AD-10 要求所有路由带限流装饰器）：

| 端点 | 限流 | 来源 |
| --- | --- | --- |
| `POST /api/v1/workflows/{workflow_id}/execute` | `20 per minute` | `app/core/config.py` → `RATE_LIMIT_ENDPOINTS["workflows_execute"]` |

手动连续调用超过阈值会返回限流响应（宿主 `rate_limit_exceeded_handler` 统一信封），属预期行为，等待窗口重置即可。

---

## 2. 三个示例 YAML 端到端执行

三个示例位于 `app/workflow/config/examples/`，CLI 默认目录即此处（`--dir` 缺省值），无需额外指定。

CLI 通用形态：

```bash
uv run python -m app.workflow run \
  --workflow <workflow_id> \
  --input '<JSON 对象>' \
  [--dir <定义目录>] [--log-level INFO|DEBUG] [--json-log]
```

- **stdout**：机器可读的 `ApiResponse` 信封（CONTRACT §4.12），是唯一成功/失败判据；
- **stderr**：structlog 结构化日志（`setup_logging`），不污染 stdout；
- **退出码**：成功 `0`，失败 `1`（AD-10）。

### 2.1 minimal.yaml — LLM 单节点（`demo_minimal`）

| 项 | 值 |
| --- | --- |
| workflow_id | `demo_minimal` |
| 所需 env | `OPENAI_API_KEY` |
| 结构 | `greet`（llm，`gpt-4o-mini`）→ END |
| 验证点 | S5（LLM 节点从 `state.messages` 取对话内容）、CONTRACT §4.12 信封 |

执行命令：

```bash
uv run python -m app.workflow run --workflow demo_minimal \
  --input '{"messages": [{"role": "user", "content": "hi"}]}'
```

> **为何传 `messages` 而非 `input`**：LLM 节点契约（S5）从 `state.messages` 取对话内容；
> `{"role": ..., "content": ...}` dict 形态由 langchain 本地转换为消息对象。`state_schema` 已声明
> `messages: {type: list}`。若 `messages` 与实例级 messages 皆空 → 抛 `ValueError`。

预期 stdout 信封（成功）：

```json
{
  "success": true,
  "data": {
    "messages": [{"role": "user", "content": "hi"}],
    "history": ["greet: {'response': '...', 'model': 'gpt-4o-mini'}..."],
    "greet_result": {"response": "<模型回复>", "model": "gpt-4o-mini"}
  },
  "error": null,
  "metadata": {"workflow_id": "demo_minimal", "run_id": "<32位hex>", "duration_ms": 1234.5, "node_count": 1}
}
```

核对清单：

- [ ] `success == true`，`error == null`，退出码 `0`；
- [ ] `data` 含 `greet_result` 整包（`{"response", "model"}`，S4 双写）；
- [ ] `metadata` 四键齐全：`workflow_id`/`run_id`/`duration_ms`/`node_count`（`node_count == 1`）；
- [ ] `run_id` 为 `uuid4().hex`（32 位十六进制）；
- [ ] `history` 为增量条目（S3），不含密钥与完整 state 明文（H6）。

> **输出投影规则（EXP-G8/S4）**：`data` 是运行结束后的 state 投影，只包含
> **① `state_schema` 显式声明且被写入/传入的通道**（如 `messages`）、**② 自动注入的 `history`**、
> **③ 本次执行过的节点的 `{node}_result` 槽位**（构建期预声明）。`map_output_to_state` 平铺出的
> `response`/`model` 等字段**若未在 `state_schema` 声明，会被 langgraph 静默丢弃**，因此不出现在 `data` 顶层——
> 上游输出应从 `{node}_result` 读取。未执行的节点其 `_result` 槽位也不出现。

无密钥离线演示：允许以 mock/测试目录方式演示 LLM 路径（spec-09 §8 DoD）——在自定义 `--dir` 中放置
使用测试 FakeLLM 的定义，或改用 §2.2 的纯 HTTP mock 示例验证引擎链路，README 已说明此豁免。

### 2.2 http_demo.yaml — HTTP 节点 mock 演示（`demo_http`）

| 项 | 值 |
| --- | --- |
| workflow_id | `demo_http` |
| 所需 env | 无 |
| 结构 | `fetch`（http，`POST https://example.com/api/items`，mock）→ END |
| 验证点 | S9（mock 显式开关，零网络）、mock key 格式、`response_path` 提取 |

执行命令：

```bash
uv run python -m app.workflow run --workflow demo_http --input '{"input": "hi"}'
```

预期 stdout 信封（成功，实测原样）：

```json
{
  "success": true,
  "data": {
    "input": "hi",
    "history": ["fetch: {'status_code': 200, 'url': 'https://example.com/api/items', 'response': {'items': ['alpha', 'beta']..."],
    "fetch_result": {"status_code": 200, "url": "https://example.com/api/items", "response": {"items": ["alpha", "beta"]}}
  },
  "error": null,
  "metadata": {"workflow_id": "demo_http", "run_id": "<32位hex>", "duration_ms": 2.4, "node_count": 1}
}
```

核对清单：

- [ ] 全程**零网络请求**（`mock_enabled: true`，S9）；命中即返回，`status_code` 固定为 `200`（mock 命中模拟成功响应）；
- [ ] mock key 命中格式 `"POST https://example.com/api/items"`（`"{METHOD} {url}"`，回退 `"{url}"`）；
- [ ] `fetch_result.response` 为 `response_path: "data"` 从 mock 响应 `{"data": {"items": [...]}}` 中提取出的 `{"items": ["alpha","beta"]}`；
- [ ] `data` 顶层为**声明通道 `input` + `history` + `fetch_result`**；平铺的 `status_code`/`url`/`response` 未在 `state_schema` 声明，被 langgraph 丢弃（EXP-G8/S4，见 §2.1 投影规则）；
- [ ] `body_template: '{"query": "{input}"}'` 渲染时 `{input}` 被替换为 `hi`（渲染上下文扁平化）；
- [ ] `duration_ms` 极小（无真实 I/O）。

> **mock 仅演示用途**（README 强调）：生产使用请关闭 mock（`mock_enabled: false`）并配置真实端点。
> mock 启用但 key 未命中 → 抛 `HTTPNodeError`，**绝不静默回退真实调用**（S9/H2/H6）。

### 2.3 condition_branch.yaml — 条件分支组合（`condition_branch_demo`）

| 项 | 值 |
| --- | --- |
| workflow_id | `condition_branch_demo` |
| 所需 env | `OPENAI_API_KEY` |
| 结构 | `check`（llm）→ 条件边 → `notify`（http mock）或 `summarize`（llm）→ END |
| 验证点 | S6/S7（条件路由）、`default_edges`/`no_match_policy` |

`check` 节点 system_prompt 要求模型**只输出一个单词** `OK` 或 `NEED_REVIEW`；两条条件边按 `check_result.response`
等值比较分流（S7：条件表达式仅支持 `path == 字面量` 或 `path` 真值判断，**绝不 eval**）：

```yaml
edges:
  - source: check
    target: notify       # HTTP mock
    condition: "check_result.response == 'OK'"
  - source: check
    target: summarize    # LLM
    condition: "check_result.response == 'NEED_REVIEW'"
```

分支 A —— 期望命中 `OK`（走 `notify` HTTP mock）：

```bash
uv run python -m app.workflow run --workflow condition_branch_demo \
  --input '{"input": "hello there", "messages": [{"role": "user", "content": "hello there"}]}'
```

预期：`data` 含 `notify_result`（`{"status_code":200,"url":"https://example.com/api/notify","response":{"ticket":"DEMO-1"}}`），
`node_count == 3`（三个节点均注册，但本次运行只执行 `check` + `notify`）。

分支 B —— 期望命中 `NEED_REVIEW`（走 `summarize` LLM）：

```bash
uv run python -m app.workflow run --workflow condition_branch_demo \
  --input '{"input": "please review", "messages": [{"role": "user", "content": "please review"}]}'
```

预期：`data` 含 `summarize_result`（`{"response":"<摘要>","model":"gpt-4o-mini"}`）。

> 分流由 LLM 实际输出决定，无法 100% 保证命中特定分支；如需稳定复现某一分支，
> 用 `--log-level DEBUG` 观察 `condition_route_matched` 日志确认命中路径（见 §5.2）。

核对清单：

- [ ] 每次运行只走**一条**分支（`notify` 或 `summarize`），二者不同时出现在 `history`；
- [ ] DEBUG 日志出现 `condition_route_matched`（记录 source/condition/target，**不含完整 state**，C3/H6）；
- [ ] 条件表达式为等值比较（含 `==`），非 eval（S7）；
- [ ] `check_result.response` 为条件读取路径（点路径逐层解析，非 dict 中途 → None）。

**no-match 兜底说明（S6）**：条件边默认 `no_match_policy='raise'`——所有 condition 均不命中（如 LLM 输出了
标记字以外的内容）时抛 `ConditionNotMatchedError`（含 source 与全部条件）。若需兜底分支，宿主构建
`GraphBuilder`/`WorkflowRegistry` 时传 `no_match_policy='default'`，并在 `register_workflow`/`build_graph`
提供 `default_edges={"<source节点>": "<兜底目标或END>"}`；缺 `default_edges` 条目会在**构建期**报错
（构建期失败优于运行期失败）。CLI 默认使用 `no_match_policy='raise'`（`build_registry` 不传该参数）。

---

## 3. HTTP API 手动测试

端点（挂载于 `app/api/v1/api.py`，`include_router(workflow_router, tags=["Workflow"])`）：

```
POST /api/v1/workflows/{workflow_id}/execute
```

- 请求体：可选 JSON 对象（作为 workflow 输入），缺省视为 `{}`；
- 注册表来源：`get_registry(request)` 从 `app.state.workflow_registry` 读取（DI）；缺失注入 → `RuntimeError`，
  在 try 内解析并落入失败信封（R6）；
- 执行方式：同步 `registry.execute_workflow` 经 `run_in_threadpool` 包装（AD-10），不阻塞事件循环。

### 3.1 场景一：200 成功

```bash
curl -X POST http://localhost:8000/api/v1/workflows/demo_http/execute \
  -H 'Content-Type: application/json' \
  -d '{"input": "hi"}'
```

预期响应体（宿主统一信封 `{code, message, data}`）：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "input": "hi",
    "history": ["fetch: ..."],
    "fetch_result": {"status_code": 200, "url": "https://example.com/api/items", "response": {"items": ["alpha", "beta"]}},
    "metadata": {"workflow_id": "demo_http", "run_id": "<32位hex>", "duration_ms": 0.7, "node_count": 1}
  }
}
```

> HTTP 成功且输出为 dict 时，`metadata` 被折叠进 `data`（`_host_envelope_content`）；`data` 其余部分
> 与 CLI 的 `data` 一致（声明通道 + `history` + `{node}_result`，见 §2.1 投影规则）。

### 3.2 场景二：404 未知 workflow_id

```bash
curl -i -X POST http://localhost:8000/api/v1/workflows/no_such_wf/execute \
  -H 'Content-Type: application/json' -d '{}'
```

预期：HTTP 状态 `404`，响应体：

```json
{"code": 404, "message": "workflow not found: no_such_wf", "data": null}
```

- `message` 为**脱敏后**的错误摘要（经 `redact_processor`，H6）；
- 内部异常 `WorkflowNotFoundError` 被捕获并映射为 404，不外泄堆栈。

### 3.3 场景三：500 执行失败

构造一个必然失败的执行（如对需要密钥的 `demo_minimal` 在未配置 `OPENAI_API_KEY` 的环境调用）：

```bash
curl -i -X POST http://localhost:8000/api/v1/workflows/demo_minimal/execute \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "hi"}]}'
```

预期：HTTP 状态 `500`，响应体：

```json
{"code": 500, "message": "workflow execution failed for 'demo_minimal': ConfigError: LLMNode 'greet': missing required env var 'OPENAI_API_KEY' for llm_type 'openai'", "data": null}
```

核对清单：

- [ ] 三场景 HTTP 状态码与信封 `code` **数值一致**（200/404/500）；
- [ ] 成功信封 `message == "success"`，失败信封 `data == null`；
- [ ] 失败 `message` 中任何密钥样式片段被替换为 `***`（H6），不含完整 state；
- [ ] 服务端 stderr 结构化日志出现 `api_workflow_execution_requested`，失败时 `api_workflow_not_found`（warning）或
      `api_workflow_execution_failed`（`logger.exception` 保留 traceback）。

---

## 4. 响应信封格式验证

CLI 与 HTTP 共用内部 `ApiResponse`（CONTRACT §4.12），但在**出口**投影为两种形态：

### 4.1 双形态对照

| 维度 | CLI stdout（§4.12 原样） | HTTP wire（宿主统一信封） |
| --- | --- | --- |
| 顶层字段 | `{success, data, error, metadata}` | `{code, message, data}` |
| 成功标识 | `success: true` | `code == HTTP status`，`message: "success"` |
| 失败标识 | `success: false`，`error: <脱敏摘要>` | `code`（404/500），`message: <脱敏摘要>`，`data: null` |
| metadata 位置 | 顶层独立键 | 折叠进 `data`（见下） |
| 投影函数 | 直接 `to_json()` | `_project_to_host_envelope` / `_host_envelope_content` |

### 4.2 HTTP metadata 折叠规则

`_host_envelope_content` 对成功响应的 metadata 处理：

- workflow 输出为 **dict**（常见）→ 运行元数据折叠进输出：`data = {**output, "metadata": {...}}`；
- workflow 输出为**非 dict** → 元数据与原始输出并列：`data = {"result": <output>, "metadata": {...}}`。

`metadata` 恒含四键：`workflow_id` / `run_id` / `duration_ms` / `node_count`。

> CLI stdout 保留未经改动的 §4.12 信封（回归基线，`tests/unit/workflow/test_cli.py`）；
> HTTP wire 形态由 `_project_to_host_envelope` 独占产出。路由的 `response_model=HostApiResponse[...]`
> 与 `responses={404,500}` 仅用于 OpenAPI 文档化，**不改变运行时行为**（端点返回 `JSONResponse`，FastAPI 跳过序列化）。

核对清单：

- [ ] CLI 信封顶层为 `success/data/error/metadata`（不含 `code/message`）；
- [ ] HTTP 信封顶层为 `code/message/data`（不含 `success/error`）；
- [ ] HTTP 成功且输出为 dict 时，`metadata` 在 `data` 内；
- [ ] `ensure_ascii=False`：CLI 信封中的中文/非 ASCII 原样输出（不转义为 `\uXXXX`）。

---

## 5. 并发场景日志隔离检查（H1/D3/S10/S11）

引擎并发模型（S10，ADR-004）：**同一 workflow 串行**（per-workflow `RLock`），**不同 workflow 并行**；
`_meta_lock` 守护锁表懒创建与注册/删除的映射变更。运行级日志经 `_RUN_COLLECTOR` ContextVar 隔离（S11），
作为并发日志不串扰的第二道防线（D3/H1）。

### 5.1 自动化并发压测（首选）

spec-07 的并发测试已固化进 `pytest -m integration`（spec-09 TC2），覆盖：

| 测试 | 断言 | 编号 |
| --- | --- | --- |
| `test_concurrent_same_workflow_logs_isolated` | 16 线程 × 64 次运行同一 `wf_concurrent`：`run_id` 全唯一；每个 `RunResult.execution_logs` 恰好覆盖 `{a, b}` 两节点各一条；无跨 run 泄漏 | H1 |
| `test_different_workflows_not_blocked` | 两个不同 workflow_id（`wf_alpha`/`wf_beta`）经共享 `threading.Barrier(2)` 会合，证明交错并发执行、互不阻塞（若锁被跨 id 共享，barrier 会超时 `BrokenBarrierError`） | D3 |

稳定性验证（spec-09 §7/§11：循环 5 次无偶发失败）：

```bash
for i in 1 2 3 4 5; do uv run pytest -m integration -q || break; done
```

核对清单：

- [ ] 5 次循环全绿，无偶发失败（flaky）；
- [ ] `test_concurrent_same_workflow_logs_isolated` 断言 `len(run_ids) == 16 * 64 == 1024`（全唯一）；
- [ ] `test_different_workflows_not_blocked` 断言 `result_alpha.run_id != result_beta.run_id` 且两者均在 10s 内完成。

### 5.2 手动日志隔离观察

以 DEBUG 级别运行示例，人工确认日志卫生（H6 终检）与条件路由轨迹：

```bash
uv run python -m app.workflow run --workflow condition_branch_demo \
  --input '{"input": "please review", "messages": [{"role": "user", "content": "please review"}]}' \
  --log-level DEBUG
```

核对清单（观察 stderr）：

- [ ] 出现 `condition_route_matched`（source/condition/target），**不打印完整 state**（C3/H6）；
- [ ] 无完整 state dump、无密钥明文；任何密钥样式片段显示为 `***REDACTED***` 或 `***`（`redact_processor`）；
- [ ] 超长字符串被截断并标注 `...(truncated)`（`redact` 的 `max_len=500`）；
- [ ] 日志事件名为 `lowercase_with_underscores`（S15），无 f-string 拼接痕迹。

JSON 日志形态（便于机器核对）：追加 `--json-log`，stderr 以 JSON 渲染每条日志。

---

## 6. spec-09 验收命令清单

照抄 [spec-09 §11](workflow-reimpl-plan/spec/spec-09-加固与交付.md#11-验收命令)，每条附预期结果：

```bash
# 1. 覆盖率 ≥ 80%（AD-08）
uv run pytest --cov=app.workflow --cov-report=term-missing --cov-fail-under=80
#    预期：全量测试绿，TOTAL 覆盖率 ≥ 80%，命令退出码 0（低于阈值则 fail）

# 2. 并发稳定性 5 次循环（spec-09 §7）
for i in 1 2 3 4 5; do uv run pytest -m integration -q || break; done
#    预期：5 次全绿，无偶发失败

# 3. 硬编码密钥审计（H6，人工逐条确认，误报记入审计记录）
grep -rniE "(api[_-]?key|token|secret|password)\s*[:=]" app/workflow/ tests/
#    预期：命中项均为 env 名引用 / 测试夹具 / 脱敏模式，无真实密钥字面量

# 4. 无界缓存审计（H4）
grep -rn "lru_cache" app/workflow/
#    预期：零命中

# 5. 构建期注册表快照反模式审计（H5）
grep -n "registry" app/workflow/graph_builder.py
#    预期：零命中或仅出现于注释；GraphBuilder 构造器无 registry 参数

# 6. 示例跑通（本文档 §2）
uv run python -m app.workflow run --workflow demo_minimal --input '{"input":"hi"}'
#    预期：stdout 输出成功信封，退出码 0

# 7. lint / format / typecheck 零问题（AD-07/AD-11）
make lint && ruff format --check . && make typecheck
#    预期：三条命令均零告警 / 零错误
```

补充审计闸门（CONTRACT §10）：

```bash
grep -rn "clear_execution_history" app/workflow/registry.py   # 期望零命中（H1：运行时禁靠清空实例历史收集日志）
grep -rn "print(" app/workflow/ --exclude=cli.py              # 期望零命中（G8：cli.py 为唯一 print 豁免点）
grep -rn "yaml\.load(" app/workflow/                          # 期望零命中（S16：只允许 yaml.safe_load）
```

---

## 7. 故障排查

异常族单点定义于 `app/workflow/models.py`（CONTRACT §5），全部继承 `WorkflowEngineError`。
下表按"症状 → 根因 → 处理建议"组织，映射 CONTRACT §5 场景表。

### 7.1 密钥缺失 → `ConfigError`

- **症状**：CLI 退出码 1，`error` 含 `missing required env var 'OPENAI_API_KEY'`；HTTP 返回 500，`message` 同上（脱敏后）。
- **根因**：`llm_type` 对应的默认 env（或显式 `api_key_env`）未设置或为空。
- **处理**：
  1. `cp .env.example .env` 并填入 `OPENAI_API_KEY`（或 `ANTHROPIC_API_KEY`）；
  2. 或在节点 config 用 `api_key_env` 指定自定义 env 名；
  3. 确认消息**只含 env 名、不含密钥值**（H6，这是设计而非缺陷）。
- **相关编号**：S8 无关；H6/R5（密钥 env-only）。

### 7.2 条件路由未命中 → `ConditionNotMatchedError`

- **症状**：CLI 退出码 1，`error` 含 `No condition matched for source 'check'; evaluated conditions: [...]`；HTTP 500。
- **根因**：`no_match_policy='raise'`（默认）下，所有条件边等值/真值判断均为 False（如 LLM 输出了 `OK`/`NEED_REVIEW` 以外的内容，或带标点/解释）。
- **处理**：
  1. 收紧上游 LLM 的 `system_prompt`，强制单一标记字输出（示例已用 `temperature: 0.0` + "只输出一个单词"）；
  2. 用 `--log-level DEBUG` 查看 `condition_route_matched` 是否缺失，确认实际 `check_result.response` 值；
  3. 如需兜底：宿主以 `no_match_policy='default'` 构建并提供 `default_edges`（S6，构建期校验，缺条目会构建期报错）；
  4. 核对条件路径写法（点路径，如 `check_result.response`），路径中途遇非 dict 解析为 `None`（S7）。
- **相关编号**：S6/S7、C3（禁静默落到最后一条边）。

### 7.3 LLM 调用失败 / 重试耗尽 → `LLMNodeError`

- **症状**：`error` 含 `LLM invocation failed after N attempts`。
- **根因**：底层 provider 报错（网络、鉴权、限流、模型名不兼容等），tenacity 重试（仅 `status_code == 429` 或 `>= 500` 命中）耗尽后包装抛出。
- **处理**：
  1. 检查 `OPENAI_BASE_URL` 与 `model_name` 是否匹配所用网关（OpenAI 兼容网关仅兼容协议、不兼容任意模型名）；
  2. 429/5xx 为可重试类，退避序列 `retry_base_delay * 2**attempt`（默认 1,2,4，AD-03/S8）；调大 `max_retries`（默认 3）；
  3. 非 429/5xx（如 401 鉴权、400 参数）不重试，直接失败——检查密钥与参数；
  4. `anthropic` 非流式调用需显式 `max_tokens`（引擎在未设置时回退保守值 4096，EXP-L1）。
- **相关编号**：S8、AD-03、EXP-L1/L3。

### 7.4 HTTP 调用失败 / mock 未命中 → `HTTPNodeError`

- **症状**：`error` 含 `<METHOD> <url> failed with status <code> after N attempts`，或 `mock enabled but no mock_responses entry for ...`。
- **根因**：
  - 真实分支：`raise_for_status()` 触发（4xx/5xx），或连接/超时错误；
  - mock 分支：`mock_enabled: true` 但 `mock_responses` 无 `"{METHOD} {url}"`（或回退 `"{url}"`）键。
- **处理**：
  1. HTTP `max_retries` **默认为 0（不重试）**，需显式开启（H2/S8）；仅 `retry_on_status`（默认 `[429,500,502,503,504]`）命中才重试；
  2. mock 未命中**禁止静默回退真实调用**（S9）——核对 mock key 拼写（含方法与渲染后 URL 完全一致）；
  3. 生产环境关闭 mock（`mock_enabled: false`）并配置真实端点；
  4. `body_template` 渲染后必须为合法 JSON，否则抛 `ValueError`（带节点名）。
- **相关编号**：S8/S9、H2/H6。

### 7.5 未知 workflow_id → `WorkflowNotFoundError`

- **症状**：CLI 退出码 1，`error` 含 `workflow not found: <id>`；HTTP 404。
- **根因**：`--workflow` 指定的 id 未在注册目录中定义，或 YAML `workflow_id` 与命令行不一致。
- **处理**：
  1. 核对 YAML 顶层 `workflow_id` 与 `--workflow` 完全一致；
  2. 确认 `--dir`（默认 `app/workflow/config/examples`）下有对应 YAML；
  3. HTTP 场景确认宿主启动时 `build_registry(DEFAULT_CONFIG_DIR)` 已加载该定义（`app.state.workflow_registry`）。
- **相关编号**：CONTRACT §5、S13（`delete_workflow` 为唯一删除入口）。

### 7.6 YAML 解析 / 字段校验失败 → `ValueError` / `ValidationError`

- **症状**：CLI 退出码 1，`error` 含 `failed to load workflow definition from <path>` 或 pydantic 校验详情。
- **根因**：YAML 语法错误、文件不存在、顶层非 mapping、必需字段缺失、节点名重复、`entry_point` 不在 nodes、边 source/target 非法、节点 config 含未知字段（`extra="forbid"`）。
- **处理**：
  1. `load_definitions_from_dir` 为 **fail-fast**：任一文件失败即抛带路径的 `ValueError`（不静默跳过）；
  2. 节点 config 校验严格（LLMConfig/HTTPNodeConfig `extra="forbid"`，S14）——移除拼写错误的多余键；
  3. `WorkflowDefinition` 为 `extra="ignore"`（容忍 `description` 等注释性键，K1）；
  4. 图级校验（`entry_point`/边端点/混用普通边与条件边）由 `GraphBuilder._validate_definition` 在构建期报错（C5）。
- **相关编号**：S14/S16、C5、K1。

### 7.7 HTTP 限流 429

- **症状**：短时间连续调用 `execute` 端点收到限流响应。
- **根因**：slowapi `workflows_execute` 限流 `20 per minute`。
- **处理**：降低调用频率或等待窗口重置；测试并发日志隔离请优先用 §5.1 的进程内 pytest 压测（不经 HTTP，不受此限流影响）。
- **相关编号**：AD-10（所有路由带限流装饰器）。

### 7.8 快速定位建议

| 想看什么 | 命令 / 位置 |
| --- | --- |
| 成功/失败判据 | CLI stdout 信封 `success`；HTTP 状态码 + `code` |
| 详细执行轨迹 | `--log-level DEBUG`（stderr），关注 `condition_route_matched` / `*_node_execution_failed` |
| 节点级执行历史 | `RunResult.execution_logs`（每 run 一份，S11）；`registry.get_node_execution_history(wf, node)` |
| 最近一次运行历史 | `registry.get_execution_history(wf)`（单槽位，只留最近一次，S12） |
| 注册了哪些 workflow | `registry.list_workflows()` / `get_registry_stats()` |
| 密钥是否泄漏到日志 | `grep -rniE "(api[_-]?key\|token\|secret\|password)" logs/`，确认均为 `***REDACTED***` |

---

## 8. 相关文档

- 引擎快速开始与扩展指南：[README.md#workflow-engine](../README.md#workflow-engine)
- 架构总览（三层）：[00-架构总览.md](workflow-reimpl-plan/00-架构总览.md)
- 编码契约（接口冻结 §4 / 异常族 §5 / 行为语义 §6）：[spec/CONTRACT.md](workflow-reimpl-plan/spec/CONTRACT.md)
- Phase 9 加固与交付：[spec-09-加固与交付.md](workflow-reimpl-plan/spec/spec-09-加固与交付.md)
- Node 开发规范：[workflow-node-development.md](workflow-node-development.md)
- API 与 Trace 支持方案：[workflow-api-and-trace.md](workflow-api-and-trace.md)
