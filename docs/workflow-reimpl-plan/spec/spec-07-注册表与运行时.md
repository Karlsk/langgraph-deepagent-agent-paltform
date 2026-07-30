# spec-07 注册表与运行时（Phase 7）

## 1. 元信息

| 项 | 值 |
| --- | --- |
| Phase / 里程碑 | Phase 7 / M6 运行时可用 |
| 人日估算 | **2.0** |
| 前置 spec | spec-06（`GraphBuilder.build_graph` 返回 `BuildResult`）、spec-03（`set_run_collector` / `RunLogCollectorLike`）；**EXP-G6/G7、EXP-C2 已闭环**（R-EXP：invoke 输入输出形态、异常传播、tags 透传的实测先于编码） |
| 后续依赖方 | spec-08（CLI/API 装配）、spec-09（并发压测固化） |
| 涉及编号 | K8、C6、R1/R6/R9、H1（落实）/H3（落实）/H5（规范）/H7（落实）、异常契约 `WorkflowNotFoundError`、D3/D7、AD-01/02 |

## 2. 目标

实现 `registry.py`：线程安全的 `WorkflowRegistry`（H1：per-workflow RLock 串行化 `execute_workflow`）、运行级日志收集（H1/H3：以 run-scoped 收集器替代"在共享节点实例上就地 mutate"）、统一 `delete_workflow`（H7/C6：三张内部映射同增同删）、YAML 目录加载 `load_definitions_from_dir`。这是全部隐患修复密度最高的阶段。

## 3. 前置依赖

- spec 间依赖：spec-06、spec-03。
- EXP 门禁（R-EXP）：EXP-G6（`invoke(input, config)` 输入输出形态与 config 传播）、EXP-G7（节点抛异常时 invoke 的传播行为——决定 `execute_workflow` 的 except 包装层）、EXP-C2（tags 透传，日志归因辅助）结论为【吻合】。
- 代码库依赖：`app/workflow/graph_builder.py`、`app/workflow/nodes/base.py`（ContextVar 接口）、`app/workflow/models.py`。
- 外部依赖：无网络（测试用 EchoNode）。

## 4. 适配决策（本 Phase 涉及的 AD 条目）

- **AD-01**：模块落位 `app/workflow/registry.py`；测试落位 `tests/unit/workflow/test_registry.py`、`tests/integration/workflow/test_concurrency.py`、`tests/integration/workflow/test_log_collection.py`。
- **AD-02**：日志用 structlog；`logger.error` 带 `workflow_id`/`run_id` kwargs（R6），`logger.exception()` 留 traceback。
- **AD-08**：并发压测标记 `@pytest.mark.integration`；EchoNode 等公共夹具来自 `tests/conftest.py`。

## 5. 任务清单

- [ ] **TC1 RunResult / RunLogCollector（0.25d）**
  - 内容：`@dataclass(frozen=True) RunResult`（`workflow_id`/`run_id`/`output`/`execution_logs`/`started_at`/`finished_at` + `duration_ms` property，不可变，每次运行一个）；`RunLogCollector`（实现 `RunLogCollectorLike`）：`__init__(run_id)`、`add(log)`（内部加锁，收集本身线程安全）、`collect()`（按 `timestamp` 排序的副本）
  - 产出文件：`app/workflow/registry.py`（目标 < 350 行；超出则把查询接口拆 `_queries` 混入，但优先保持单文件）
  - TDD 节奏：先写 §7 前 2 项（RED）→ 实现（GREEN）
- [ ] **TC2 WorkflowRegistry 注册/查询/delete_workflow（H7）（0.5d）**
  - 内容：内部状态 `_registry` / `_definitions` / `_nodes_map` / `_run_locks` / `_meta_lock` / `_builder`；`register_workflow`：先 `_ensure_operator_logs(definition)` 补空 `OperatorLog`（**通用空 schema，无任何节点类型特判**——替代原 `auto_generate_operator_logs` 领域分支）→ `BuildResult` → 三映射**同增**；重复 `workflow_id` = 更新语义（`_meta_lock` 下先整体删除旧映射再写入）；`delete_workflow`（C6/H7）：**唯一删除入口**，三映射 + 锁表条目同删，返回是否成功，**不提供 `unregister_workflow`**；查询接口全套（`get_workflow` 不存在 → `WorkflowNotFoundError` / `has_workflow` / `list_workflows` / `get_workflow_definition` / `get_operator_logs` / `get_operator_log_by_node` / `get_execution_history` / `get_node_execution_history` / `get_node_by_name` / `get_registry_stats`）
  - 产出文件：`app/workflow/registry.py`
- [ ] **TC3 execute_workflow（H1 锁 + H3 ContextVar 收集）（0.5d）**
  - 内容：① `get_workflow`（不存在 → `WorkflowNotFoundError`）→ ② `_get_run_lock`（`_meta_lock` 下惰性创建 RLock），`with lock:` 串行化同一 workflow → ③ `run_id = uuid.uuid4().hex`；`collector = RunLogCollector(run_id)`；`token = set_run_collector(collector)` → ④ `try: output = workflow.invoke(input_data)`；except → `logger.error` 带 workflow_id/run_id 后重抛（R6，异常形态以 EXP-G7 实测为准）→ ⑤ `finally:` token reset（ContextVar 不泄漏）→ ⑥ `logs = collector.collect()` 写入 `definition.execution_history`（**只保留最近一次运行**，文档化该决策）→ 返回 `RunResult`；⑦ **不得调用 `node.clear_execution_history()` 作为收集手段**（H1 根因之一）；锁粒度说明写入类 docstring（同 workflow 串行、不同 workflow 并行；ContextVar 按线程/任务隔离，锁 + 收集器双保险，D3）
  - 产出文件：`app/workflow/registry.py`
- [ ] **TC4 load_definitions_from_dir（0.25d）**
  - 内容：模块级函数：递归扫描 `*.yaml` / `*.yml`，按文件名排序解析；单文件失败 → `ValueError` 带文件路径上下文（fail fast）；空目录返回 `[]` 并 `logger.warning`
  - 产出文件：`app/workflow/registry.py`
- [ ] **TC5 单测 + 并发压测（16×64）+ 日志收集集成（0.5d）**
  - 内容：§7 全部用例；并发压测 `test_concurrent_same_workflow_logs_isolated`（16 线程 × 64 次）与 `test_logs_cover_all_executed_nodes`（3 节点含条件分支）落在 integration 文件
  - 产出文件：`tests/unit/workflow/test_registry.py`、`tests/integration/workflow/test_concurrency.py`、`tests/integration/workflow/test_log_collection.py`

## 6. 接口契约

见 CONTRACT §4.10（`RunResult` / `RunLogCollector` / `WorkflowRegistry` / `load_definitions_from_dir` 全部签名）、§5（`WorkflowNotFoundError` 场景）、§6 S10/S11/S12/S13（execute 锁与收集器语义、execution_history 单槽位、delete 三表同增同删、re-register 原子替换）。

## 7. TDD 测试要点

| 先写的测试 | 覆盖分支 | Mock/工具 |
| --- | --- | --- |
| `test_register_and_get` | 注册 → `has_workflow`/`get_workflow`/`list_workflows` 一致 | EchoNode 工作流 |
| `test_delete_removes_all_three_maps`（H7） | `delete_workflow` 后 `_registry`/`_definitions`/`_nodes_map` 均无该 id | 直接断言内部映射（白盒一次，回归守护） |
| `test_delete_absent_returns_false` | 删除不存在 id → `False` 且不抛错 | — |
| `test_no_unregister_api`（守护） | `hasattr(registry, "unregister_workflow") is False` | 反射断言 |
| `test_re_register_replaces_atomically` | 同 id 二次注册后查询接口全部指向新定义 | 两版 definition |
| `test_execute_returns_run_result` | 输出正确、`run_id` 为 32 位 hex、`duration_ms >= 0` | EchoNode |
| `test_execute_unknown_workflow_raises` | → `WorkflowNotFoundError` | `pytest.raises` |
| `test_collector_reset_after_run` | 执行后 `get_run_collector() is None`（ContextVar 不泄漏） | 断言 |
| `test_execution_history_keeps_last_run` | 连续执行 2 次 → `get_execution_history` 只含第 2 次日志 | 断言长度与 run 内容 |
| `test_load_definitions_from_dir` | 目录含 2 个合法 yaml + 1 个 txt → 返回 2 个、排序稳定 | `tmp_path` |
| `test_load_definitions_bad_file_fails_fast` | 坏 yaml → `ValueError` 消息含文件路径 | `tmp_path` |
| `test_load_definitions_empty_dir` | 空目录 → `[]` | `tmp_path` |
| `integration: test_concurrent_same_workflow_logs_isolated`（H1） | 16 线程 × 64 次并发 `execute_workflow` 同一 id → 每个 `RunResult.execution_logs` 恰好覆盖全部被执行节点各 1 条、run_id 互异、output 与日志互不串扰 | `ThreadPoolExecutor` + EchoNode |
| `integration: test_logs_cover_all_executed_nodes`（H3） | 3 节点（含条件分支）运行 → `execution_logs` 的 node_name 集合 == 实际路径上全部节点 | EchoNode + 条件图 |

## 8. 验收标准 DoD

- [ ] 单元与集成测试全绿；并发压测（`test_concurrent_same_workflow_logs_isolated`）在 `pytest -m integration` 下稳定通过（重复运行 5 次无偶发失败）
- [ ] `grep -n "clear_execution_history" app/workflow/registry.py` 零命中（H1 守护：运行时不靠清理共享实例收集日志）
- [ ] `delete_workflow` 是三张映射的唯一删除路径（代码审查 + H7 回归测试）
- [ ] `execute_workflow` 内 ContextVar 设置/复位成对出现（`try/finally`）
- [ ] H1/H3/H7 各有具名测试作为回归守护
- [ ] `make lint`、`ruff format --check .`、`make typecheck` 零问题
- [ ] 通用 DoD G1-G8 + 红线自检表（CONTRACT §12）逐项勾选

## 9. 本阶段涉及的隐患修复

- **H1（落实）**：per-workflow `RLock` 串行化 `execute_workflow`；日志收集改走 run-scoped `RunLogCollector`（ContextVar），与共享节点实例解耦。
- **H3（落实）**：收集器覆盖"本次运行实际执行的所有节点"，与节点创建方式无关；接口为未来子图场景预留（任何走 `log_execution` 的节点自动被收集）。
- **H7（落实）**：`unregister_workflow` 移除，`delete_workflow` 保证 `_registry`/`_definitions`/`_nodes_map` 同增同删。
- **H5（规范约束）**：registry 在**运行期**解析图对象，无任何构建期注册表快照。

## 10. 交付物清单

- `app/workflow/registry.py`
- `tests/unit/workflow/test_registry.py`
- `tests/integration/workflow/test_concurrency.py`
- `tests/integration/workflow/test_log_collection.py`

## 11. 验收命令

```bash
uv run pytest tests/unit/workflow/test_registry.py -m unit -v
uv run pytest tests/integration/workflow/test_concurrency.py tests/integration/workflow/test_log_collection.py -m integration -v
for i in 1 2 3 4 5; do uv run pytest tests/integration/workflow/test_concurrency.py -m integration -q || break; done   # 5 次稳定性
make lint && ruff format --check . && make typecheck
grep -n "clear_execution_history" app/workflow/registry.py   # 期望零命中（H1）
wc -l app/workflow/registry.py   # 期望 < 350
```
