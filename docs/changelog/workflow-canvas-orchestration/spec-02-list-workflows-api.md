# spec-02 · GET /api/v1/workflows 列表端点

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M1 | 后端 | 1 | spec-01 | AD-02 / AD-10 / H4；api-trace §4.1 |

## 1. 目标

新增只读端点 `GET /api/v1/workflows`，返回注册表内已注册 workflow 的摘要列表，供前端列表页展示。

## 2. 范围（In / Out）

- **In**：`app/workflow/api.py` 增 `list_workflows` handler + 投影；slowapi 限流；DI 注入 registry。
- **Out**：定义详情（spec-03）、执行（既有）、写路径（spec-16+）。

## 3. 接口契约

```python
@router.get("/workflows")
@limiter.limit(...)  # 与既有 execute 同策略
async def list_workflows(
    request: Request,
    registry: WorkflowRegistry = Depends(get_registry),
) -> JSONResponse: ...
```

- 数据源：`registry.list_workflows()` + `registry.get_registry_stats()` + `registry.get_workflow_definition(id)`。
- 投影：`data = [{"workflow_id", "node_count", "entry_point", "description"?}]`，按 `workflow_id` 升序。
- 宿主信封：经既有 `_project_to_host_envelope` → `{code, message, data}`。

## 4. TDD · RED（测试先行）

新增 `tests/unit/workflow/test_api.py::test_list_workflows`：

- [ ] 空注册表 → `data == []`，`code == 0`。
- [ ] 注册 2 个 workflow（fake definition）→ 返回 2 条，字段齐全，按 id 升序。
- [ ] `node_count` 等于定义节点数；`entry_point` 正确。
- [ ] 限流触发 → 429（若既有测试已覆盖限流机制，可引用同款断言）。
- [ ] 无模块级缓存：连续两次调用结果一致且实时反映注册表变化（H4）。

## 5. GREEN（最小实现）

- 实现 handler：读 registry → 构造摘要 list → `_project_to_host_envelope` 返回。
- 复用既有 DI `get_registry` 与信封投影，不新增全局状态。

## 6. REFACTOR

- 摘要投影抽为 `_workflow_summary(definition) -> dict`，与 spec-03 详情投影共享字段构造，避免重复。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；`make test` 通过，覆盖率不降。
- [ ] `make lint` / `ruff format --check` / `make typecheck` 全绿。
- [ ] grep `app/workflow/api.py` 无 `lru_cache` / 模块级可变全局（H4）。
- [ ] 端点有限流装饰器、DI 注入、宿主信封形态与既有一致（AD-10）。
- [ ] 提交 `feat(workflow): add GET /workflows list endpoint`。

## 8. 交付物清单

- 改：`app/workflow/api.py`（handler + `_workflow_summary`）
- 改：`tests/unit/workflow/test_api.py`（列表用例）
