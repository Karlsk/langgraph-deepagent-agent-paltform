# 1.x API 探索任务书与报告（api-exploration-1x）

| 项 | 值 |
| --- | --- |
| 文档版本 | v1.1（实测闭环）；TC4-6 全部回填完成，2026-07-30 |
| 角色 | CONTRACT 第 7 章 **R-EXP 探索先行规则**的执行载体 |
| 执行时机 | Phase 0（spec-00 TC4-6），**全部闭环是 M1 开工前置条件** |
| 版本基准（uv.lock） | langgraph 1.0.2 / langchain 1.0.5 / langchain-core 1.0.4 / langchain-openai 1.0.2 / pydantic 2.11 / Python 3.13 / tenacity 9.1.2 / httpx 0.28.1 |
| 门禁映射 | EXP-G → spec-02/06/07；EXP-C → spec-03/07；EXP-L → spec-04；EXP-X → spec-00/01 |

---

## 0. 使用规则

1. **闭环标准**：每个 EXP 项的"实测结果"栏填写实测行为 + 证据（`.venv` 源码 `路径:行号` 或离线脚本输出），并在"结论"栏勾选【吻合】或【偏差】。全部 EXP 闭环后才允许进入被门禁的 spec 编码。
2. **允许手段**（四选一或组合，项内已指定）：
   - `SRC`：读 `.venv` 内已装包源码（`uv run python -c "import <pkg>; print(<pkg>.__file__)"` 定位）；
   - `REPL`：离线实例化/内省脚本（`uv run python -c ...`，**禁止真实网络调用**）；
   - `MOCK`：mock 传输层（`httpx.MockTransport` / 假 transport）驱动的 characterization test；
   - `TEST`：写入 `tests/integration/workflow/` 的 characterization test（可长期保留作回归）。
3. **禁止**：真实调用 OpenAI/Anthropic API；依赖未安装版本的文档记忆（一切以下述锁定版本实测为准）。
4. **偏差处置**：实测与"规划文档假设"不符 → **停止相关编码** → 按 CONTRACT 第 11 章变更流程提出 2-3 个方案（含回退方案）→ 决策后回写本文件与 CONTRACT。
5. **填写规范**：实测结果用客观描述（行为 + 证据），不写推测；无法离线验证的项标记【阻塞】并立即提问。

---

## EXP-G 图构建（langgraph 1.0.2）— 门禁 spec-02 / spec-06 / spec-07

### EXP-G1 pydantic 模型作为 state schema

- **API 点**：`StateGraph(state_schema)` 接受 pydantic `BaseModel` 子类（含 `create_model` 产物）。
- **规划文档假设**：`create_model("DynamicWorkflowState", **fields, __base__=_DynamicStateBase)`（`extra="allow"` 基类）生成的模型可直接传给 `StateGraph`，langgraph 按字段注解推断 channel（D1/D2）。
- **验证方法**：SRC（`langgraph/graph/state.py` 中 `StateGraph.__init__` 与 state schema 分支）+ REPL（生成动态模型 → `StateGraph(model)` → 编译空图）。
- **实测结果**：吻合。`create_model("DynamicWorkflowState", **fields, __base__=_DynamicStateBase)`（`extra="allow"` 基类）可直接传入 `StateGraph` 并编译。SRC：`__init__` 不区分 pydantic/TypedDict，统一走 `_add_schema` → `_get_channels`（`.venv/.../langgraph/graph/state.py:187-239`、`state.py:1306-1326`），后者用 `get_type_hints(schema, include_extras=True)` 从类型注解推 channel，任何带 `__annotations__` 的类型均可；`_warn_invalid_state_schema`（`state.py:92-101`）只拒非 type 对象。REPL：四字段动态模型推得 channels `{input: LastValue, history: BinaryOperatorAggregate, mode: BinaryOperatorAggregate, plain: LastValue}`，`compile()` 成功。TEST：`tests/integration/workflow/test_exploration_graph.py::test_g1_dynamic_pydantic_model_as_state_schema` 绿。
- **结论**：【吻合】

### EXP-G2 Annotated reducer 的 channel 推断

- **API 点**：`Annotated[list, operator.add]` / `Annotated[T, _last]` 在 pydantic 字段上被识别为聚合 channel；未标注字段为 LastValue（后写覆盖）。
- **规划文档假设**：reducer 放 annotation 位置（二元组 `(annotation, FieldInfo)`）即被识别；未标注字段默认 LastValue（K3/D2）。
- **验证方法**：SRC（langgraph channel 推断逻辑）+ TEST（两节点各返回 `{"history": [x]}`，断言终态合并为 `[x, y]`；未标注字段两次写后者覆盖）。
- **实测结果**：吻合。SRC：`_get_channel`（`state.py:1341-1364`）优先识别 `Annotated` 元数据中的 BaseChannel，其次 `_is_field_binop`（`state.py:1381-1399`）把 `Annotated[T, <二元可调用>]` 末位元素识为 reducer → `BinaryOperatorAggregate`（签名必须恰两个位置参数，否则 `ValueError`）；无标注字段 fallback `LastValue`（`state.py:1362-1364`）。pydantic 二元组 `(Annotated[list, operator.add], default)` 形态经 `get_type_hints(include_extras=True)` 正常进入上述判定。TEST：两节点各返 `{"history": [x]}` 终态合并 `["a","b"]`；`Annotated[str, _last]` 与未标注字段均后写覆盖（`test_g2_g3_reducer_merge_partial_update_and_input_form` 绿）。
- **结论**：【吻合】

### EXP-G3 add_node 的输入形态与返回值合并语义

- **API 点**：`graph.add_node(name, RunnableLambda(func))`；节点 func 收到的输入形态（dict 还是模型实例）、返回 dict 如何合并入 state。
- **规划文档假设**：func 收到可 `convert_state_to_dict` 处理的对象；返回 `dict` 为**部分更新**，按 channel 语义合并（K4/R3）。
- **验证方法**：SRC + TEST（EchoNode 返回部分键，断言未返回键保持原值；记录 func 实参类型）。
- **实测结果**：吻合。`add_node(name, RunnableLambda(func))` 形态可用（`state.py:349-462`，Runnable 分支取 `get_name()`）。**func 实参是 pydantic 模型实例**（REPL 实测 `type(state) == DynamicWorkflowState`，state schema 为 pydantic 时 langgraph 先构造模型再传入），可被 `convert_state_to_dict` 的 `model_dump()` 分支处理，符合 R3 假设。返回 dict 为部分更新：`_get_updates`（`state.py:954-967`）把 dict 拆为 `(key, value)` 写入对应 channel，未返回键保持原值（TEST 实测 `input` 未被 node_b 返回仍为 `"hello"`），按 channel 语义合并（add 累加 / LastValue 覆盖）。
- **结论**：【吻合】（备忘：实参是模型实例而非 dict，节点入口必须走 `convert_state_to_dict`，不可直接下标取键）

### EXP-G4 add_conditional_edges 的 1.x 签名与 path_map 行为

- **API 点**：`graph.add_conditional_edges(source, path_func, path_map)`；`path_func(state) -> str` 返回值经 `path_map` 映射到目标节点。
- **规划文档假设**：三参形态可用；`path_map` 为 `dict[str, str]`，可把 `"END"` 映射为 `END` 常量（K7）。
- **验证方法**：SRC（`langgraph/graph/graph.py` 签名与文档字符串）+ REPL（最小条件图编译 + invoke）。
- **实测结果**：吻合。注：1.0.2 无 `graph/graph.py`，签名在 `langgraph/graph/state.py:613-658`：`add_conditional_edges(source, path, path_map=None)` 三参形态可用，`path_map: dict[Hashable, str] | list[str] | None`；`BranchSpec.from_path`（`langgraph/graph/_branch.py:89-120`）将 dict 原样 copy、list 转恒等 dict。REPL/TEST：`path_func(state) -> str` 返回值经 `path_map={"finish": END, "cont": "other"}` 映射，命中 `END` 常量后图正常终止、未路由分支不执行（`test_g4_conditional_edges_path_map_to_end` 绿）。把字面量 `"END"` 映射为 `END` 常量（K7）的做法成立。
- **结论**：【吻合】

### EXP-G5 START / END 常量

- **API 点**：`from langgraph.graph import START, END`；`add_edge(START, x)` / `add_edge(x, END)` / `set_entry_point`。
- **规划文档假设**：常量可导入；`set_entry_point(name)` 等价于 `add_edge(START, name)`；边 target `"END"` 字面量需映射为 `END` 常量（K6）。
- **验证方法**：SRC（`langgraph/constants.py` 或 `langgraph/graph/__init__.py` 导出面）+ REPL。
- **实测结果**：吻合。`from langgraph.graph import START, END` 可用（`__init__.py` 导出面含二者）；实值 `START == "__start__"`、`END == "__end__"`（`langgraph/constants.py:28,30`，`sys.intern` 哨兵字符串）。`set_entry_point(name)` 源码即 `return self.add_edge(START, key)`（`state.py:705-716`），REPL 实测调用后 `(START, "n") in graph.edges`。边 target 用字面量 `"END"` 不会自动等于 `END`（实值是 `"__end__"`），GraphBuilder 必须显式映射（K6 假设成立）。TEST：`test_g5_start_end_constants_and_entry_point` 绿。
- **结论**：【吻合】

### EXP-G6 compile() 产物与 invoke 的输入输出形态

- **API 点**：`graph.compile()` 返回类型；`compiled.invoke(input_dict, config)` 的输入校验、输出形态；`config`（tags/callbacks）向节点 func 的传播。
- **规划文档假设**：返回 `CompiledStateGraph`；`invoke(dict)` 返回终态 dict（含 extra 键）；config 沿 Runnable 协议传播（K6/K8）。
- **验证方法**：SRC + TEST（带 config 的 invoke，断言输出形态与 config 可达性）。
- **实测结果**：吻合（含两条备忘）。`compile()` 返回 `CompiledStateGraph`（`state.py:801-811` 签名；实例是 `Pregel` 子类即 Runnable）。`invoke(dict)` 返回普通 `dict`，config 的 `tags`/`metadata` 可传到双参节点 func（实测 `tags=["mytag"]` 可达，Runnable 协议传播成立）。备忘 1：终态 dict **仅含声明字段**，不含 extra 键（见 G8 偏差）。备忘 2：输入 dict 不经 pydantic 校验——输入直接作为 channel 更新写入，坏类型（如 `history="x"`）直到 reducer 处才报 `TypeError: can only concatenate list...`，而非 `ValidationError`，引擎入口校验需自行把关。TEST：`test_g6_invoke_output_dict_and_config_propagation`、`test_g6_invoke_input_not_pydantic_validated` 绿。
- **结论**：【吻合】（输出含 extra 键的子句除外，归入 G8 偏差处置）

### EXP-G7 节点异常的传播行为

- **API 点**：节点 func 抛出自定义异常（如 `ConditionNotMatchedError`）时，`invoke` 如何传播（原样 or 包装）；无 checkpointer 时的行为。
- **规划文档假设**：异常原样向调用方传播，`execute_workflow` 可捕获并重抛（R6/S6）；本期**无 checkpointer**。
- **验证方法**：TEST（节点抛 `ConditionNotMatchedError` → `pytest.raises` 断言类型与消息不被吞/不包装）。
- **实测结果**：吻合。无 checkpointer 时，节点 func 抛出的自定义异常经 `invoke` **原类型原消息**传播（`pytest.raises(_ConditionNotMatchedError, match=...)` 命中，未被包装为 langgraph 异常）；条件路由 path_func 内抛异常同样原样传播。`execute_workflow` 可直接捕获引擎异常族（R6/S6 可行）。TEST：`test_g7_node_exception_propagates_unwrapped`、`test_g7_path_func_exception_propagates_unwrapped` 绿。
- **结论**：【吻合】

### EXP-G8 langgraph 1.0.2 × pydantic 2.11 兼容性边界

- **API 点**：动态模型（`create_model` + `extra="allow"`）在 langgraph 输入校验/序列化路径上的行为；extra 键在终态中的保留。
- **规划文档假设**：extra 键可写入并在终态保留（K2 Dify 风格）。
- **验证方法**：TEST（节点写未声明键 `foo` → 终态含 `foo`；记录有无告警/异常）。
- **实测结果**：**偏差**。`extra="allow"` 动态模型下，节点返回的未声明键 `foo` **被静默丢弃**（无告警、无异常，`warnings.catch_warnings(record=True)` 捕获为空）；invoke 输入 dict 中的未声明键同样被丢弃。根因（SRC）：channel 集合在 `StateGraph.__init__` 时由 `_get_channels` 从类型注解一次性冻结（`state.py:1306-1326`），运行期节点更新经 `attach_node._get_updates` 按 `k in output_keys` 过滤（`state.py:954-967`），START 输入也按 input_schema 声明键过滤（`state.py:942-948`）：pydantic `extra="allow"` 只影响模型自身校验，对 langgraph 的 channel 写入面**无效**。K2“extra 键写入并在终态保留”假设不成立。TEST 已固化实际行为：`test_g8_extra_keys_silently_dropped`、`test_g8_input_extra_keys_also_dropped`。已按 CONTRACT §11 上报 3 个备选方案，**2026-07-30 决策：采纳方案 1（构建期预声明双写键）**——StateModelFactory/GraphBuilder 在 `create_state_model` 阶段按 `definition.nodes` 为每个节点自动注入 `{node_name}_result: (Any, None)` 声明字段（走 LastValue channel），S4 双写语义完整保留；除此之外的任意 extra 键定为**不支持**，YAML `state_schema` 必须显式声明。具体设计留待 spec-02 实施时落地；CONTRACT §4.3 与 spec-02 已加修订备注。
- **结论**：【偏差-已决策】（CONTRACT §11 已走完：2026-07-30 拍板方案 1；影响面收敛为 spec-02 StateModelFactory 预声明 `{node_name}_result` 字段 + K2 extra 语义改写为“仅声明键”）

---

## EXP-C Runnable 契约（langchain-core 1.0.4）— 门禁 spec-03 / spec-07

### EXP-C1 RunnableLambda func 签名规则

- **API 点**：`RunnableLambda(func)` 的 func 支持哪些签名（单参 `func(state)`；双参 `func(state, config)` 的注入条件——参数名/类型注解要求）。
- **规划文档假设**：单参 `func(state) -> dict` 可直接使用（K4）；若声明第二参，langchain 注入 `RunnableConfig`。
- **验证方法**：SRC（`langchain_core/runnables/base.py` 中 `RunnableLambda` 的 `acall`/调用逻辑与 config 注入判定）+ REPL（两种签名各跑通）。
- **实测结果**（SRC+REPL+TEST，langchain-core 1.0.4）：
  - 单参 `func(state)` 直接跑通：`RunnableLambda(lambda s: {"out": s["in"]+1}).invoke({"in":1})` → `{"out": 2}`。
  - **config 注入判定是「参数名」而非「第二参位置」或「类型注解」**：`utils.py:92 accepts_config` 用 `signature(callable).parameters.get("config") is not None` 判断；注入发生在 `config.py:399 call_func_with_variable_args`（`kwargs["config"]=config`），经 `base.py:4714 RunnableLambda._invoke` 调用。
  - 双参 `def node(state, config)`：`config` 被注入为普通 `dict`，含 `ensure_config` 默认键 `{tags, metadata, callbacks, configurable, recursion_limit}`（REPL 实测 `type=dict`，`keys=['callbacks','configurable','metadata','recursion_limit','tags']`）。
  - 反例 `def node(state, cfg=None)`（第二参名非 `config`）→ **不注入**，`cfg` 保持默认 `None`（证明非按位置注入）。
  - 证据：`tests/integration/workflow/test_exploration_runnable.py::test_c1_*`（3 例，`uv run pytest -m integration` 通过）。
- **结论**：**吻合**（附精确用法：节点若要接收 `RunnableConfig`，第二参数必须**恰命名为 `config`**；引擎节点将采用 `def node(state, config)` 签名，假设 K4 成立）。

### EXP-C2 with_config(tags=[name]) 的 tags 透传

- **API 点**：`RunnableLambda(func).with_config(tags=[name])` 后，tags 是否出现在 func 收到的 config 中（K4 tags 约定依赖此行为）。
- **规划文档假设**：`with_config` 的 tags 合并进运行时 config，可在 func 内（若接收 config）或 callback 中观测。
- **验证方法**：SRC + REPL（双参 func 打印/断言 `config.get("tags")`）。
- **实测结果**（REPL+TEST，langchain-core 1.0.4）：
  - `RunnableLambda(node).with_config(tags=["my_node"]).invoke(...)` → func 内 `config.get("tags") == ["my_node"]`，tags 确实合并进运行时 config 并对（接收 `config` 的）func 可见。
  - 绑定 tags 与 invoke 时 `config={"tags":["b"]}` 合并为**排序去重并集** `["a","b"]`（证据 `config.py:352-355 merge_configs` tags 分支 `sorted(set(base + config))`）。
  - 证据：`test_exploration_runnable.py::test_c2_with_config_tags_visible_in_func`、`::test_c2_bound_tags_merge_with_invoke_tags`。
- **结论**：**吻合**（K4 的 tags 约定成立；注意多来源 tags 会被排序去重合并，节点内不能假设 tags 顺序或单一来源）。

### EXP-C3 RunnableConfig 结构与 callbacks 字段

- **API 点**：`RunnableConfig` 的字段集（tags/metadata/callbacks/run_name 等）与类型。
- **规划文档假设**：`config: RunnableConfig` 为 TypedDict/dict 形态，含 `tags` 与 `callbacks`。
- **验证方法**：SRC（`langchain_core/runnables/config.py`）。
- **实测结果**（SRC，`config.py:49`）：`RunnableConfig(TypedDict, total=False)`，字段集：`tags: list[str]`、`metadata: dict[str, Any]`、`callbacks: Callbacks`、`run_name: str`、`max_concurrency: int | None`、`recursion_limit: int`、`configurable: dict[str, Any]`、`run_id: uuid.UUID | None`（共 8 个，含 `tags` 与 `callbacks`）。`CONFIG_KEYS`（`config.py:101`）为同一集合；`ensure_config` 默认填充 `tags/metadata/callbacks/recursion_limit/configurable` 五键。证据：`test_exploration_runnable.py::test_c3_runnable_config_field_set`。
- **结论**：**吻合**（`config: RunnableConfig` 为 `total=False` 的 TypedDict/dict 形态，含 `tags` 与 `callbacks`；运行时注入的 dict 未设置的键需用 `config.get(k)` 取，不能假设 `run_name`/`run_id`/`max_concurrency` 一定存在）。

---

## EXP-L LLM 客户端（langchain-openai 1.0.2 + langchain-anthropic 1.0.4）— 门禁 spec-04

### EXP-L1 ChatOpenAI / ChatAnthropic 1.x 构造参数

- **API 点**：`ChatOpenAI(model=?, api_key=?, base_url=?, temperature=?, max_tokens=?, timeout=?, max_retries=?)` 与 `ChatAnthropic` 对应参数名与默认值；`extra_params` 的透传通道（`model_kwargs`?）。
- **规划文档假设**：可按 `LLMConfig` 字段直接构造；anthropic 的 `max_tokens` 可能有必填/默认值差异。
- **验证方法**：SRC（两包 `chat_models.py` 的字段定义）+ REPL（离线实例化，env 设哑密钥，断言属性落位）。
- **实测结果**（SRC+REPL+TEST，langchain-openai 1.0.2 / **langchain-anthropic 1.0.4** / openai SDK 2.7.1 / anthropic SDK 0.120.2）：
  - 两类均 `populate_by_name=True`，统一 kwarg 面 `model/api_key/base_url/temperature/max_tokens/timeout/max_retries/model_kwargs` **全部经 alias 落位成功**（REPL 断言属性值逐一吻合）。字段↔alias 映射：
    - `ChatOpenAI`（`langchain_openai/chat_models/base.py:470-641`）：`model_name`(alias `model`，默认 `"gpt-3.5-turbo"`)、`openai_api_key: SecretStr|None|Callable`(alias `api_key`，`secret_from_env("OPENAI_API_KEY", default=None)`)、`openai_api_base`(alias `base_url`)、`temperature: float|None=None`、`max_tokens: int|None=None`(alias `max_completion_tokens`，见 `base.py:2832`)、`request_timeout`(alias `timeout`)、`max_retries: int|None=None`（None 时用 openai SDK 默认 2）、`model_kwargs: dict`（extra_params 透传通道，另有 `extra_body` 供非标准 provider 参数）。
    - `ChatAnthropic`（`langchain_anthropic/chat_models.py:1430-1497`）：`model: str`(**必填**，alias `model_name`)、`anthropic_api_key: SecretStr`(alias `api_key`，`secret_from_env("ANTHROPIC_API_KEY", default="")`)、`anthropic_api_url`(alias `base_url`，默认 `https://api.anthropic.com`)、`temperature: float|None=None`、`max_tokens: int|None`(alias `max_tokens_to_sample`)、`default_request_timeout`(alias `timeout`)、`max_retries: int = 2`、`model_kwargs: dict`。
  - **max_tokens 差异**（假设中的"必填/默认值差异"实测确认）：ChatAnthropic 构造时 `set_default_max_tokens`（`chat_models.py:1587-1591`）按模型家族自动填充——`claude-sonnet-4-5` → 64000，未知家族 fallback 4096（`_default_max_tokens_for`，`chat_models.py:76-88`）；ChatOpenAI 保持 `None`（交给服务端默认）。
  - **陷阱 1**：ChatAnthropic 家族默认 max_tokens（如 64000）+ 非流式 invoke + 未显式设 timeout → anthropic SDK 直接抛 `ValueError: Streaming is required for operations that may take longer than 10 minutes`（`anthropic/_base_client.py:775` 预估耗时 >10 分钟即拒绝）。**spec-04 的 LLMNode 必须显式传 max_tokens（或 timeout）**。
  - **陷阱 2**：ChatOpenAI 在 env 无 `OPENAI_API_KEY` 且未传 `api_key` 时**构造即抛** `openai.OpenAIError`（`validate_environment` 内建 SDK client）；ChatAnthropic 允许空 key 构造（`SecretStr('')`），失败推迟到调用期。
  - 证据：`tests/integration/workflow/test_exploration_llm.py::test_l1_*`（3 例，离线全绿）。
- **结论**：【吻合】（可按 `LLMConfig` 字段直接构造，统一 alias 面成立；max_tokens 差异与两条陷阱已记录，spec-04 需显式归一化 max_tokens/api_key）

### EXP-L2 invoke 返回 AIMessage 形态

- **API 点**：`.invoke(messages)` 返回 `AIMessage`；`.content` 形态（str vs list blocks）；token usage 元数据字段名（`usage_metadata`?）。
- **规划文档假设**：成功输出取 `<AIMessage>.content` 写入 `{"response": content, "model": model_name}`（Phase 4 契约）。
- **验证方法**：SRC + MOCK（以假 transport/假 client 驱动一次 invoke，记录返回结构）。
- **实测结果**（MOCK：`httpx.MockTransport` 驱动，零真实网络。ChatOpenAI 经构造参数 `http_client=` 注入；ChatAnthropic 无该参数，经预填 `cached_property _client`（`chat_models.py:1617`）注入 mock `anthropic.Client`）：
  - 两 provider 的 `.invoke(messages)` 均返回 `langchain_core.messages.AIMessage`（`type(msg) is AIMessage`）。
  - `.content`：纯文本回复时**两家均为 `str`**（anthropic 的单 text block 被折叠为 str）；多 block（tool_use 等）时才是 list。跨 provider 统一读法：`msg.content_blocks`（标准化 list，如 `[{"type": "text", "text": ...}]`）或 `msg.text`。
  - token 用量字段名确认为 **`usage_metadata`**：两家均为 `{"input_tokens", "output_tokens", "total_tokens", ...details}` 标准化 dict（openai 由 `prompt/completion_tokens` 映射而来，`total_tokens` anthropic 侧为自动求和）。
  - `response_metadata`：两家均含 `model_name` 与 `model_provider`（`"openai"`/`"anthropic"`）；provider 特有键不同——openai 有 `finish_reason`/`token_usage`/`system_fingerprint`，anthropic 有 `stop_reason`/`usage`（原始形态）。**取模型名统一用 `response_metadata["model_name"]`**（anthropic 侧 `llm_output` 已做 `model`→`model_name` 补齐，`chat_models.py:1832-1833`）。
  - Phase 4 契约 `{"response": <AIMessage>.content, "model": model_name}` 可行。
  - 证据：`test_exploration_llm.py::test_l2_openai_invoke_returns_aimessage_shape`、`::test_l2_anthropic_invoke_returns_aimessage_shape`。
- **结论**：【吻合】（成功输出取 `.content` 成立；备忘：`.content` 仅纯文本时是 str，稳妥读法是 `msg.text`/`content_blocks`；模型名从 `response_metadata["model_name"]` 取）

### EXP-L3 限流/5xx 异常类型层级

- **API 点**：429 / 5xx 时 langchain 抛出的异常类型（`openai.RateLimitError` / `anthropic.RateLimitError` 是否原样透出）、异常消息文本是否含 "429"/"rate" 字样、状态码属性名。
- **规划文档假设**：tenacity retry 谓词可依据"异常消息命中 429/rate（不区分大小写）"判定（等价原手写循环口径，AD-03）。
- **验证方法**：SRC（两包异常处理路径）+ MOCK（构造 429 响应经 mock transport 触发，记录异常类型与 str(exc)）。
- **实测结果**（MOCK 驱动 429/500/503/529 + SRC 异常树，SDK `max_retries=0` 关闭内建重试后观测）：
  - **SDK 异常原样透出**，langchain 不包装：429 → `openai.RateLimitError` / `anthropic.RateLimitError`；500/503 → 两家 `InternalServerError`（openai 把**所有 ≥500** 归入 `InternalServerError`；anthropic 另有专属 529 `OverloadedError`、503 `ServiceUnavailableError` 等细分子类）。层级：`RateLimitError → APIStatusError → APIError → OpenAIError/AnthropicError → Exception`。
  - 属性与消息：`APIStatusError` 两家均带 **`status_code: int`**；`str(exc)` 形如 `"Error code: 429 - {...}"`——**429 消息必含 "429" 且含 "rate"**（错误体 type 为 rate_limit_error），假设的"消息命中 429/rate"谓词对 429 成立；但 **5xx 消息不含这两个词**，纯消息谓词会漏掉 5xx。
  - **tenacity 谓词建议写法**（推荐 status_code 口径，免 import 两家异常类且覆盖 5xx）：
    ```python
    def _is_retryable_llm_error(exc: BaseException) -> bool:
        status = getattr(exc, "status_code", None)
        return isinstance(status, int) and (status == 429 or status >= 500)

    retry=retry_if_exception(_is_retryable_llm_error)
    ```
    实测该谓词对两家 429/500/529 全判真、400 `BadRequestError` 判假，接入 `Retrying(stop=stop_after_attempt(2), reraise=True)` 重试计数与 reraise 类型均正确。
  - 备忘：SDK 自身有内建重试（openai 默认 2 次、anthropic 默认 2 次），spec-04 用 tenacity 统一管理时应构造 `max_retries=0` 避免双层重试叠加。
  - 证据：`test_exploration_llm.py::test_l3_openai_429_and_5xx_raise_sdk_errors_unwrapped`、`::test_l3_anthropic_429_and_5xx_raise_sdk_errors_unwrapped`、`::test_l3_tenacity_predicate_classifies_and_drives_retry`。
- **结论**：【吻合】（原假设的消息谓词对 429 成立；定稿采用更稳的 `status_code == 429 or >= 500` 谓词，等价覆盖原手写循环口径且补齐 5xx，AD-03 满足）

### EXP-L4 api_key 的 SecretStr 脱敏行为（H6 依赖）

- **API 点**：构造时传入 `api_key` 后，实例 `repr()`/日志序列化是否脱敏（pydantic `SecretStr`）。
- **规划文档假设**：密钥不落日志（H6 验证依赖实例不泄露密钥值）。
- **验证方法**：REPL（哑密钥实例化 → `repr(instance)` / `str(instance)` 断言不含密钥值）。
- **实测结果**（REPL+TEST，哑密钥 `dummy-exploration-key-123`）：
  - 两类的 api_key 字段均为 pydantic `SecretStr`（`ChatOpenAI.openai_api_key` / `ChatAnthropic.anthropic_api_key`）。`repr(instance)`、`str(instance)`、`repr(secret)`（=`SecretStr('**********')`）、`str(secret)`、f-string、`model_dump()`、`model_dump_json()` **全部不泄露**密钥明文；SDK 侧经 `get_secret_value()` 取真值（如 `chat_models.py:1604`）。
  - 两类均声明 `lc_secrets`（openai：`{"openai_api_key": "OPENAI_API_KEY"}`；anthropic 含 `anthropic_api_key`），langchain 序列化面同样脱敏。
  - 证据：`test_exploration_llm.py::test_l4_api_key_never_leaks_from_repr_str_or_dump`。
- **结论**：【吻合】（H6 依赖成立：实例 repr/str/dump 不泄露密钥；日志脱敏 processor 仍按 spec-08 计划补全，作为纵深防御）

### EXP-L5 langchain-anthropic 版本配套区间

- **API 点**：与 langchain-core 1.0.4 / langchain 1.0.5 配套的 `langchain-anthropic` 主版本区间（写入 pyproject 的约束写法，AD-05）。
- **规划文档假设**：存在与 langchain 1.x 同代际的 langchain-anthropic 1.x。
- **验证方法**：SRC/REPL（`uv add` 后查 `uv.lock` 解析结果与 `langchain_anthropic.__version__`；确认 import 链无冲突）。
- **实测结果**（dist-info METADATA + importlib.metadata + TEST；注：包未导出 `__version__` 属性，版本以 `importlib.metadata.version` 为准）：
  - TC2 约束 `">=1.0,<1.1"` 解析装入 **langchain-anthropic 1.0.4**。其 dist-info 依赖声明：`langchain-core<2.0.0,>=1.0.4`（冻结的 langchain-core 1.0.4 恰好满足下界）、`anthropic<1.0.0,>=0.69.0`（实装 0.120.2）、`pydantic<3.0.0,>=2.7.4`（实装 2.11.x）。与 langchain 1.0.5 / langchain-openai 1.0.2 同装无解析冲突，import 链无冲突（EXP-X1 已验证 clean-room 导入）。
  - 假设"存在与 langchain 1.x 同代际的 langchain-anthropic 1.x"成立。上界 `<1.1` 的保守性论证：langchain-anthropic 1.0.x 内的后续 patch 若抬高 langchain-core 下界超过 1.0.4，uv 解析会因本项目 `langchain-core` 被 uv.lock 冻结在 1.0.4 而拒绝升级（fail-fast 而非静默漂移）；放开到 `<2.0` 则允许 minor 升级引入行为变化，与"探索结论按 1.0.4 实测冻结"的口径不符。
  - **最终 pyproject 约束文本（定稿）**：`"langchain-anthropic>=1.0,<1.1"`——**维持 TC2 现状，pyproject.toml/uv.lock 无需改动**。
  - 证据：`test_exploration_llm.py::test_l5_langchain_anthropic_pairs_with_frozen_langchain_core`（元数据配套关系已固化为回归测试）。
- **结论**：【吻合】（AD-05 定稿：`langchain-anthropic>=1.0,<1.1`，解析 1.0.4，与 langchain-core 1.0.4 配套；文件零改动）

---

## EXP-X 1.0 迁移影响 — 门禁 spec-00 / spec-01

### EXP-X1 最小依赖集

- **API 点**：`app/workflow/` 直接 import 的包面（`langgraph`、`langchain_core`、`langchain_openai`、`langchain_anthropic`）；顶层 `langchain` 包（1.0.5）是否仍被需要。
- **规划文档假设**：引擎只依赖 `langgraph` + `langchain-core` + 两个 provider 包；顶层 `langchain` 可不被引擎直接 import（现有 app 代码另有用途，不动）。
- **验证方法**：SRC（规划 import 清单逐一核对 `uv pip show`）+ 结论写入 spec-00。
- **实测结果**（REPL+SRC+TEST，`.venv` 实测 langchain-core 1.0.4 / langgraph 1.0.2 / langchain-openai 1.0.2 / langchain-anthropic 1.0.4）：
  - 引擎规划 import 面全部可导入：`langgraph.graph`（`StateGraph`/`START`/`END`）、`langgraph.graph.state.CompiledStateGraph`、`langchain_core.runnables`（`RunnableLambda`/`RunnableConfig`）、`langchain_openai.ChatOpenAI`、`langchain_anthropic.ChatAnthropic`、`pydantic`（`create_model`/`BaseModel`）。
  - `importlib.metadata.requires` 核查：`langchain-openai`、`langchain-anthropic`、`langgraph`、`langchain-core` 四包均**不**声明对顶层 `langchain` 包的运行时依赖。
  - 干净子解释器只 import 上述引擎面后，`sys.modules` 中**无**顶层 `langchain`（`langchain_core` 在，`langchain_classic` 不在）。证据：`test_exploration_runnable.py::test_x1_engine_imports_do_not_require_top_level_langchain`（clean-room 子进程断言）。
- **结论**：**吻合**。引擎仅依赖 `langgraph` + `langchain-core` + `langchain-openai` + `langchain-anthropic`；顶层 `langchain`（1.0.5，仍随现有 app 代码安装）不被引擎直接 import，也不被上述四包运行时强依赖。（`app/workflow/` 尚未落编码，本项为规划 import 面核查；spec-01+ 实际落码时任一模块若出现 `import langchain` 需回到本项复核。）

### EXP-X2 0.x 假设 vs 1.x 实测对照总表

- **API 点**：规划文档（00-03）中全部基于 langgraph 0.2-0.7 / langchain 0.x 的 API 假设点的汇总对照。
- **填写方式**：EXP-G/C/L 各项闭环后，把结论浓缩进下表，作为 CONTRACT 附录长期留存。

| 假设点 | 来源 | 0.x 假设 | 1.x 实测 | 结论 |
| --- | --- | --- | --- | --- |
| pydantic state schema | EXP-G1 | `create_model` 产物（`extra="allow"` 基类）可直传 `StateGraph`，按注解推 channel | 成立：`_add_schema → _get_channels` 不区分 pydantic/TypedDict，`get_type_hints(include_extras=True)` 推 channel，编译成功（`state.py:187-239,1306-1326`） | 吻合 |
| reducer channel 推断 | EXP-G2 | `Annotated[list, operator.add]` 识为聚合 channel，未标注字段 LastValue | 成立：`_is_field_binop` 识别二元 reducer → `BinaryOperatorAggregate`（签名须恰两位置参数），无标注 fallback `LastValue`（`state.py:1341-1399`） | 吻合 |
| 节点输入/返回合并 | EXP-G3 | func 收可转 dict 的对象；返回 dict 为部分更新 | 成立且更精确：func 实参是 **pydantic 模型实例**（需 `convert_state_to_dict`，不可直接下标）；返回 dict 按 channel 语义部分合并（`state.py:954-967`） | 吻合 |
| add_conditional_edges path_map | EXP-G4 | 三参形态；`path_map: dict[str,str]` 可映射 `"END"`→`END` | 成立：签名在 `state.py:613-658`（非 0.x 的 `graph/graph.py`），`path_map: dict[Hashable,str]|list[str]|None` | 吻合 |
| START/END | EXP-G5 | 常量可导入；`set_entry_point ≡ add_edge(START, name)` | 成立：`START=="__start__"`、`END=="__end__"`（`constants.py:28,30`）；字面量 `"END"` ≠ `END`，GraphBuilder 须显式映射 | 吻合 |
| compile/invoke 形态 | EXP-G6 | 返回 `CompiledStateGraph`；`invoke(dict)` 返回终态 dict；config 沿 Runnable 协议传播 | 基本成立；两条备忘：输出**仅含声明字段**（extra 归 G8）；输入 dict **不经 pydantic 校验**，入口校验需自行把关 | 吻合（附备忘） |
| 异常传播 | EXP-G7 | 节点异常原样向调用方传播（无 checkpointer） | 成立：节点 func 与 path_func 异常均**原类型原消息**透出，不被包装 | 吻合 |
| extra 键保留 | EXP-G8 | `extra="allow"` 下 extra 键可写入并在终态保留（K2） | **不成立**：channel 集合构建期冻结，未声明键被静默丢弃（`state.py:942-967,1306-1326`） | 偏差-已决策（方案 1：预声明 `{node_name}_result` 字段，其余 extra 键定为不支持） |
| RunnableLambda 签名 | EXP-C1 | 单参 `func(state)` 可用；声明第二参则注入 config | 成立且更精确：注入判定是**参数名恰为 `config`**（非位置/注解，`utils.py:92 accepts_config`），注入物为普通 dict | 吻合 |
| tags 透传 | EXP-C2 | `with_config(tags=[...])` 合并进运行时 config | 成立；多来源 tags 按**排序去重并集**合并（`config.py:352-355`），不可假设顺序 | 吻合 |
| RunnableConfig 结构 | EXP-C3 | TypedDict/dict 形态，含 `tags` 与 `callbacks` | 成立：`total=False` TypedDict 共 8 字段；`ensure_config` 默认只填 5 键，未设键需 `config.get(k)` | 吻合 |
| Chat 构造参数 | EXP-L1 | 可按 `LLMConfig` 字段直接构造；anthropic max_tokens 有差异 | 成立：统一 alias 面 `model/api_key/base_url/temperature/max_tokens/timeout` 两家全落位；ChatAnthropic 按模型家族自动填 max_tokens（64000/4096），大 max_tokens 非流式会触发 SDK "Streaming is required" ValueError，spec-04 须显式传 max_tokens | 吻合（附陷阱备忘） |
| AIMessage 形态 | EXP-L2 | 成功输出取 `.content` 写 `{"response", "model"}` | 成立：两家均返 `AIMessage`，纯文本 `.content` 为 str；`usage_metadata` 标准化 `{input/output/total_tokens}`；模型名取 `response_metadata["model_name"]` | 吻合 |
| 限流异常层级 | EXP-L3 | 消息命中 429/rate 的谓词可判定（AD-03） | SDK 异常原样透出（`RateLimitError → APIStatusError`，带 `status_code`）；429 消息含 "429"/"rate"，5xx 不含；定稿谓词 `status_code == 429 or >= 500` | 吻合（谓词升级为 status_code 口径） |
| SecretStr 脱敏 | EXP-L4 | 密钥不落日志（H6） | 成立：两家 api_key 均 `SecretStr`，repr/str/model_dump/model_dump_json 全部脱敏，`lc_secrets` 已声明 | 吻合 |
| anthropic 版本区间 | EXP-L5 | 存在与 langchain 1.x 同代际的 1.x | 成立：`>=1.0,<1.1` 解析 1.0.4，METADATA 声明 `langchain-core<2.0.0,>=1.0.4` 与冻结的 1.0.4 配套；约束定稿维持不变 | 吻合 |
| 最小依赖集 | EXP-X1 | 引擎只依赖 langgraph + langchain-core + 两 provider 包 | 成立：四包均不运行时依赖顶层 `langchain`；clean-room 导入后 `sys.modules` 无顶层 `langchain` | 吻合 |

---

## 闭环签字

- [x] EXP-G1..G8 全部闭环（执行人 / 日期：spec-00 TC4 执行工程师 / 2026-07-30）
- [x] EXP-C1..C3 全部闭环（执行人 / 日期：spec-00 TC5 执行工程师 / 2026-07-30）
- [x] EXP-L1..L5 全部闭环（执行人 / 日期：spec-00 TC6 执行工程师 / 2026-07-30）
- [x] EXP-X1..X2 全部闭环（执行人 / 日期：X1 由 TC5 执行工程师、X2 总表由 TC6 执行工程师 / 2026-07-30）
- [x] 偏差项已全部走 CONTRACT §11 变更流程并回写（无偏差写"无"）：仅 EXP-G8 一项偏差，2026-07-30 已决策采纳方案 1（构建期预声明 `{node_name}_result` 双写键）并回写 CONTRACT §4.3 与 spec-02；L 组无偏差（L3 谓词升级为 status_code 口径属实现建议优化，非契约偏差）

> **门禁声明**：本文件全部 EXP 闭环前，spec-01..07 中任何涉及 langgraph/langchain 1.x API 行为的编码不得开工（R-EXP）。
