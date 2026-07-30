# spec-06 图构建器（Phase 6）

## 1. 元信息

| 项 | 值 |
| --- | --- |
| Phase / 里程碑 | Phase 6 / M5 图可编译执行 |
| 人日估算 | **1.5** |
| 前置 spec | spec-02（`StateModelFactory`）、spec-03（`create_node`）、spec-04/05（至少一个具体节点可用于集成测试，建议两者皆完成）；**EXP-G3..G7 已闭环**（R-EXP：add_node 形态、add_conditional_edges path_map、START/END、compile/invoke、异常传播的实测先于编码） |
| 后续依赖方 | spec-07（`BuildResult` 消费者）、spec-09（示例 YAML 校对） |
| 涉及编号 | K6/K7、C1/C3/C5、R1/R2/R6/R9、H5（规范）/H6（部分）、异常契约 `ConditionNotMatchedError`、D1、AD-01/02/06 |

## 2. 目标

实现 `graph_builder.py` 的 `GraphBuilder`（K6 七步、C1 单一职责拆分、C5 干净校验、C3 条件路由器治理）：`_validate_definition` → `create_state_model` → `StateGraph` → `_add_nodes` → `_add_edges` → `set_entry_point` → `compile()`。本阶段结束时，YAML 到"可执行编译图"的链路打通。

## 3. 前置依赖

- spec 间依赖：spec-02 / spec-03 / spec-04 / spec-05。
- EXP 门禁（R-EXP）：EXP-G3（`add_node(RunnableLambda)` 输入形态与返回值合并语义）、EXP-G4（`add_conditional_edges` path_func/path_map 签名与行为）、EXP-G5（START/END 常量）、EXP-G6（`compile()` 产物 `invoke(input, config)` 形态）、EXP-G7（节点抛异常时 invoke 传播行为）结论为【吻合】；path_map 写法以 EXP-G4 实测结论为准。
- 代码库依赖：`app/workflow/state.py`、`app/workflow/nodes/factory.py`、`app/workflow/utils.py`、`app/workflow/models.py`（异常族）+ langgraph 1.0.2。
- 外部依赖：无网络（集成测试用 EchoNode，不调真实 LLM/HTTP）。

## 4. 适配决策（本 Phase 涉及的 AD 条目）

- **AD-01**：模块落位 `app/workflow/graph_builder.py`；测试落位 `tests/unit/workflow/test_graph_builder.py`、`tests/integration/workflow/test_graph_e2e.py`；示例落位 `app/workflow/config/examples/condition_branch.yaml`。
- **AD-02**：路由器调试输出走 structlog `logger.debug`（kwargs 传参），只记条件与命中目标，**不记完整 state**，禁止 `print`（C3/H6，替换原代码 `[ROUTER DEBUG]` print）。
- **AD-06**：langgraph 1.0.2 公开 API 形态以 EXP-G 实测为准（原文档 0.2-0.7 约束作废）；只依赖公开 API（`StateGraph` / `add_node` / `add_edge` / `add_conditional_edges` / `set_entry_point` / `compile` / `invoke`），不依赖内部属性（D1）。

## 5. 任务清单

- [ ] **TC1 _validate_definition（C5 干净校验）（0.25d）**
  - 内容：`workflow_id` 非空；`nodes` 非空；`entry_point` 非空且存在于节点名集合；每条边 `source`/`target` 属于节点名集合或字面量 `"END"`；**删除原代码对 dispatcher 的字符串字面量豁免**（本期无 dispatcher）；每个错误抛 `ValueError`，消息含 `workflow_id` 与具体出错对象
  - 产出文件：`app/workflow/graph_builder.py`（目标 < 300 行；**只含 GraphBuilder**，状态工厂在 `state.py`、注册表在 `registry.py`，落实 C1）
  - TDD 节奏：先写 §7 校验类 5 例（RED）→ 实现（GREEN）
- [ ] **TC2 七步 build_graph + _add_nodes/_add_edges（0.5d）**
  - 内容：`build_graph` 严格七步（代码注释逐步标注 1..7），返回 `BuildResult(compiled_graph, nodes_map)`（NamedTuple，避免 registry 偷窥 builder 内部状态）；`_add_nodes`：逐个 `create_node(node_def, operator_log=definition.operator_logs.get(name))` → `build_runnable()` → `graph.add_node(name, runnable)`，构建失败 `logger.error` 带节点名后重抛；`_add_edges`：按 source 分组为 `normal`/`conditional`，**同一 source 混存两类边 → `ValueError`**；无条件边 `add_edge(source, END if target == "END" else target)`；条件边 `add_conditional_edges(source, router, path_map)`，`path_map` 把 `"END"` 映射为 `END` 对象（1.x 形态以 EXP-G4/G5 实测为准）
  - 产出文件：`app/workflow/graph_builder.py`
- [ ] **TC3 条件路由器（C3 no-match 策略 + _parse_condition/_resolve_path）（0.375d）**
  - 内容：`_build_condition_router(source, conditional_edges, default_target)` 闭包捕获 source/边列表副本/default_target/`self.no_match_policy`；`router(state)`：`convert_state_to_dict` → 按声明顺序遍历 → 命中即 `logger.debug` 后返回 target；**全部未命中**：`raise` 策略 → `ConditionNotMatchedError`（消息含 source 与全部条件）；`default` 策略 → 构建期已校验 `default_target` 存在则返回之，否则构建期 `ValueError`；静态方法 `_parse_condition`（含 `==` → `(path, expected)`，两侧 strip、expected 去引号；否则 `(path, None)` 真值判断）与 `_resolve_path`（点路径逐层解析，非 dict 中途 → `None`）
  - 产出文件：`app/workflow/graph_builder.py`
- [ ] **TC4 单测 + e2e 集成 + condition_branch.yaml（0.375d）**
  - 内容：§7 全部用例（EchoNode 经 `register_node_type` 注册，autouse fixture 恢复注册表，D7）；集成测试：含一条条件分支的 3 节点图（入口 → 条件分流 → 两分支 → END）真实编译并跑通；示例 `condition_branch.yaml`（LLM `check` 节点 + 两条 `condition` 边 + HTTP 分支节点与另一分支节点）
  - 产出文件：`tests/unit/workflow/test_graph_builder.py`、`tests/integration/workflow/test_graph_e2e.py`、`app/workflow/config/examples/condition_branch.yaml`

## 6. 接口契约

见 CONTRACT §4.9（`BuildResult` / `GraphBuilder` 全部签名）、§5（`ConditionNotMatchedError` 场景）、§6 S6/S7（条件路由 no-match 两态、条件表达式解析）。

## 7. TDD 测试要点

| 先写的测试 | 覆盖分支 | Mock/工具 |
| --- | --- | --- |
| `test_validate_empty_workflow_id` | `workflow_id=""` → `ValueError` | 构造 definition |
| `test_validate_empty_nodes` | `nodes=[]` → `ValueError` | 同上 |
| `test_validate_entry_point_missing` | entry_point 不在节点集 → `ValueError` 含名字 | 同上 |
| `test_validate_edge_endpoint_missing` | 边 source/target 悬空 → `ValueError`；`target="END"` 合法 | 参数化 |
| `test_validate_no_dispatcher_exemption`（守护） | 名为 dispatcher 的普通节点不再有任何豁免，校验一视同仁 | 构造 type="dispatcher" 的 EchoNode 定义 |
| `test_build_two_node_linear` | A→B→END 编译成功并可 `invoke`，终态含两节点输出 | EchoNode + 真实 langgraph |
| `test_mixed_conditional_and_normal_raises` | 同 source 混存两类边 → `ValueError` | 构造 definition |
| `test_router_equality_branch` | `condition: "check_result.status == 'ok'"` → 状态值匹配走 ok 分支 | EchoNode 写 `{"status": "ok"}` |
| `test_router_truthiness_branch` | 纯路径条件 `flag` → 真值命中 | EchoNode 写 `{"flag": True}` |
| `test_router_no_match_raises` | 全不命中 + `no_match_policy="raise"` → `ConditionNotMatchedError` 含 source 与条件 | `pytest.raises` |
| `test_router_no_match_default` | `no_match_policy="default"` + `default_edges={"check": "fallback"}` → 走 fallback | 真实执行 |
| `test_router_default_missing_at_build` | policy=default 但未提供 default_edges → 构建期 `ValueError` | `pytest.raises` |
| `test_router_no_print_no_full_state`（H6 守护） | `caplog`/`capsys`：DEBUG 日志不含完整 state dump；stdout 为空 | `caplog`/`capsys` |
| `test_parse_condition_variants` | `"a.b == 'x'"` / `"a.b==\"y\""` / `"flag"` 三种解析 | 纯函数 |
| `integration: test_condition_branch_e2e` | 入口 → 条件分流 → 两分支 → END 真实编译跑通，命中路径节点输出齐全 | EchoNode + 真实 langgraph |

## 8. 验收标准 DoD

- [ ] `tests/unit/workflow/test_graph_builder.py` 与 `tests/integration/workflow/test_graph_e2e.py` 全绿；含条件分支的 3 节点图真实编译并跑通
- [ ] `graph_builder.py` 不含 `WorkflowRegistry` / `StateModelFactory` 实现（C1 守护：只 import 使用）
- [ ] `grep -n "print(" app/workflow/graph_builder.py` 零命中（C3 守护，ruff `T20` 兜底）
- [ ] 无 `dispatcher` / `triage` / `subgraph` 字面量豁免（C5 守护）
- [ ] 七步顺序与 K6 一致（代码注释逐步标注 1..7）
- [ ] `make lint`、`ruff format --check .`、`make typecheck` 零问题
- [ ] 通用 DoD G1-G8 + 红线自检表（CONTRACT §12）逐项勾选

## 9. 本阶段涉及的隐患修复

- **H5（规范约束）**：`GraphBuilder` **不持有、不快照任何注册表**；构造器无 registry 参数，构建期只消费传入的 `WorkflowDefinition`。需要引用其它工作流的能力（未来 AgentNode）必须运行期惰性解析。
- **H6（部分落实）**：路由器日志只记条件与命中目标，不落完整 state；无 print 调试残留。

## 10. 交付物清单

- `app/workflow/graph_builder.py`
- `tests/unit/workflow/test_graph_builder.py`
- `tests/integration/workflow/test_graph_e2e.py`
- `app/workflow/config/examples/condition_branch.yaml`

## 11. 验收命令

```bash
uv run pytest tests/unit/workflow/test_graph_builder.py -m unit -v
uv run pytest tests/integration/workflow/test_graph_e2e.py -m integration -v
make lint && ruff format --check . && make typecheck
grep -n "print(" app/workflow/graph_builder.py                          # 期望零命中（C3）
grep -nE "dispatcher|triage|subgraph" app/workflow/graph_builder.py     # 期望零命中（C5）
wc -l app/workflow/graph_builder.py   # 期望 < 300
```
