# spec-02 State 自动生成（Phase 2）

## 1. 元信息

| 项 | 值 |
| --- | --- |
| Phase / 里程碑 | Phase 2 / M2 State 可生成 |
| 人日估算 | **1.0** |
| 前置 spec | spec-01（`StateFieldSchema`）；**EXP-G1/G2/G8 已闭环**（R-EXP 门禁：pydantic state schema、reducer channel 推断、pydantic 2.11 边界的实测结论先于编码） |
| 后续依赖方 | spec-06（GraphBuilder 消费状态模型）、spec-03（utils 测试用动态模型） |
| 涉及编号 | K2/K3、C2、R2/R8/R9、D2、`test_no_hardcoded_field_names` 守护 |

## 2. 目标

实现 `state.py` 的 `StateModelFactory.create_state_model`（K2/K3），用 `pydantic.create_model` 把 `state_schema` 动态合成为带 reducer channel 的状态模型。本阶段是契约清理 **C2** 的主战场：**reducer 只由 YAML 显式声明驱动**，彻底移除原代码对 `circle_conclusions` / `planner_result` 等领域字段名的硬编码特判。

## 3. 前置依赖

- spec 间依赖：spec-01（`StateFieldSchema` 类型定义）。
- EXP 门禁（R-EXP）：EXP-G1（pydantic 模型作 state schema）、EXP-G2（Annotated reducer → channel）、EXP-G8（langgraph 1.0.2 × pydantic 2.11 边界）结论为【吻合】；若为【偏差】须先完成 CONTRACT §11 变更。
- 代码库依赖：仅 `app/workflow/models.py` + pydantic 2.11 + langgraph 1.0.2（集成测试）。
- 外部依赖：无网络。

## 4. 适配决策（本 Phase 涉及的 AD 条目）

- **AD-01**：`app/workflow/state.py`；测试落位 `tests/unit/workflow/test_state.py`、`tests/integration/workflow/test_state_channels.py`。
- **AD-06/AD-11**：langgraph 1.x channel 行为以 EXP-G 实测为准；`create_model` 处不可避免的类型放宽最小化并注释（pyright）。
- **AD-08**：集成测试标记 `@pytest.mark.integration`。

## 5. 任务清单

- [ ] **TC1 state.py（TYPE_MAP/_last/reducer 规则/history 注入/extra=allow）（0.5d）**
  - 内容：按 CONTRACT §4.3 实现 `TYPE_MAP`（10 个键）、`_last`、`StateModelFactory.create_state_model`；模块级基类 `_DynamicStateBase(BaseModel): model_config = ConfigDict(extra="allow")`，`create_model("DynamicWorkflowState", **field_definitions, __base__=_DynamicStateBase)`（v2 写法陷阱见 D2）
  - 产出文件：`app/workflow/state.py`（目标 < 150 行；`create_state_model` 函数体 < 50 行，必要时抽私有辅助函数，R8）
  - 冻结规则（C2）：`reducer=="add"` → `(Annotated[list, operator.add], Field(default_factory=list, description=...))`；`reducer=="last"` → `(Annotated[py_type, _last], Field(default=default_val, description=...))`；未声明 → `(py_type, Field(default=default_val, description=...))`；**未知 type → `ValueError` 并列出支持类型**（fail fast，R9）；**禁止任何按字段名的 if 分支**
  - history 注入（K2）：未显式声明 `history` 时注入 `(Annotated[list, operator.add], Field(default_factory=list, description="Auto-injected execution history (reducer=add)"))`；显式声明优先
  - 修订备注【EXP-G8 决策，2026-07-30】：依据 EXP-G8 实测（未声明键被 langgraph 静默丢弃，`extra="allow"` 对 channel 写入面无效）+ 方案 1 决策：`create_state_model` 需额外支持构建期按 `definition.nodes` 预声明 `{node_name}_result: (Any, None)` 字段（LastValue channel，承载 S4 双写）；除此之外的任意 extra 键不支持写入运行期 state，YAML `state_schema` 必须显式声明。入参形态（节点名清单入参或由 GraphBuilder 组装）在本 spec 实施时定稿；详见 CONTRACT §4.3 修订备注与 `api-exploration-1x.md` G8 行
  - TDD 节奏：先写 §7 单测前 4 项（RED）→ 实现（GREEN）→ 重构
- [ ] **TC2 单测 8 例（0.25d）**
  - 内容：§7 单测全部用例（含守护测试 `test_no_hardcoded_field_names` 参数化：`circle_conclusions` / `planner_result` 等字段名行为与任意普通字段完全一致）
  - 产出文件：`tests/unit/workflow/test_state.py`
- [ ] **TC3 langgraph 1.x channel 集成测试 2 例（0.25d）**
  - 内容：`tests/integration/workflow/test_state_channels.py`：把生成的状态模型真正喂给 `langgraph.StateGraph`，验证 add channel 合并与 extra 键穿透（用例见 §7 末两行）；与 EXP-G2/G8 的 characterization 结果互相印证
  - 产出文件：`tests/integration/workflow/test_state_channels.py`

## 6. 接口契约

见 CONTRACT §4.3（`TYPE_MAP` / `_last` / `StateModelFactory.create_state_model` 签名与 docstring 要点）与 §6 S1/S2/S14（reducer 三态、history 注入、extra=allow）。

## 7. TDD 测试要点

| 先写的测试 | 覆盖分支 | Mock/工具 |
| --- | --- | --- |
| `test_plain_field_last_value` | 无 reducer 字段：两次赋值后者覆盖 | 实例化模型 |
| `test_reducer_add_annotation` | `reducer="add"` 字段：`typing.get_type_hints(include_extras=True)` 能取到 `operator.add` reducer 元数据 | `typing` 内省 |
| `test_reducer_last_annotation` | `reducer="last"` 字段：reducer 元数据为 `_last` | 内省 |
| `test_unknown_type_raises` | `type: "date"` → `ValueError` 且消息列出支持类型 | `pytest.raises` |
| `test_extra_fields_allowed` | 实例化后写未声明字段不报错（extra=allow） | 模型赋值 |
| `test_history_auto_injected` | 未声明 history → 模型含 history 且为 add channel、默认 `[]` | 内省 |
| `test_explicit_history_overrides` | 显式声明 `history`（如 `reducer="last"`）→ 不被自动注入覆盖 | 内省 |
| `test_no_hardcoded_field_names`（守护测试） | 对 `circle_conclusions` / `planner_result` 等字段名创建模型，行为与任意普通字段完全一致（无特判） | 参数化 |
| `integration: test_add_channel_merges` | 生成模型 → `StateGraph(model)` → 两个节点各返回 `{"history": [x]}` → 终态 `history == [x, y]` | 真实 langgraph |
| `integration: test_extra_key_flows_through` | 节点写未声明键 `foo` → 终态含 `foo` | 真实 langgraph |

修订备注【EXP-G8 决策，2026-07-30】：`test_extra_key_flows_through` 原口径（终态含未声明键 `foo`）已被 EXP-G8 实测推翻，实施时改为断言：预声明的 `{node_name}_result` 键可写入并在终态保留，未声明键被静默丢弃（与 `tests/integration/workflow/test_exploration_graph.py` 的 test_g8_* 互印）；`test_extra_fields_allowed` 仅验模型自身宽容校验，口径不变。§8 DoD 中“`extra=allow` 生效”相应按本备注口径验收。

## 8. 验收标准 DoD

- [ ] `tests/unit/workflow/test_state.py` 与 `tests/integration/workflow/test_state_channels.py` 全绿
- [ ] `grep -nE "circle_|planner_|worker_|reflector_|current_node" app/workflow/state.py` 零命中（C2 守护）
- [ ] langgraph 集成测试证明 `add` channel 合并、`extra=allow` 生效（与 EXP-G2/G8 实测一致）
- [ ] `create_state_model` 函数体 < 50 行（R8）
- [ ] `make lint`、`ruff format --check .`、`make typecheck` 零问题
- [ ] 通用 DoD G1-G8 + 红线自检表（CONTRACT §12）逐项勾选

## 9. 本阶段涉及的隐患修复

无 H 编号隐患；本阶段落实清理项 **C2**（移除硬编码领域字段名分支，reducer 只由显式声明驱动 + 明确默认策略），守护测试 `test_no_hardcoded_field_names` 防回归（R2）。

## 10. 交付物清单

- `app/workflow/state.py`
- `tests/unit/workflow/test_state.py`
- `tests/integration/workflow/test_state_channels.py`

## 11. 验收命令

```bash
uv run pytest tests/unit/workflow/test_state.py -m unit -v
uv run pytest tests/integration/workflow/test_state_channels.py -m integration -v
make lint && ruff format --check . && make typecheck
grep -nE "circle_|planner_|worker_|reflector_|current_node" app/workflow/state.py   # 期望零命中
wc -l app/workflow/state.py   # 期望 < 150
```
