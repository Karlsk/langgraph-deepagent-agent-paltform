# spec-05 HTTPNode（Phase 5）

## 1. 元信息

| 项 | 值 |
| --- | --- |
| Phase / 里程碑 | Phase 5 / M4 具体节点可用（与 spec-04 并行） |
| 人日估算 | **1.5** |
| 前置 spec | spec-03（`BaseNode` / 工厂 / `utils` 两工具）；无 langchain 相关 EXP 门禁（不依赖 langgraph/langchain API 行为） |
| 后续依赖方 | spec-06（集成测试节点）、spec-07 |
| 涉及编号 | K10、R3/R5/R6/R10、H2（落实）/H4（规范）/H6（部分落实）、异常契约 `HTTPNodeError`、AD-02/03/05 |

## 2. 目标

实现 `nodes/http_node.py`：占位符模板渲染、method/headers/body_template、`response_path` 结果提取、**显式可配置**的 retry 与 mock。mock 仅在配置显式启用时生效（H2/H6）；失败路径必须有测试（H2）；默认不引入任何缓存（H4）。

## 3. 前置依赖

- spec 间依赖：spec-03。可与 spec-04 并行。
- 代码库依赖：`app/workflow/nodes/base.py`、`app/workflow/utils.py`、`app/workflow/models.py`（异常族）+ httpx 0.28.1（spec-00 已提升为主依赖，AD-05）/ tenacity 9.1.2。
- 外部依赖：**测试全程零真实网络**（`httpx.MockTransport` 或 monkeypatch `httpx.request`）。

## 4. 适配决策（本 Phase 涉及的 AD 条目）

- **AD-02**：日志用 structlog；`ExecutionLog.input_data` 只记 method/rendered_url/配置摘要（H6）；错误消息不含 headers 敏感值。
- **AD-03**：重试用 tenacity 实现（`stop_after_attempt(max_retries+1)` + `wait_exponential(multiplier=retry_base_delay)` + retry 谓词＝状态码命中 `retry_on_status`），耗尽或不可重试包装为 `HTTPNodeError`（消息含 method/url/状态码，**不含 headers 敏感值**）；测试 monkeypatch `tenacity.nap.sleep`，断言退避序列，不真睡。行为合同（S8/S9）与原文档手写循环等价。
- **AD-05**：`httpx` 主依赖（spec-00 已就位）。

## 5. 任务清单

- [ ] **TC1 HTTPNodeConfig + 模板渲染/_extract（0.375d）**
  - 内容：按 CONTRACT §4.8 实现 `HTTPNodeConfig`（`extra="forbid"`；`max_retries` 默认 0 显式开启；`mock_enabled` 默认 False）；`HTTPNode.__init__`（dict → `HTTPNodeConfig(**config)`；`super().__init__(name, NodeType.HTTP, ...)`）；`validate_config`（`url` 非空；`mock_enabled=True` 时 `mock_responses` 非空否则 `ValueError`）；`render_template`（`{key}` 占位符；一层嵌套 dict 扁平化为 `{parent[child]}`；**移除 device/cmd 等领域别名**；未命中占位符原样保留 + DEBUG 日志）；`_extract`（点路径逐层 get，缺失 → None；`path is None` → 整个 data）
  - 产出文件：`app/workflow/nodes/http_node.py`（目标 < 280 行）
  - TDD 节奏：先写 §7 前 2 项与 `_extract` 用例（RED）→ 实现（GREEN）
- [ ] **TC2 发送 + tenacity retry + mock 显式分支（0.5d）**
  - 内容：`_send_once`（`httpx.request(..., timeout=self.node_config.timeout)`，同步 K10）；`build_runnable()` 内部 `func(state)`：`convert_state_to_dict` + 一层扁平化渲染上下文 → 渲染 url/headers/body_template（body 非空 `json.loads`，解析失败 → 显式 `ValueError` 带节点名）→ **mock 分支**（仅 `mock_enabled`：按 `"{METHOD} {rendered_url}"` 回退 `"{rendered_url}"` 查 `mock_responses`；命中直接解析 JSON；**未命中 → `HTTPNodeError`，禁止静默回退真实调用**，H2/H6）→ **真实分支**（`_send_once` + `raise_for_status` + tenacity 按 `retry_on_status` 退避）→ `_extract` → 输出 `{"status_code": ..., "url": rendered_url, "response": extracted}` → `log_execution` → `map_output_to_state`；except 分支记录后 `raise`（R6）
  - 产出文件：`app/workflow/nodes/http_node.py`
- [ ] **TC3 失败路径 + H6 防泄漏测试（0.5d）**
  - 内容：§7 中重试/mock/失败/防泄漏全部用例（MockTransport 序列；monkeypatch `tenacity.nap.sleep`；`test_execution_log_no_secret_leak` 以含 `Authorization` 哑值 headers 断言 ExecutionLog/错误消息不含其值）
  - 产出文件：`tests/unit/workflow/nodes/test_http_node.py`
- [ ] **TC4 自注册/导出/DoD 核对（0.125d）**
  - 内容：模块底部 `register_node_type("http", HTTPNode)`；`nodes/__init__.py` 追加导出 `HTTPNode`/`HTTPNodeConfig`；工厂内置 http 分支联调转绿；§8 DoD 逐项核对
  - 产出文件：`app/workflow/nodes/http_node.py`、`app/workflow/nodes/__init__.py`

## 6. 接口契约

见 CONTRACT §4.8（`HTTPNodeConfig` / `HTTPNode` 签名）、§5（`HTTPNodeError` 场景）、§6 S5/S8/S9/S14/S15（进出管线、retry/mock 显式开关、extra=forbid、日志摘要）。

## 7. TDD 测试要点

| 先写的测试 | 覆盖分支 | Mock/工具 |
| --- | --- | --- |
| `test_render_simple_and_nested` | `{name}` 与 `{a[b]}` 扁平化渲染 | 纯函数断言 |
| `test_render_unknown_placeholder_kept` | 未命中占位符原样保留 | 纯函数断言 |
| `test_success_extracts_response_path` | 200 + `response_path="data.result"` → 输出 `response` 正确 | `httpx.MockTransport` 或 monkeypatch `httpx.request` |
| `test_no_response_path_returns_whole_body` | `response_path=None` → 整个 JSON | 同上 |
| `test_response_path_missing_yields_none` | 路径不存在 → `response=None` 且流程不崩 | 同上 |
| `test_headers_and_body_rendered` | headers/body 中占位符被渲染、body 以 JSON 发送 | 断言 transport 收到的请求 |
| `test_invalid_body_json_raises` | body 渲染后非法 JSON → `ValueError` 带节点名 | `pytest.raises` |
| `test_retry_on_500_then_success` | `max_retries=2`，前 2 次 500 后 200 → 成功；`tenacity.nap.sleep` 调用与退避值断言【AD-03】 | MockTransport 序列 + monkeypatch `tenacity.nap.sleep` |
| `test_no_retry_by_default` | `max_retries=0`、500 → 立即 `HTTPNodeError`，请求只发 1 次 | 计数 transport |
| `test_non_retryable_status_raises` | 404 且不在 `retry_on_status` → 立即 `HTTPNodeError` | 同上 |
| `test_mock_enabled_hit` | `mock_enabled=True` + 命中 key → 不发网络、返回 mock 数据 | 断言 transport 零调用 |
| `test_mock_enabled_miss_raises` | `mock_enabled=True` + 未命中 → 显式报错且**不回退真实调用**（H2/H6） | 断言 transport 零调用 |
| `test_mock_disabled_ignores_mock_responses` | `mock_enabled=False` 即使配了 `mock_responses` 也走真实分支 | 计数 transport |
| `test_mock_enabled_without_responses_invalid` | `validate_config` → `ValueError` | `pytest.raises` |
| `test_execution_log_no_secret_leak`（H6 守护） | headers 含 `Authorization` 时，ExecutionLog/错误消息不含其值 | 哑值断言 |

## 8. 验收标准 DoD

- [ ] `tests/unit/workflow/nodes/test_http_node.py` 全绿，零真实网络（所有测试经 MockTransport/monkeypatch）
- [ ] mock 与 retry 行为均由显式配置开关驱动，且各有正反两个方向的测试（H2 验证）
- [ ] `grep -n "cache\|lru_cache" app/workflow/nodes/http_node.py` 零命中（H4 守护）
- [ ] 无领域字段残留（`device` / `cmd` / `worker_result` / `short_memory` 不得出现）
- [ ] 重试为 tenacity 实现且测试 monkeypatch `tenacity.nap.sleep`（AD-03）
- [ ] `make lint`、`ruff format --check .`、`make typecheck` 零问题
- [ ] 通用 DoD G1-G8 + 红线自检表（CONTRACT §12）逐项勾选

## 9. 本阶段涉及的隐患修复

- **H2（落实）**：retry/mock 显式、由配置开关驱动、经测试；每个 except 显式处理（记录 + 重抛）。
- **H4（规范约束）**：默认不引入缓存（R10；未来若加缓存必须 maxsize + TTL + 开关 + 文档 + 命中/失效/淘汰测试）。
- **H6（部分落实）**：错误消息与日志不泄露 headers 敏感值；生产路径无硬编码 mock 回退。

## 10. 交付物清单

- `app/workflow/nodes/http_node.py`
- `tests/unit/workflow/nodes/test_http_node.py`
- `app/workflow/nodes/__init__.py`（追加导出 `HTTPNode` / `HTTPNodeConfig`）

## 11. 验收命令

```bash
uv run pytest tests/unit/workflow/nodes/test_http_node.py -m unit -v
make lint && ruff format --check . && make typecheck
grep -n "cache\|lru_cache" app/workflow/nodes/http_node.py        # 期望零命中（H4）
grep -nE "device|cmd|worker_result|short_memory" app/workflow/nodes/http_node.py   # 期望零命中（领域残留）
wc -l app/workflow/nodes/http_node.py   # 期望 < 280
```
