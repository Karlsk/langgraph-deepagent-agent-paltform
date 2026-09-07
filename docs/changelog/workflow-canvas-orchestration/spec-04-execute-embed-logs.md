# spec-04 · execute 响应内嵌 execution_logs（§7.1 方案 A）

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M1 | 后端 | 1 | spec-01 | api-trace §7.1；H6（redact）；AD-10 |

## 1. 目标

在既有 `POST /api/v1/workflows/{id}/execute` 的**成功响应** `metadata` 中内嵌脱敏后的 `execution_logs`，供前端展示逐节点执行过程（无需新增存储 / 端点）。

## 2. 范围（In / Out）

- **In**：`app/workflow/api.py` 成功分支 `metadata` 追加第五键 `execution_logs`；逐条 `ExecutionLog.model_dump(mode="json")` + `redact(max_len=500)` 脱敏；`_host_envelope_content` 折叠同步。
- **Out**：独立 trace 查询端点 / DB 持久化（§7.2 方案 B，范围外）；`registry.py` 内核**零改动**（RunResult 已含 execution_logs）。

## 3. 接口契约

成功响应（宿主信封 `data`）：

```jsonc
{
  "output": { /* EXP-G8 投影 */ },
  "metadata": {
    "workflow_id": "...", "success": true,
    "node_count": 3, "execution_time_ms": 120,
    "execution_logs": [
      {"node_name","node_type","timestamp","input_data","output_data","execution_time_ms","error"}
    ]
  }
}
```

- 仅**成功**响应内嵌；失败信封 `data=null`、错误走 `error`（不变）。
- 每条 log 的 `input_data`/`output_data` 经 `redact(max_len=500)` 脱敏（H6）。

## 4. TDD · RED（测试先行）

扩展 `tests/unit/workflow/test_api.py::test_execute_embeds_logs`：

- [ ] 成功执行（fake LLM / respx mock http）→ `metadata.execution_logs` 存在，条数 == 执行节点数。
- [ ] 每条 log 含全部 7 字段；`timestamp` 为 ISO 字符串（mode="json"）。
- [ ] 注入含密钥样式值（如 `sk-...`）的 input → 输出被 `redact` 脱敏，明文不出现（H6）。
- [ ] 超长字段（>500）被截断（max_len 生效）。
- [ ] 失败执行（节点抛错）→ 信封 `data` 为 null / 错误分支，**不含** execution_logs。
- [ ] `registry.py` 无改动（git diff 断言 / 既有 registry 测试仍绿）。

## 5. GREEN（最小实现）

- 成功分支构造 metadata 时追加 `execution_logs`：`[redact(log.model_dump(mode="json"), max_len=500) for log in run_result.execution_logs]`。
- `_host_envelope_content` 若折叠 metadata，确保新键随之一并进入 `data`。

## 6. REFACTOR

- 脱敏 + 序列化抽为 `_serialize_execution_logs(logs) -> list[dict]`，单点维护脱敏策略。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；脱敏 / 截断 / 失败不含 logs 三条关键断言通过。
- [ ] `make lint` / `ruff format --check` / `make typecheck` / `make test` 全绿。
- [ ] grep 确认脱敏走 `redact(...max_len=500)`，无明文密钥外泄路径（H6）。
- [ ] `registry.py` 零改动（内核稳定）；execute 限流 / DI / 信封形态不变（AD-10）。
- [ ] 提交 `feat(workflow): embed redacted execution_logs in execute response metadata`。

## 8. 交付物清单

- 改：`app/workflow/api.py`（成功分支 metadata + `_serialize_execution_logs`）
- 改：`tests/unit/workflow/test_api.py`（内嵌 / 脱敏 / 失败分支用例）
