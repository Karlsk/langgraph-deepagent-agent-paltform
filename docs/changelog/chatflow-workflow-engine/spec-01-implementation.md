# Chatflow via AgentApp `engine="workflow"` — 实施 Spec

> 状态：待评审 · 日期：2026-09-16 · 范围：MVP（单 chunk 流式、无 token 级流式、无 interrupt）

## 1. 背景与决策

### 1.1 问题

需要让声明式 workflow 引擎支持「对话式」使用（chatflow）：用户通过聊天界面多轮对话，
每轮把对话历史喂给 workflow，workflow 产出回复。

### 1.2 决策：复用 AgentApp 层，不在 workflow 引擎加 type 字段

代码库**已为此预留槽位**，无需新架构：

| 既有设施 | 位置 | 作用 |
|---|---|---|
| `AgentApp.engine` 字段 | `app/models/agent_assets.py:125` | `"deepagents"`（默认）/ `"workflow"`（预留） |
| `WorkflowAppRuntime` 占位类 | `app/services/agents/runtime.py:932` | 5 个原语全部 `raise NotImplementedError("workflow engine runtime reserved")` |
| runtime 路由 | `runtime.py:1273` | `engine == "workflow"` → `WorkflowAppRuntime` |
| `AgentAppRuntime` 基类 | `runtime.py:306` | 已封装会话/流式/历史/记忆/鉴权/checkpointer/压缩/L2 上下文 |
| chat 消费层 | `chat_service.py` / `sessions_service.py` | 只调公共模板方法 `ainvoke`/`astream`/`get_chat_history`，**引擎无关** |

**核心结论**：
- workflow 引擎保持纯净（单次、结构化 I/O、无 checkpointer）——不改 `app/workflow/`。
- chatflow = `AgentApp(engine="workflow", workflow_id=<已注册 workflow>)`。
- chat API/service 层**零改动**——实现 `WorkflowAppRuntime` 的 5 个原语即可贯通。
- **不**在 `WorkflowDefinition` 上加 `type`/`mode` 字段。

### 1.3 关键设计决策

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | runtime 如何驱动 workflow | **薄包装图**（wrapper LangGraph） | 包装图持有 `messages` channel + checkpointer，workflow 作为黑盒在单节点内被调用；引擎零改动，基类 5 原语直接复用 |
| D2 | 多轮状态 | **无状态重放** | 包装图 checkpointer 持有对话历史；每轮把完整历史喂给 workflow；workflow 是 `(history → reply)` 纯函数 |
| D3 | runtime 如何拿到 registry | **host 级桥接模块** | `get_runtime` 无 request/app 上下文；桥接模块由 main.py 启动时注入，符合红线（`app/workflow/` 不持全局，host 层持引用） |
| D4 | 流式 | **MVP 单 chunk** | `execute_workflow` 同步返回 RunResult；包装节点跑完一次性 yield 最终 AIMessage。token 级流式延后 |
| D5 | 回复提取约定 | **`output["messages"][-1]` 的 AI 消息** | chatflow workflow 的 state_schema 应含 `messages` channel；与 S21 输入合成对齐。结构化输出 workflow 的 `output_key` 延后 |
| D6 | interrupt/HIL | **MVP 不支持** | workflow 引擎无 interrupt；包装图 `state.next` 恒空，基类模板方法自动走正常路径；`AgentApp.interrupt_on` 对 workflow 引擎忽略 |
| D7 | 发布路径 | **engine 感知分支** | `publish_agent_app` 当前深度绑定 deepagents workspace 物化；workflow app 无 skills/subagents/workspace，需绕过 |

## 2. 架构

### 2.1 包装图（D1）

```
StateGraph(messages: Annotated[list, add_messages])
  └─ node "chatflow":
       result = await run_in_threadpool(registry.execute_workflow, workflow_id, {"messages": state["messages"]})
       reply  = extract_reply(result.output)          # D5
       return {"messages": [AIMessage(content=reply)]}
  └─ compile(checkpointer=shared_checkpointer)
```

- 输入：包装图 `messages` = 完整对话历史（user/ai 交替）。
- `execute_workflow` 内部 `synthesize_run_input`（S21）见 `messages` 为真值 → 原样透传，
  workflow 的 `messages` channel 收到完整历史。
- workflow 跑完，从 `RunResult.output` 提取最后一条 AI 消息作为回复。
- 包装图只把**这一条** AIMessage 追加进自己的历史 → 历史保持干净
  `[user1, ai1, user2, ai2, ...]`，workflow 内部的工具消息/中间消息不污染。

### 2.2 数据流（一轮对话）

```
chat_service.stream(messages, session_id)
  → rt.astream(...)                         [基类模板方法，引擎无关]
    → _prepare_input → {"messages": dump(messages)}
    → _stream(graph_input, config)          [WorkflowAppRuntime 原语]
      → wrapper_graph.astream(stream_mode="messages")
        → chatflow 节点 → execute_workflow(workflow_id, {"messages": history})
        → RunResult.output → AIMessage(reply)  [单 chunk]
    → _get_state → 无 interrupt（D6）
    → _fire_memory_add / _fire_context_record / 指标  [基类横切，自动生效]
```

### 2.3 registry 桥接（D3）

新模块 `app/services/agents/workflow_bridge.py`（host 层，非 `app/workflow/`）：

```python
_REGISTRY: WorkflowRegistry | None = None

def set_workflow_registry(registry: WorkflowRegistry) -> None: ...
def get_workflow_registry() -> WorkflowRegistry:  # None → RuntimeError
```

`app/main.py` 启动构建 registry 后调用 `set_workflow_registry(app.state.workflow_registry)`。
`WorkflowAppRuntime` / 包装图构建器从桥接模块取 registry。

## 3. 分阶段任务（TDD）

> 每阶段：RED → GREEN → REFACTOR；门禁 `uv run pytest -m unit` + `make lint` + `make typecheck`；
> 约定式提交。测试零真实网络/LLM。

### Phase 1 — 契约与 schema：`workflow_id` + `engine` 可设置

**后端**
- `app/models/agent_assets.py`：`AgentApp` 加 `workflow_id: Optional[str] = Field(default=None, max_length=64)`。
- `alembic/versions/<rev>_add_agent_app_workflow_id.py`：加列（nullable）。
- `app/schemas/agent_apps.py`：
  - `AgentAppCreate` 加 `engine: Literal["deepagents","workflow"] = "deepagents"` +
    `workflow_id: Optional[str] = None`。
  - `AgentAppUpdate`：**不**加 engine（创建后不可变，同 name）；可加 `workflow_id`（允许改绑）。
  - `AgentAppRead` 加 `workflow_id: Optional[str]`。
- 创建校验（实现落点订正）：真实代码中创建逻辑内联在 `app/api/v1/apps.py`
  的 `create_agent_app`，无独立 service 层创建方法。校验改用 `AgentAppCreate`
  的 Pydantic `model_validator`（FastAPI 自动转 422）：`engine=="workflow"` 时
  `workflow_id` 必填且非空，否则 `ValueError`；`engine=="deepagents"` 时忽略
  `workflow_id`。create handler 把 `engine` / `workflow_id` 透传给 `AgentApp(...)`。

**RED**：`tests/unit/agents/test_agent_app_workflow_fields.py`
- 建 workflow app（engine + workflow_id）→ 落库正确。
- engine="workflow" 缺 workflow_id → 422。
- engine 默认 deepagents，workflow_id=None。
- Read 序列化含 workflow_id。

**DoD**：迁移可 upgrade/downgrade；schema 测试绿；lint/typecheck 过。

### Phase 2 — registry 桥接

**后端**
- 新增 `app/services/agents/workflow_bridge.py`（§2.3）。
- `app/main.py`：构建 registry 后 `set_workflow_registry(...)`。

**RED**：`tests/unit/agents/test_workflow_bridge.py`
- 未设置 → `get_workflow_registry()` raises RuntimeError。
- set 后 → 返回同一实例。

**DoD**：桥接测试绿；main.py 启动注入（启动冒烟）。

### Phase 3 — `WorkflowAppRuntime` + 包装图（核心）

**后端**
- 新增包装图构建器（建议 `app/services/agents/chatflow_graph.py`）：
  - `build_chatflow_graph(workflow_id, checkpointer) -> CompiledStateGraph`
  - `extract_reply(output: dict) -> str`（D5：`output["messages"][-1]` AI 文本；
    无 messages/无 AI 消息 → 回退 `json.dumps(output, ensure_ascii=False)`，截断保护）。
- 实现 `WorkflowAppRuntime` 5 原语（镜像 `DeepAgentsAppRuntime`，但 `_stream` 更简单）：
  - `__init__(*, app_cfg, graph, checkpointer, resolved_model_name)`
  - `_get_state` → `graph.aget_state(config)`
  - `_run` → `graph.ainvoke(graph_input, config)`
  - `_stream` → `graph.astream(graph_input, config, stream_mode="messages")`，
    仅 yield coordinator AIMessage 文本（无 subagent 计时逻辑）。
  - `_history` → `state.values["messages"]`
  - `_clear` → checkpointer 删除线程（无 checkpointer → RuntimeError，同 DeepAgents）
  - **不**覆写 `_build_resume_value`（无 interrupt，基类默认即可）。
- `runtime.py:1273` 分支改为：构建包装图 → 实例化 `WorkflowAppRuntime`（传 graph + checkpointer）。

**RED**：`tests/unit/agents/test_workflow_runtime.py`
- 用 fake registry（`execute_workflow` 返回固定 RunResult，output 含 messages=[HumanMessage, AIMessage("hi")]）。
- `ainvoke([user msg], session_id)` → 返回 `[Message(assistant, "hi")]`。
- 多轮：第二次 ainvoke 时 fake registry 收到的 `input_data["messages"]` 含上一轮 ai 回复（验证重放）。
- `extract_reply`：有 messages → 最后 AI 文本；无 messages → JSON 回退。
- `get_chat_history` → 投影历史；`clear_chat_history` → 清空。
- `astream` → 至少一个 message chunk 含 "hi"。
- workflow 执行失败（RunResult.status="failed"）→ 回复含错误摘要或抛错（约定：抛错，由基类 `agent_app_stream_failed` 记录）。

**DoD**：runtime 测试绿；chat_service 现有测试不回归（公共方法签名未变）。

### Phase 4 — 发布路径 engine 感知（D7）

**后端**
- `publish_agent_app`：`engine=="workflow"` 分支——
  - 跳过 skill 物化 / `agent_dir` / `workspace_hash` / `agent_workspace_status`。
  - 校验 `workflow_id` 在 registry 已注册（`has_workflow`），否则 `AgentAppNotPublishedError`/422。
  - `published_hash` = sha256 over `(workflow_id + workflow 内容 hash)`（用 store 的 content_hash 或 definition 序列化）。
  - `status="published"`、`version+=1`、失效用户层缓存（保留）。
- `ensure_all_agent_workspaces` / 启动 bootstrap：workflow app 跳过 workspace 物化。
- `associate_user_with_app`：workflow app 跳过 `materialize_to_user_combined`（无 user 层 skills）。

**RED**：`tests/unit/agents/test_publish_workflow_app.py`
- 发布 workflow app（registry 含该 workflow）→ status=published，无 agent_dir/workspace_hash。
- workflow_id 未注册 → 发布失败（明确异常）。
- deepagents app 发布路径不回归。

**DoD**：发布测试绿；`get_runtime` 对已发布 workflow app 返回 `WorkflowAppRuntime`。

### Phase 5 — 前端：engine 选择器 + workflow 下拉

**前端**（`agent-web/src/views/agent/AgentList.vue` + API 层）
- `src/api/agent.ts`（或对应模块）：`AgentAppCreate/Update/Read` 类型加 `engine` / `workflow_id`。
- 表单对话框：
  - 加「引擎类型」`el-select`（deepagents / workflow），**创建时可选，编辑时禁用**（不可变）。
  - `engine=="workflow"` 时：显示「绑定工作流」`el-select`（选项来源 `GET /workflows` 列表），
    隐藏 system_prompt/allowed_tools/model/skill_names/subagent_names/interrupt_on（deepagents 专属）。
  - `engine=="deepagents"` 时：保持现有表单。
- 列表：加「引擎」列或标签区分类型。
- 提交逻辑：workflow app 只发 `{name, engine, workflow_id}`。

**RED**：`tests/components/agent-list.spec.ts`（扩展现有）
- 选 workflow 引擎 → 显示 workflow 下拉，隐藏 deepagents 字段。
- 创建 workflow app → payload 含 engine + workflow_id，不含 skill_names 等。
- 编辑时 engine 选择器禁用。

**DoD**：`npx vue-tsc --build --force` 过；组件测试绿；`npm run build` 过。

### Phase 6 — 验证 + E2E

- 后端：`uv run pytest -m unit` 全绿；`make lint` / `make typecheck` 过。
- 前端：`npx vitest run`（除 2 个已知 agent-list 预存失败外全绿）。
- E2E（无 Docker，按记忆 `project-e2e-verification-without-http-or-browser`）：
  - 启动 dev server，注册一个含 `messages` channel 的简单 chatflow workflow（如单 react/llm 节点）。
  - 创建 `AgentApp(engine="workflow", workflow_id=...)` 并发布。
  - 通过 chat 接口多轮对话，验证：回复正确、历史累积、记忆/L2 写入触发。

## 4. 改动文件清单

| 文件 | 动作 | Phase |
|---|---|---|
| `app/models/agent_assets.py` | 加 `workflow_id` 列 | 1 |
| `alembic/versions/<rev>_add_agent_app_workflow_id.py` | 新建迁移 | 1 |
| `app/schemas/agent_apps.py` | Create/Update/Read 加 engine/workflow_id | 1 |
| `app/services/agents/agent_apps_service.py` | 创建校验 + 发布 engine 分支 + bootstrap 跳过 | 1,4 |
| `app/services/agents/workflow_bridge.py` | 新建桥接模块 | 2 |
| `app/main.py` | 启动注入 registry | 2 |
| `app/services/agents/chatflow_graph.py` | 新建包装图构建器 + extract_reply | 3 |
| `app/services/agents/runtime.py` | 实现 WorkflowAppRuntime + 改 1273 分支 | 3 |
| `agent-web/src/api/agent*.ts` | 类型加 engine/workflow_id | 5 |
| `agent-web/src/views/agent/AgentList.vue` | engine 选择器 + workflow 下拉 + 条件字段 | 5 |
| `tests/unit/agents/test_*.py` | 4 个新测试文件 | 1-4 |
| `agent-web/tests/components/agent-list.spec.ts` | 扩展 | 5 |

## 5. MVP 范围外（延后）

1. **token 级流式**：需把内层 workflow 图的 `astream_events` 透传到包装图，或给 registry 加异步流式路径（会绕过 execute_workflow 的日志收集，需权衡）。
2. **interrupt / HIL**：workflow 引擎需先支持 interrupt；AgentApp.interrupt_on 映射。
3. **结构化输出 workflow**：`output_key` 约定（非 messages channel 的回复提取）。
4. **workflow 级 checkpointer**：当前无状态重放；若需 workflow 内部跨轮状态，另议。
5. **engine 变更**：创建后不可变；如需 deepagents↔workflow 互转，单独迁移逻辑。

## 6. 风险

| 风险 | 缓解 |
|---|---|
| 包装图 `messages` 与 workflow `messages` 语义混淆 | 明确约定：包装图历史只含 user/ai；workflow 内部消息不回灌包装图（只提取最后 AI 回复） |
| 无状态重放下长对话 token 膨胀 | 复用基类压缩（SummarizationMiddleware 作用于包装图 messages channel）；MVP 可接受 |
| `execute_workflow` 同步阻塞事件循环 | 包装节点用 `run_in_threadpool`（同 api.py execute 路由模式） |
| registry 桥接全局态测试污染 | 桥接模块提供 reset；测试用 fixture set/teardown |
| workflow 未注册即绑定 | 发布期 `has_workflow` 校验（Phase 4）；创建期仅校验非空（registry 可能后启动） |

## 7. 与既有契约的关系

- **不改** `docs/workflow-reimpl-plan/spec/CONTRACT.md` 冻结面（`execute_workflow` 签名不变）。
- **不改** `app/workflow/` 任何模块（红线：引擎不依赖 host；host 层 runtime 可 import 引擎）。
- 复用 S21（`synthesize_run_input`）：chatflow 依赖其 `messages` 透传规则。
- `AgentAppRuntime` 基类横切语义（记忆/L2/指标/压缩）对 workflow 引擎自动生效，无需特判。
