# 1.x API 探索任务书与报告（api-exploration-1x）

| 项 | 值 |
| --- | --- |
| 文档版本 | v1.0（任务书）；实测栏待 spec-00 TC4-6 填写 |
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
- **实测结果**：_（待填）_
- **结论**：_（待填：吻合 / 偏差 / 阻塞）_

### EXP-G2 Annotated reducer 的 channel 推断

- **API 点**：`Annotated[list, operator.add]` / `Annotated[T, _last]` 在 pydantic 字段上被识别为聚合 channel；未标注字段为 LastValue（后写覆盖）。
- **规划文档假设**：reducer 放 annotation 位置（二元组 `(annotation, FieldInfo)`）即被识别；未标注字段默认 LastValue（K3/D2）。
- **验证方法**：SRC（langgraph channel 推断逻辑）+ TEST（两节点各返回 `{"history": [x]}`，断言终态合并为 `[x, y]`；未标注字段两次写后者覆盖）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-G3 add_node 的输入形态与返回值合并语义

- **API 点**：`graph.add_node(name, RunnableLambda(func))`；节点 func 收到的输入形态（dict 还是模型实例）、返回 dict 如何合并入 state。
- **规划文档假设**：func 收到可 `convert_state_to_dict` 处理的对象；返回 `dict` 为**部分更新**，按 channel 语义合并（K4/R3）。
- **验证方法**：SRC + TEST（EchoNode 返回部分键，断言未返回键保持原值；记录 func 实参类型）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-G4 add_conditional_edges 的 1.x 签名与 path_map 行为

- **API 点**：`graph.add_conditional_edges(source, path_func, path_map)`；`path_func(state) -> str` 返回值经 `path_map` 映射到目标节点。
- **规划文档假设**：三参形态可用；`path_map` 为 `dict[str, str]`，可把 `"END"` 映射为 `END` 常量（K7）。
- **验证方法**：SRC（`langgraph/graph/graph.py` 签名与文档字符串）+ REPL（最小条件图编译 + invoke）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-G5 START / END 常量

- **API 点**：`from langgraph.graph import START, END`；`add_edge(START, x)` / `add_edge(x, END)` / `set_entry_point`。
- **规划文档假设**：常量可导入；`set_entry_point(name)` 等价于 `add_edge(START, name)`；边 target `"END"` 字面量需映射为 `END` 常量（K6）。
- **验证方法**：SRC（`langgraph/constants.py` 或 `langgraph/graph/__init__.py` 导出面）+ REPL。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-G6 compile() 产物与 invoke 的输入输出形态

- **API 点**：`graph.compile()` 返回类型；`compiled.invoke(input_dict, config)` 的输入校验、输出形态；`config`（tags/callbacks）向节点 func 的传播。
- **规划文档假设**：返回 `CompiledStateGraph`；`invoke(dict)` 返回终态 dict（含 extra 键）；config 沿 Runnable 协议传播（K6/K8）。
- **验证方法**：SRC + TEST（带 config 的 invoke，断言输出形态与 config 可达性）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-G7 节点异常的传播行为

- **API 点**：节点 func 抛出自定义异常（如 `ConditionNotMatchedError`）时，`invoke` 如何传播（原样 or 包装）；无 checkpointer 时的行为。
- **规划文档假设**：异常原样向调用方传播，`execute_workflow` 可捕获并重抛（R6/S6）；本期**无 checkpointer**。
- **验证方法**：TEST（节点抛 `ConditionNotMatchedError` → `pytest.raises` 断言类型与消息不被吞/不包装）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-G8 langgraph 1.0.2 × pydantic 2.11 兼容性边界

- **API 点**：动态模型（`create_model` + `extra="allow"`）在 langgraph 输入校验/序列化路径上的行为；extra 键在终态中的保留。
- **规划文档假设**：extra 键可写入并在终态保留（K2 Dify 风格）。
- **验证方法**：TEST（节点写未声明键 `foo` → 终态含 `foo`；记录有无告警/异常）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

---

## EXP-C Runnable 契约（langchain-core 1.0.4）— 门禁 spec-03 / spec-07

### EXP-C1 RunnableLambda func 签名规则

- **API 点**：`RunnableLambda(func)` 的 func 支持哪些签名（单参 `func(state)`；双参 `func(state, config)` 的注入条件——参数名/类型注解要求）。
- **规划文档假设**：单参 `func(state) -> dict` 可直接使用（K4）；若声明第二参，langchain 注入 `RunnableConfig`。
- **验证方法**：SRC（`langchain_core/runnables/base.py` 中 `RunnableLambda` 的 `acall`/调用逻辑与 config 注入判定）+ REPL（两种签名各跑通）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-C2 with_config(tags=[name]) 的 tags 透传

- **API 点**：`RunnableLambda(func).with_config(tags=[name])` 后，tags 是否出现在 func 收到的 config 中（K4 tags 约定依赖此行为）。
- **规划文档假设**：`with_config` 的 tags 合并进运行时 config，可在 func 内（若接收 config）或 callback 中观测。
- **验证方法**：SRC + REPL（双参 func 打印/断言 `config.get("tags")`）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-C3 RunnableConfig 结构与 callbacks 字段

- **API 点**：`RunnableConfig` 的字段集（tags/metadata/callbacks/run_name 等）与类型。
- **规划文档假设**：`config: RunnableConfig` 为 TypedDict/dict 形态，含 `tags` 与 `callbacks`。
- **验证方法**：SRC（`langchain_core/runnables/config.py`）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

---

## EXP-L LLM 客户端（langchain-openai 1.0.2 + langchain-anthropic 新增）— 门禁 spec-04

### EXP-L1 ChatOpenAI / ChatAnthropic 1.x 构造参数

- **API 点**：`ChatOpenAI(model=?, api_key=?, base_url=?, temperature=?, max_tokens=?, timeout=?, max_retries=?)` 与 `ChatAnthropic` 对应参数名与默认值；`extra_params` 的透传通道（`model_kwargs`?）。
- **规划文档假设**：可按 `LLMConfig` 字段直接构造；anthropic 的 `max_tokens` 可能有必填/默认值差异。
- **验证方法**：SRC（两包 `chat_models.py` 的字段定义）+ REPL（离线实例化，env 设哑密钥，断言属性落位）。
- **实测结果**：_（待填，须含 langchain-anthropic 实际装入版本号）_
- **结论**：_（待填）_

### EXP-L2 invoke 返回 AIMessage 形态

- **API 点**：`.invoke(messages)` 返回 `AIMessage`；`.content` 形态（str vs list blocks）；token usage 元数据字段名（`usage_metadata`?）。
- **规划文档假设**：成功输出取 `<AIMessage>.content` 写入 `{"response": content, "model": model_name}`（Phase 4 契约）。
- **验证方法**：SRC + MOCK（以假 transport/假 client 驱动一次 invoke，记录返回结构）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-L3 限流/5xx 异常类型层级

- **API 点**：429 / 5xx 时 langchain 抛出的异常类型（`openai.RateLimitError` / `anthropic.RateLimitError` 是否原样透出）、异常消息文本是否含 "429"/"rate" 字样、状态码属性名。
- **规划文档假设**：tenacity retry 谓词可依据"异常消息命中 429/rate（不区分大小写）"判定（等价原手写循环口径，AD-03）。
- **验证方法**：SRC（两包异常处理路径）+ MOCK（构造 429 响应经 mock transport 触发，记录异常类型与 str(exc)）。
- **实测结果**：_（待填，须给出谓词建议写法）_
- **结论**：_（待填）_

### EXP-L4 api_key 的 SecretStr 脱敏行为（H6 依赖）

- **API 点**：构造时传入 `api_key` 后，实例 `repr()`/日志序列化是否脱敏（pydantic `SecretStr`）。
- **规划文档假设**：密钥不落日志（H6 验证依赖实例不泄露密钥值）。
- **验证方法**：REPL（哑密钥实例化 → `repr(instance)` / `str(instance)` 断言不含密钥值）。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-L5 langchain-anthropic 版本配套区间

- **API 点**：与 langchain-core 1.0.4 / langchain 1.0.5 配套的 `langchain-anthropic` 主版本区间（写入 pyproject 的约束写法，AD-05）。
- **规划文档假设**：存在与 langchain 1.x 同代际的 langchain-anthropic 1.x。
- **验证方法**：SRC/REPL（`uv add` 后查 `uv.lock` 解析结果与 `langchain_anthropic.__version__`；确认 import 链无冲突）。
- **实测结果**：_（待填，须给出最终 pyproject 约束文本）_
- **结论**：_（待填）_

---

## EXP-X 1.0 迁移影响 — 门禁 spec-00 / spec-01

### EXP-X1 最小依赖集

- **API 点**：`app/workflow/` 直接 import 的包面（`langgraph`、`langchain_core`、`langchain_openai`、`langchain_anthropic`）；顶层 `langchain` 包（1.0.5）是否仍被需要。
- **规划文档假设**：引擎只依赖 `langgraph` + `langchain-core` + 两个 provider 包；顶层 `langchain` 可不被引擎直接 import（现有 app 代码另有用途，不动）。
- **验证方法**：SRC（规划 import 清单逐一核对 `uv pip show`）+ 结论写入 spec-00。
- **实测结果**：_（待填）_
- **结论**：_（待填）_

### EXP-X2 0.x 假设 vs 1.x 实测对照总表

- **API 点**：规划文档（00-03）中全部基于 langgraph 0.2-0.7 / langchain 0.x 的 API 假设点的汇总对照。
- **填写方式**：EXP-G/C/L 各项闭环后，把结论浓缩进下表，作为 CONTRACT 附录长期留存。

| 假设点 | 来源 | 0.x 假设 | 1.x 实测 | 结论 |
| --- | --- | --- | --- | --- |
| pydantic state schema | EXP-G1 | _（待填）_ | _（待填）_ | _（待填）_ |
| reducer channel 推断 | EXP-G2 | _（待填）_ | _（待填）_ | _（待填）_ |
| 节点输入/返回合并 | EXP-G3 | _（待填）_ | _（待填）_ | _（待填）_ |
| add_conditional_edges path_map | EXP-G4 | _（待填）_ | _（待填）_ | _（待填）_ |
| START/END | EXP-G5 | _（待填）_ | _（待填）_ | _（待填）_ |
| compile/invoke 形态 | EXP-G6 | _（待填）_ | _（待填）_ | _（待填）_ |
| 异常传播 | EXP-G7 | _（待填）_ | _（待填）_ | _（待填）_ |
| extra 键保留 | EXP-G8 | _（待填）_ | _（待填）_ | _（待填）_ |
| RunnableLambda 签名 | EXP-C1 | _（待填）_ | _（待填）_ | _（待填）_ |
| tags 透传 | EXP-C2 | _（待填）_ | _（待填）_ | _（待填）_ |
| RunnableConfig 结构 | EXP-C3 | _（待填）_ | _（待填）_ | _（待填）_ |
| Chat 构造参数 | EXP-L1 | _（待填）_ | _（待填）_ | _（待填）_ |
| AIMessage 形态 | EXP-L2 | _（待填）_ | _（待填）_ | _（待填）_ |
| 限流异常层级 | EXP-L3 | _（待填）_ | _（待填）_ | _（待填）_ |
| SecretStr 脱敏 | EXP-L4 | _（待填）_ | _（待填）_ | _（待填）_ |
| anthropic 版本区间 | EXP-L5 | _（待填）_ | _（待填）_ | _（待填）_ |
| 最小依赖集 | EXP-X1 | _（待填）_ | _（待填）_ | _（待填）_ |

---

## 闭环签字

- [ ] EXP-G1..G8 全部闭环（执行人 / 日期：____）
- [ ] EXP-C1..C3 全部闭环（执行人 / 日期：____）
- [ ] EXP-L1..L5 全部闭环（执行人 / 日期：____）
- [ ] EXP-X1..X2 全部闭环（执行人 / 日期：____）
- [ ] 偏差项已全部走 CONTRACT §11 变更流程并回写（无偏差写"无"）：____

> **门禁声明**：本文件全部 EXP 闭环前，spec-01..07 中任何涉及 langgraph/langchain 1.x API 行为的编码不得开工（R-EXP）。
