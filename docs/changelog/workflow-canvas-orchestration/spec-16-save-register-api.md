# spec-16 · PUT /api/v1/workflows/{id} 全量注册端点

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M3 | 后端 | 2 | spec-01、spec-03（`_definition_view`）、spec-17（落盘）、spec-19（鉴权） | **S13**（原子替换）；**S15**（拒 python）；**S6**（构建期校验→422）；D2/D3；AD-10 |

## 1. 目标

新增 `PUT /api/v1/workflows/{workflow_id}`：接收前端传来的**结构化 JSON**（`WorkflowDefinition`），服务端 pydantic 校验 + 节点类型白名单 + 编译期校验，经 `register_workflow` **全量原子替换**注册（D3），并调用 spec-17 落盘 YAML（D2：YAML 由后端生成）。

## 2. 范围（In / Out）

- **In**：`api.py` 增 `save_workflow` handler；`parse_definition` 校验；`workflow_id` 路径/body 一致性；节点类型白名单（拒 `python`）；`register_workflow`（S13）；构建期异常 → 422；成功后调 spec-17 `save_definition_yaml`；返回 `_definition_view`。
- **Out**：YAML 落盘实现（spec-17）；鉴权依赖（spec-19 提供 `require_admin`）；SSRF（spec-20）；前端保存流程（spec-21）。

## 3. 接口契约

```python
@router.put("/workflows/{workflow_id}")
@limiter.limit(...)
async def save_workflow(
    workflow_id: str, request: Request,
    payload: dict = Body(...),
    registry: WorkflowRegistry = Depends(get_registry),
    _admin=Depends(require_admin),           # spec-19
) -> JSONResponse: ...
```

处理顺序（RORO + 早返回守卫）：

1. `parse_definition(payload)` → `ValidationError` → **422**（携带 pydantic 错误明细）。
2. `definition.workflow_id != workflow_id` → **422/400**（id 不一致）。
3. 任一 `node.type == "python"`（或不在 `{llm, http}` 白名单）→ **422**（S15：RCE 防线，服务端二次拒绝）。
4. `await run_in_threadpool(registry.register_workflow, definition)` → 构建期异常（悬空边 / 缺入口 / 非法条件 / 缺 default_edges，S6）→ **422**（AD-10 同步内核经线程池）。
5. `save_definition_yaml(definition)`（spec-17）→ 落盘失败 → **500**（不回滚注册，记录告警；见 §6）。
6. 成功 → 返回 `_definition_view(definition)`（spec-03）。

> **D6**：画布不传 `default_edges` / `no_match_policy`；`register_workflow` 用注册表全局 `no_match_policy`。含条件边且不穷尽者，构建期或运行期由引擎按全局策略处理，前端已在 spec-14 提示。

## 4. TDD · RED（测试先行）

新增 `tests/unit/workflow/test_api.py::test_save_workflow` + `tests/integration/workflow/`：

- [ ] 合法 definition（llm+http，含边）→ 200；`registry.has_workflow(id)` 为真；`_definition_view` 返回。
- [ ] 重复 PUT（同 id 改内容）→ 原子替换（S13）：旧节点/边被新定义覆盖，`get_registry_stats` 计数不翻倍。
- [ ] payload 非法（缺 `entry_point` / nodes 空）→ **422**，注册表无副作用。
- [ ] body.workflow_id ≠ path id → 422/400。
- [ ] 含 `type: python` 节点 → **422**（S15 守卫断言）。
- [ ] 悬空边 / entry_point 不存在 → 构建期 **422**（S6）。
- [ ] 成功后 spec-17 `save_definition_yaml` 被调用（mock 断言），YAML 落盘。
- [ ] 落盘失败（mock 抛错）→ 500，且已注册状态可回滚或告警（按 §6 决策断言）。

## 5. GREEN（最小实现）

- 按 §3 顺序实现 handler；白名单常量 `ALLOWED_NODE_TYPES = {"llm", "http"}`；构建异常映射 422。

## 6. REFACTOR

- 校验（parse / id 一致 / 白名单）抽 `_validate_definition_payload(payload, workflow_id) -> WorkflowDefinition`（守卫集中，happy path 最后）；落盘失败回滚策略统一（建议：落盘失败则 `delete_workflow` 回滚注册，保证「注册表 = 磁盘」一致）。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿（含 S13 替换 / S15 拒 python / S6 构建期 422）；`make test` 通过，覆盖率不降。
- [ ] `make lint` / `ruff format --check` / `make typecheck` 全绿。
- [ ] grep：无 `type == "python"` 放行路径；`register_workflow` 经 `run_in_threadpool`（AD-10）；无模块级缓存（H4）。
- [ ] 落盘走 spec-17（`yaml.safe_dump`，S16）；注册表与磁盘一致性策略明确并测试。
- [ ] 提交 `feat(workflow): add PUT /workflows/{id} full-replace register endpoint`。

## 8. 交付物清单

- 改：`app/workflow/api.py`（`save_workflow` + `_validate_definition_payload`）
- 依赖：spec-17 `app/workflow/store.py`、spec-19 `require_admin`
- 改：`tests/unit/workflow/test_api.py`；新：`tests/integration/workflow/test_save_workflow.py`
