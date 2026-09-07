# spec-17 · YAML 落盘持久化（user 目录 + 白名单文件名 + 启动扫描）

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M3 | 后端 | 2 | spec-01、spec-16 | **S16**（safe_load/dump + fail-fast）；**H4**（无全局缓存）；D2；文件名白名单 |

## 1. 目标

新增持久化模块，把用户经画布保存的 workflow 定义落盘为 YAML（现状注册表内存态、重启即丢），并让 `build_registry` 启动时扫描 `examples/` + `user/` 两目录恢复，实现「注册表 = 磁盘」一致。

## 2. 范围（In / Out）

- **In**：新模块 `app/workflow/store.py`：`save_definition_yaml` / `delete_definition_yaml` / `user_workflow_dir`；文件名白名单校验；`yaml.safe_dump` 原子写（tmp + `os.replace`）；`build_registry`（组合根，AD-02 例外）扫描 examples + user 调 `load_definitions_from_dir`（S16）。
- **Out**：端点逻辑（spec-16/18 调用本模块）；DB 存储（§7.2 范围外，仍走文件）。

## 3. 接口契约（冻结）

```python
# app/workflow/store.py
_WORKFLOW_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # 文件名白名单（防路径穿越）

def user_workflow_dir() -> Path: ...                        # app/workflow/config/user/
def save_definition_yaml(definition: WorkflowDefinition) -> Path:
    """校验 workflow_id 白名单 -> yaml.safe_dump(去 execution_history, 保 ui_layout) -> 原子写 {id}.yaml。"""
def delete_definition_yaml(workflow_id: str) -> bool:
    """白名单校验 -> 删除 {id}.yaml；不存在返回 False。"""
```

- `build_registry(*, no_match_policy="raise") -> WorkflowRegistry`（组合根）：`load_definitions_from_dir(examples)` + `load_definitions_from_dir(user)` → 逐个 `register_workflow`（S16 fail-fast：任一文件损坏则启动报错）。
- dump 内容 = `definition.model_dump(mode="json", exclude={"execution_history", "operator_logs"?})`（保留 `ui_layout`；operator_logs 视需要保留），`yaml.safe_dump(..., allow_unicode=True, sort_keys=False)`。

## 4. TDD · RED（测试先行）

新增 `tests/unit/workflow/test_store.py`（用 `tmp_path` 作 user 目录，零真实 IO 副作用）：

- [ ] `save_definition_yaml` → 生成 `{id}.yaml`；`yaml.safe_load` 回读 == 定义（去 history、保 ui_layout）。
- [ ] **往返**：save → `load_definition_from_yaml` → `parse_definition` 通过（可再注册）。
- [ ] `workflow_id` 非法（含 `/`、`..`、空格、>64、空）→ 抛校验错，**不写文件**（路径穿越守卫）。
- [ ] 原子写：写入中途失败不留半截文件（tmp + replace 断言）。
- [ ] `delete_definition_yaml` 存在 → True 且文件消失；不存在 → False。
- [ ] `build_registry` 扫描 examples + user → 两目录 workflow 均注册；user 目录空/缺失 → 仅 examples，不报错（S16 空目录 warning）。
- [ ] user 目录含损坏 YAML → `build_registry` fail-fast 抛错（S16）。
- [ ] grep：仅出现 `yaml.safe_load` / `yaml.safe_dump`，无任何不安全加载器（S16）。

## 5. GREEN（最小实现）

- 实现白名单校验 + safe_dump 原子写 + delete；`build_registry` 串接两目录 `load_definitions_from_dir` + `register_workflow`。

## 6. REFACTOR

- 目录路径、dump 选项抽常量；白名单校验复用于 spec-16（id 一致）与 spec-18（delete）；确保 `store.py` 不 import `app.core.*`（AD-02，组合根 `build_registry` 为例外）。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿（往返 / 白名单 / 原子 / fail-fast / 空目录）；`make test` 通过，覆盖率不降。
- [ ] `make lint` / `ruff format --check` / `make typecheck` 全绿。
- [ ] grep：仅出现 `yaml.safe_load` / `yaml.safe_dump`，无任何不安全加载 / 转储调用（S16 硬门）；无 `lru_cache` / 模块级可变全局（H4）。
- [ ] 文件名白名单阻断路径穿越（`../`、绝对路径）；启动扫描 examples + user 恢复成功。
- [ ] 提交 `feat(workflow): persist user workflows to yaml and scan on registry build`。

## 8. 交付物清单

- 新：`app/workflow/store.py`
- 改：`app/workflow/__main__.py` / 组合根（`build_registry` 接入 user 目录扫描）
- 新：`app/workflow/config/user/.gitkeep`（用户定义目录）
- 新：`tests/unit/workflow/test_store.py`
