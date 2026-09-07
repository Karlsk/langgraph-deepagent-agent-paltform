# spec-18 · DELETE /api/v1/workflows/{id} 端点

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M3 | 后端 | 1 | spec-01、spec-17（删文件）、spec-19（鉴权） | **C6/H7**（delete_workflow 唯一删除入口）；AD-10 |

## 1. 目标

新增 `DELETE /api/v1/workflows/{workflow_id}`：经注册表**唯一删除入口** `delete_workflow`（C6/H7，四表同步）移除内存态，并调 spec-17 `delete_definition_yaml` 删除磁盘 YAML，保持「注册表 = 磁盘」一致。

## 2. 范围（In / Out）

- **In**：`api.py` 增 `delete_workflow_endpoint` handler；`registry.delete_workflow(id)`；`delete_definition_yaml(id)`；404（未知 id）；鉴权依赖（spec-19）。
- **Out**：软删 / 回收站（workflow 无软删需求，直接硬删）；批量删除。

## 3. 接口契约

```python
@router.delete("/workflows/{workflow_id}")
@limiter.limit(...)
async def delete_workflow_endpoint(
    workflow_id: str, request: Request,
    registry: WorkflowRegistry = Depends(get_registry),
    _admin=Depends(require_admin),           # spec-19
) -> JSONResponse: ...
```

处理顺序（早返回守卫）：

1. `removed = await run_in_threadpool(registry.delete_workflow, workflow_id)`（C6/H7 唯一入口）。
2. `delete_definition_yaml(workflow_id)`（spec-17；磁盘不存在容忍，不阻断）。
3. `removed is False` 且磁盘也无 → **404**（未知 id，信封 `{code: NOT_FOUND}`）。
4. 成功 → `data = null`，`code = 0`。

## 4. TDD · RED（测试先行）

扩展 `tests/unit/workflow/test_api.py::test_delete_workflow`：

- [ ] 已注册 workflow → DELETE 返回 `data=null`；`registry.has_workflow(id)` 为 False（四表同步，C6/H7）。
- [ ] DELETE 后 `delete_definition_yaml` 被调用（mock 断言），磁盘 YAML 移除。
- [ ] 未知 id → **404** + 信封；注册表 / 磁盘无副作用。
- [ ] 重复 DELETE（已删）→ 第二次 404（幂等边界）。
- [ ] `get_registry_stats` 计数在删除后 -1（不残留）。

## 5. GREEN（最小实现）

- 实现 handler：`delete_workflow` + `delete_definition_yaml` + 404 分支；经 `run_in_threadpool`（AD-10）。

## 6. REFACTOR

- 删除编排（内存 + 磁盘 + 404 判定）抽 `_remove_workflow(registry, workflow_id) -> bool`，供端点与潜在 CLI 复用。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿（含四表同步 / 404 / 幂等）；`make test` 通过，覆盖率不降。
- [ ] `make lint` / `ruff format --check` / `make typecheck` 全绿。
- [ ] grep：删除仅经 `registry.delete_workflow`（无直接操作内部 `_registry`/`_definitions`，C6/H7）。
- [ ] 限流 / DI / 鉴权（spec-19）/ 信封形态与既有一致（AD-10）。
- [ ] 提交 `feat(workflow): add DELETE /workflows/{id} endpoint`。

## 8. 交付物清单

- 改：`app/workflow/api.py`（`delete_workflow_endpoint` + `_remove_workflow`）
- 依赖：spec-17 `delete_definition_yaml`、spec-19 `require_admin`
- 改：`tests/unit/workflow/test_api.py`（删除用例）
