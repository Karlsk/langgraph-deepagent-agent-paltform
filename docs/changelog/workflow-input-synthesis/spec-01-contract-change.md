# spec-01 · 契约变更：运行输入合成（S21）——`input` 自动转换为 `messages`

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M0 | 后端（文档） | 0.5 | 无 | **S21**（新增）、§4.10（新增公开函数） |

> 本文档为**纯契约变更**，不含代码实现。按 CONTRACT §11「禁止先改代码后补契约」要求先行落地。

## 1. 动机

执行一个含 LLM 节点的工作流，调用方必须手写完整的 langchain 消息结构：

```json
{"input": {"messages": [{"role": "user", "content": "帮我查一下今天的天气"}]}}
```

而 `messages` 是**引擎内置通道**（LLM 节点契约 S5 从 `state.messages` 取对话内容），并非业务字段。现状的三个后果：

1. **前端体验差**：执行对话框只有裸 JSON 文本框（`WorkflowExecuteDialog.vue`），预设示例甚至不含 `messages`，普通用户无法凭直觉执行一个工作流。
2. **文档里已出现人工合成的痕迹**：`docs/workflow-manual-testing.md:213` 的示例命令同时写 `"input": "please review"` 与 `"messages": [{"role": "user", "content": "please review"}]`——同一句话抄两遍，纯手工维护。
3. **CLI 与 API 重复受害**：`registry.execute_workflow` 是两者共用的唯一咽喉点，但它在 `graph.invoke` 前对入参零校验、零加工（`registry.py:164-197`），缺失 channel 静默取声明默认值，LLM 节点直到运行期才以 `ValueError` 报「no messages provided」。

目标：调用方只写**业务输入**——一个主输入 `input`（字符串）加 state_schema 声明的自定义字段；`messages` 由引擎在运行入口自动合成。

## 2. 影响面

### 2.1 契约签名变更（**新增**模块级公开函数，无既有签名改动）

| 位置 | 变更 |
| --- | --- |
| §4.10 `app/workflow/registry.py` | 新增 `def synthesize_run_input(definition: WorkflowDefinition, input_data: dict[str, Any]) -> dict[str, Any]`（纯函数：返回新 dict，**不 mutate 入参**） |

`WorkflowRegistry.execute_workflow`、`api.py` 的 execute 路由、`cli.py` 的 `--input` 解析**签名与代码路径均不变**：合成发生在 `execute_workflow` 内部 `graph.invoke` 之前的一行，API 与 CLI 自动同享。

### 2.2 行为语义新增

**S21（运行输入合成）** — 详见 CONTRACT §6。要点：

- `execute_workflow` 在 `graph.invoke` 前调用 `synthesize_run_input(definition, input_data)`，规则按序：
  1. `input_data["messages"]` 存在且为真值 → **原样透传**（显式消息优先，向后兼容既有调用方与测试）；
  2. 否则 `input_data["input"]` 为非空 `str` → **附加式**注入 `messages=[{"role": "user", "content": <input>}]`（不删除、不改写任何用户键，`input` 键本身仍写入 state，供 `body_template` 的 `{input}` 占位符继续使用）；
  3. `input` 缺失、非 `str`、或空白 → **不合成**（缺失 channel 按 S14/EXP-G8 走声明默认值；LLM 节点空 messages 仍按其现有 `ValueError` 失败，不引入新的静默路径）；
  4. 合成对**所有**工作流生效，**不**以「是否含 llm 节点」为条件——引擎禁止按节点类型特判输入（R2 通用性）；无 llm 节点时多余的 `messages` 键被 langgraph 静默丢弃（S14），无副作用。
- **wire 形态双兼容**：`{"input": "hi"}`（`input` 为 str 时 `api.py:318-320` 的 inner 非 dict，回退整包）与 `{"input": {"input": "hi", "user_id": "u1"}}`（inner 为 dict，解包后内层 `input` 键命中合成）两种形态均正确合成。

### 2.3 不变更项（显式声明）

- `state.py` 的「不做字段名特判」原则**不受影响**：该原则约束的是**状态模型构建**（reducer/类型映射），S21 约束的是**运行入口的输入预处理**，两者层面不同；合成结果仍是一个普通 state 键。
- `LLMNode` 的取数逻辑（`state_dict.get("messages") or self.messages`）逐字不变。
- 前端执行对话框的简化（简单模式表单 + 高级 JSON 模式）为**纯前端变更**，不涉及契约；其提交形状 `{input, ...custom}` 经既有 `executeWorkflow` 包装为 `{"input": {...}}`，由 S21 消费。

## 3. 备选方案对比（R-EXP 第 5 条：给 2-3 个附影响面）

| 方案 | 描述 | 影响面 | 结论 |
| --- | --- | --- | --- |
| A. registry 边界合成（本方案） | `execute_workflow` 内一行调用纯函数 | 引擎 1 文件 +1 函数；API/CLI/未来入口全受益；单测纯函数易写 | **采纳** |
| B. 仅前端合成 | 对话框提交前拼 `messages` | 后端零改动；但 CLI 与直接调 API 者继续手写消息结构，文档人工抄写问题依旧 | 否决 |
| C. LLMNode 内回退读 `input` | `messages` 空时改读 `state.input` | 触碰 S5 冻结语义；http 节点等其他消费者拿不到消息；节点层出现输入特判 | 否决 |

## 4. 验收

- 单测：`synthesize_run_input` 四卡（str 合成 / messages 存在跳过 / dict 或缺失 no-op / 不 mutate）+ execute 级合成卡（FakeLLM 断言收到 user 消息）+ CLI 卡 + API 卡。
- 门禁：`uv run pytest -m unit`、`make lint`、`make typecheck` 全绿；既有 execute 测试（含 `test_execute_embeds_execution_logs` 的 `{"input": "hi"}` 形态）不回归。
- E2E：docker-up 全栈后，执行对话框简单模式只填主输入即可跑通含 LLM 节点的工作流；轨迹抽屉中 llm 节点输入摘要消息条数为 1。
