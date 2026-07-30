# spec-03 节点基础设施（Phase 3）

## 1. 元信息

| 项 | 值 |
| --- | --- |
| Phase / 里程碑 | Phase 3 / M3 节点契约闭环 |
| 人日估算 | **1.0** |
| 前置 spec | spec-01（`NodeDefinition` / `ExecutionLog` / `OperatorLog` 模型）；spec-02 非硬依赖但按统一口径串行；**EXP-C1/C2 已闭环**（R-EXP：RunnableLambda 签名与 tags 透传实测先于 `wrap_runnable` 编码） |
| 后续依赖方 | spec-04 / spec-05（并行窗口）、spec-06、spec-07（H3 预埋接口的消费者） |
| 涉及编号 | K4/K5/K9、C4/C7、R3/R4、H3（预埋）、D7 |

## 2. 目标

建立节点体系的三块地基：`nodes/base.py`（`BaseNode` 抽象契约，K4）、`nodes/factory.py`（插件式工厂，K5）、`utils.py`（两个状态工具，K9/C4/C7）。同时为 H3（运行级日志收集）预埋收集器接口（Protocol + ContextVar），为 spec-07 做铺垫。

## 3. 前置依赖

- spec 间依赖：spec-01（模型层）。
- EXP 门禁（R-EXP）：EXP-C1（RunnableLambda func 签名规则）、EXP-C2（`with_config(tags=...)` 透传）结论为【吻合】。
- 代码库依赖：`app/workflow/models.py` + langchain-core 1.0.4（`Runnable` / `RunnableLambda`）；`nodes/*` 不得 import `registry`/`graph_builder`（依赖红线 2）；`utils.py` 不得 import LLM/HTTP 客户端库（依赖红线 3，C7）。
- 外部依赖：无网络。

## 4. 适配决策（本 Phase 涉及的 AD 条目）

- **AD-01**：模块落位 `app/workflow/nodes/{base,factory}.py`、`app/workflow/utils.py`；测试落位 `tests/unit/workflow/nodes/` 与 `tests/unit/workflow/test_utils.py`。
- **AD-02**：需要日志处用 structlog（本阶段基本无日志点）。
- **AD-04**：factory 与节点相关导入一律**顶层导入**（覆盖原文档"函数内延迟导入"口径）；内置分支直接顶层 import `LLMNode`/`HTTPNode`（来自 spec-04/05 模块；本阶段先写工厂骨架时以占位引用 + 04/05 完成后接线的顺序处理，或按 04/05 完成后再跑工厂内置分支测试的节奏执行）。
- **AD-08**：`tests/conftest.py` 增加 autouse fixture `restore_node_registry`（快照-恢复 `factory._NODE_REGISTRY`，D7）。

## 5. 任务清单

- [ ] **TC1 nodes/base.py（BaseNode/RunLogCollectorLike/ContextVar）（0.375d）**
  - 内容：按 CONTRACT §4.4 实现 `RunLogCollectorLike`（`@runtime_checkable Protocol`）、`_RUN_COLLECTOR` ContextVar（名称 `"workflow_run_collector"`）与 `set_run_collector`/`get_run_collector`；`BaseNode(ABC)` 全套（构造器、两个抽象方法、`log_execution` 双写、`get_execution_history` 返回副本、`clear_execution_history` 仅调试、`wrap_runnable` 统一 `RunnableLambda(func).with_config(tags=[self.name])`、`__str__`）
  - 产出文件：`app/workflow/nodes/base.py`（目标 < 200 行，**不含任何网络/模型调用逻辑**）
  - TDD 节奏：先写 §7 前 3 项（RED）→ 实现（GREEN）
- [ ] **TC2 nodes/factory.py（注册表 + 双内置分支）（0.25d）**
  - 内容：按 CONTRACT §4.5 实现 `_NODE_REGISTRY`、`register_node_type`（非 BaseNode 子类 → `TypeError`）、`list_node_types`、`create_node`（插件优先；未传 `operator_log` 时构造空 `OperatorLog`；内置兜底**恰好 2 个分支**：`type in ("llm","LLM")` → `LLMNode(LLMConfig(**definition.config))`、`type in ("http","HTTP")` → `HTTPNode(原 dict)`；未知类型 `ValueError` 列出 `list_node_types()` 并提示注册）；**无 `workflow_registry` 参数**（H5）
  - 产出文件：`app/workflow/nodes/factory.py`（目标 < 120 行）、`app/workflow/nodes/__init__.py`（导出 `BaseNode`/`register_node_type`/`create_node`；内置类型在 spec-04/05 模块内自注册，内置分支作双保险）
  - 注意：内置分支引用的 `LLMNode`/`HTTPNode` 在 spec-04/05 交付后可用；本卡先把分支写好，相关测试随 spec-04/05 完成转绿（或用占位 FakeNode 先行验证注册表路径）
- [ ] **TC3 utils.py 两工具（0.25d）**
  - 内容：按 CONTRACT §4.6 实现 `convert_state_to_dict`（pydantic→`model_dump()` / dict 直通 / 其它→`{}`）与 `map_output_to_state`（双写 + history **增量**追加规则，C4 修正写入 docstring）；C7 瘦身：只有这两个公开函数
  - 产出文件：`app/workflow/utils.py`（目标 < 150 行）
- [ ] **TC4 三个测试文件 + conftest fixture（0.125d）**
  - 内容：§7 全部用例；`tests/conftest.py` 增加 `restore_node_registry` autouse fixture（D7）
  - 产出文件：`tests/unit/workflow/nodes/test_base.py`、`tests/unit/workflow/nodes/test_factory.py`、`tests/unit/workflow/test_utils.py`、`tests/conftest.py`

## 6. 接口契约

见 CONTRACT §4.4（base.py）、§4.5（factory.py）、§4.6（utils.py）与 §6 S3/S4/S5（history 增量、双写、节点进出）。

`map_output_to_state` 的 history 规则冻结复述（C4）：**仅当** `state` 含 `history` 键且其值为 `list`、且 `node_output` 自身未写 `history`、且 `history_increment=True` 时，追加增量 `[entry]`（`entry = f"{node_name}: {str(node_output)[:100]}..."`）；因自动注入的 history 是 `reducer="add"` channel，**只返回增量 `[entry]`，不返回全量**（原代码全量返回在 add reducer 下会造成历史翻倍，必须修正）。

## 7. TDD 测试要点

| 先写的测试 | 覆盖分支 | Mock/工具 |
| --- | --- | --- |
| `test_base_node_requires_abstract_impls` | 直接实例化 `BaseNode` → `TypeError` | `pytest.raises` |
| `test_log_execution_writes_both_sinks` | 设置假收集器（实现 `add`）→ `log_execution` 后实例历史与收集器各得 1 条 | 假收集器 + `set_run_collector` |
| `test_get_execution_history_returns_copy` | 返回值被外部 mutate 不影响内部 | 列表操作 |
| `test_register_and_create_custom_node` | 注册 `FakeNode` → `create_node(NodeDefinition(name="fake_node", type="fake", config={}), operator_log=None)` 产出实例 | 测试专用 `FakeNode(BaseNode)` |
| `test_register_non_subclass_raises` | 注册非 BaseNode 类 → `TypeError` | `pytest.raises` |
| `test_create_node_unknown_type` | 未知类型 → `ValueError`，消息含 `list_node_types()` | `pytest.raises(match=...)` |
| `test_registry_restore_fixture`（守护） | 测试内注册的类型在下一个测试不可见 | autouse fixture |
| `test_builtin_branch_count_is_two`（守护，R4） | 内置兜底分发分支数 == 2，且为 `("llm","LLM")` / `("http","HTTP")` 大小写集合 | 源码审查 + 断言 |
| `test_convert_pydantic_model` / `test_convert_dict` / `test_convert_other` | 三种输入分支 | spec-02 动态模型 / dict / object() |
| `test_dual_write_both_keys` | `{node}_result` 整包 + 逐字段平铺同时存在 | 断言键集 |
| `test_dual_write_disabled` | `dual_write=False` → 只有 `{node}_result` | 断言键集 |
| `test_history_increment_only` | state 含 `history=[a]` → 更新返回 `{"history": [entry]}`（**增量，非 a+entry**） | 精确断言 |
| `test_history_not_list_no_append` | state `history="str"` → 更新不含 history 键（消除二义性，C4） | 断言 |
| `test_node_output_writes_history_no_auto_append` | `node_output` 自带 history → 不重复追加 | 断言 |

## 8. 验收标准 DoD

- [ ] 三个测试文件全绿；`grep -nE "LLMHelper|neo4j|requests" app/workflow/utils.py` 零命中（C7 守护）
- [ ] `create_node` 内置兜底分发分支数 == 2（`("llm","LLM")` / `("http","HTTP")` 双别名大小写集合）；无任何领域 if/elif（R4 守护：`grep -n "elif" app/workflow/nodes/factory.py` 人工核对）
- [ ] `nodes/base.py` 不含任何网络/模型调用逻辑
- [ ] 运行级收集器接口（Protocol + ContextVar）就位且被 `log_execution` 使用（H3 预埋）
- [ ] `make lint`、`ruff format --check .`、`make typecheck` 零问题
- [ ] 通用 DoD G1-G8 + 红线自检表（CONTRACT §12）逐项勾选

## 9. 本阶段涉及的隐患修复

- **H3（预埋）**：`RunLogCollectorLike` + `_RUN_COLLECTOR` ContextVar 接口就位，`log_execution` 双写（实例历史仅调试 + 运行级收集器为权威）；完整运行级收集在 spec-07 闭环。

## 10. 交付物清单

- `app/workflow/nodes/base.py`、`app/workflow/nodes/factory.py`、`app/workflow/nodes/__init__.py`（导出）、`app/workflow/utils.py`
- `tests/unit/workflow/nodes/test_base.py`、`tests/unit/workflow/nodes/test_factory.py`、`tests/unit/workflow/test_utils.py`
- `tests/conftest.py`（新增 `restore_node_registry` autouse fixture）

## 11. 验收命令

```bash
uv run pytest tests/unit/workflow/nodes/ tests/unit/workflow/test_utils.py -m unit -v
make lint && ruff format --check . && make typecheck
grep -nE "LLMHelper|neo4j|requests" app/workflow/utils.py    # 期望零命中
grep -n "elif" app/workflow/nodes/factory.py                 # 人工核对：无领域分支堆叠
wc -l app/workflow/nodes/base.py app/workflow/nodes/factory.py app/workflow/utils.py   # 期望 < 200 / < 120 / < 150
```
