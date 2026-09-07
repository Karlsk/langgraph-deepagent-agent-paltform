# spec-03 · GET /api/v1/workflows/{id} 查定义端点

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M1 | 后端 | 1 | spec-01、spec-02 | AD-02 / AD-10 / H4；S16（yaml.safe_dump） |

## 1. 目标

新增只读端点 `GET /api/v1/workflows/{workflow_id}`，返回单个 workflow 的完整定义，供前端设计器载入（`?format=json`）与 YAML 预览（`?format=yaml`，spec-22）。

## 2. 范围（In / Out）

- **In**：`app/workflow/api.py` 增 `get_workflow` handler；`format=json|yaml`（默认 json）；json 投影剔除 `execution_history`、保留 `ui_layout` 注解；yaml 用 `yaml.safe_dump` 生成文本。
- **Out**：注册 / 保存（spec-16）、执行（既有）。

## 3. 接口契约

```python
@router.get("/workflows/{workflow_id}")
@limiter.limit(...)
async def get_workflow(
    workflow_id: str,
    request: Request,
    format: Literal["json", "yaml"] = "json",
    registry: WorkflowRegistry = Depends(get_registry),
) -> JSONResponse: ...
```

- `format=json`：`data = definition.model_dump(mode="json", exclude={"execution_history"})`（保留 `ui_layout`）。
- `format=yaml`：`data = {"yaml_text": yaml.safe_dump(同上去除 history 的 dict, allow_unicode=True, sort_keys=False)}`。
- 未知 id → HTTP 404，信封 `{code: NOT_FOUND, message}`。

## 4. TDD · RED（测试先行）

新增 `tests/unit/workflow/test_api.py::test_get_workflow`：

- [ ] 已注册 workflow，`format=json` → 返回定义，含 `nodes/edges/state_schema/entry_point`，**不含** `execution_history`。
- [ ] `ui_layout` 注解键在 json 投影中保留（D4）。
- [ ] `format=yaml` → `data.yaml_text` 为字符串，`yaml.safe_load` 回读 == json 投影（往返一致）。
- [ ] 未知 id → 404 + 信封。
- [ ] yaml 生成走 `yaml.safe_dump`（grep 断言无 `yaml.dump` 默认 loader / 无 `yaml.load`）。

## 5. GREEN（最小实现）

- 实现 handler：`registry.get_workflow_definition(id)` → 无则 404；按 format 分支投影。
- yaml 分支复用 json 投影 dict 再 `safe_dump`，保证两格式同源。

## 6. REFACTOR

- 抽 `_definition_view(definition) -> dict`（去 history、保 ui_layout），json/yaml 两分支共用，避免投影漂移。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿；往返一致（json ↔ yaml）测试通过。
- [ ] `make lint` / `ruff format --check` / `make typecheck` / `make test` 全绿。
- [ ] grep 确认仅 `yaml.safe_dump` / `yaml.safe_load`（S16），无裸 `yaml.dump`/`yaml.load`。
- [ ] 404 与限流、DI、信封形态与既有端点一致（AD-10）。
- [ ] 提交 `feat(workflow): add GET /workflows/{id} definition endpoint`。

## 8. 交付物清单

- 改：`app/workflow/api.py`（handler + `_definition_view`）
- 改：`tests/unit/workflow/test_api.py`（详情 / yaml / 404 用例）
