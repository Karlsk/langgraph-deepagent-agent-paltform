# G4 Spec：Chat 交互层（v1）

> **主题**：X-Session-Id 寻址的 chat 端点族（非流式 / SSE 流式 / 历史渲染 / 灾难重建）+ HIL 交互闭环 + subagent_trace 挂链 + 前端聊天界面。
> **关联文档**：`overview.md`（路线图）、`spec-g1-auth.md`（§1.1 X-Session-Id 预留，本期兑现）、`spec-g2-workspace.md`（get_runtime / User 层）、`spec-g3-session.md`（L2 JSONL / read_or_rebuild_l2 / RunTracer 与 rebuild 留痕 §11.11）
> **目标读者**：后端架构师 + 前端工程师
> **风险等级**：中（议题 9 收口定稿：新语义面 SSE 首例 / auto-approve / rebuild spike 与利好面 runtime 零改动 / 回滚干净 / 前后端独立相抵）
> **估算工时**：3 周（议题 9 收口定稿；初评 2.5 周上调——后端轻但前端重，8 文件 + SSE 客户端 + 帧状态机 + 测试，rebuild spike 有不确定性）
> **修订记录**：
> - 2026-08-27 议题 1 落盘：端点拓扑定案——寻址用 X-Session-Id header（G1 §1.1 原案兑现，CRUD 保持 path 参数，管理面/交互面两种视图并存）；端点集合 3+1（POST /chat 非流式信封 + POST /chat/stream SSE + GET /messages 历史 + POST /rebuild 灾难恢复本期落地）；clear 端点砍掉（DELETE 级联已覆盖）；workflow 引擎接入排除（独立 spec 体系）；限流 key 复用 3 个现成 key（chat/chat_stream/messages）+ 新增 rebuild。
> - 2026-08-27 议题 2 落盘：SSE 事件协议升级 type 多事件（message/interrupt/summary/error/done）+ 15s 心跳；interrupt 投影稳定 schema（tool+args，不透出 deepagents 内部字段）；压缩事件本期推送（实现取轮末 state 检测，避免复合流改造）；非流式端点 interrupt 自动批准（用户决策：循环到完成，上限 10 次可配，流式端点保持人在环审批）。
> - 2026-08-27 议题 2 澄清：SSE 事件协议增加 tool_call type（6 类 type 帧：message/tool_call/interrupt/summary/error/done）；tool_call 帧载荷为 {name, content, source}，由 runtime _stream 检测 ToolMessage 时触发；StreamChunk 扩展 type 和 name 字段。
> - 2026-08-27 议题 3 落盘：HIL 交互闭环——审批复用消息通道（decisions JSON 塞 content，零新端点，与 runtime _build_resume_value 零适配）；本期仅 approve+reject（edit/respond 记待办）；刷新恢复 = GET /messages 加 pending_interrupt 可选字段；pending 时普通文本 = 全 reject（自然语义）。
> - 2026-08-27 议题 3 确认：interrupt 帧后 done 帧（interrupted=true 元数据）+ 连接关闭/thread 保持 pending 语义确认；审批提交复用消息通道确认；普通文本 pending 语义（全 reject）确认；刷新恢复 GET /messages 扩展 pending_interrupt 字段确认。
> - 2026-08-27 议题 4 落盘：GET /messages = L2 行投影（type 维度 message|tool_call|summary，前端可渲染工具折叠/压缩提示，信息零丢失，数据源 read_or_rebuild_l2）；rebuild = message 行照灌 + summary 行转 HumanMessage（与 SummarizationMiddleware 自身形态一致），tool_call 行跳过记入 skipped；边界：L2 无数据 422 / pending 中断 409 / 重建前清旧 thread 同 id 重灌。
> - 2026-08-27 议题 5 落盘：RunTracer 挂 chat 链——每轮一行（name=app 名，events 内补 agent 字段区分 coordinator|subagent）；表加 source(test|chat) + session_id 两字段（与 G3 更名迁移合并）；新端点 GET /chat/traces（X-Session-Id 会话维度）；CHAT_TRACE_ENABLED 默认 true。
> - 2026-08-27 议题 5 确认：lc_agent_name 传递机制验证通过——deepagents 创建 subagent 时设置 metadata，LangGraph ensure_config 传递 parent metadata，RunTracer callback 可接收 lc_agent_name。
> - 2026-08-27 议题 6 落盘：会话自动起名 = 截断+LLM覆盖两级（首轮即时首条消息截 20 字；后台 fire-and-forget LLM 起名成功后覆盖；SESSION_NAMING_ENABLED 开关沿用）；session_naming 链从 git 历史恢复并适配新架构（sessions_service 调用面）。
> - 2026-08-27 议题 6 确认：两级策略、开关、恢复清单、适配点全部确认。
> - 2026-08-27 议题 7 落盘：前端聊天界面——两路由跳转（/chat 列表页 G3 已定 + /chat/:sessionId 聊天页新增）；SSE 客户端自研 fetch-based（utils/sse.ts，token/分帧/心跳/abort/401 重试，零依赖）；消息流 P0 全量（气泡/source 标签/工具折叠/压缩提示/HIL 审批卡片/decisions 胶囊/pending 恢复）；运行轨迹抽屉本期落地（复用 trace 渲染模式）；Markdown 纯文本先行（记待办）。
> - 2026-08-27 议题 7 确认：路由设计、SSE客户端、消息流UI P0清单、运行轨迹抽屉、组件拆分全部确认。
> - 2026-08-27 议题 8 落盘：服务层落点 app/services/agents/chat_service.py（5 职责：非流式 auto-approve / 流式 SSE generator / 历史 / rebuild / traces；依赖同包零跨包）；rebuild 编排归 chat_service（graph.aupdate_state 操作与 chat 链同域，维持 sessions_service 轻量边界）；chatbot.py 不复活（G3 议题 6 已定删除，新端点进新文件 api/v1/chat.py）；schemas/chat.py 单文件重组（新增 7 / 删 StreamResponse / 扩展 ChatResponse）。
> - 2026-08-27 议题 8 确认：chat_service落点、chatbot.py处置、路由层职责、schemas重组全部确认。
> - 2026-08-27 议题 9 落盘（收口）：终评风险中 / 工时 3 周；集成测试全闭环（流式帧序 / HIL resume / pending 恢复 / rebuild 续聊，mock LLM）；待办汇总 8 项；回滚方案与实施顺序定案；头部初评更新为终评。
> - 2026-08-27 议题 9 确认：待办汇总、终评、验证策略、回滚方案、实施顺序全部确认。G4 规格讨论完毕。

---

## 1. 目标与非目标

### 1.1 目标

1. **chat 端点族落地**（议题 1）：`X-Session-Id` header 寻址（G1 §1.1 预留的正式兑现）——`POST /chat`（非流式，信封）、`POST /chat/stream`（SSE 流式）、`GET /messages`（历史渲染）
2. **HIL 交互闭环**（议题 3）：interrupt 结构化下发（修复旧实现 str() 化丢失结构的问题）+ 审批提交 + 刷新后中断状态恢复
3. **RunTracer 挂 chat 调用链**（议题 5）：subagent 中间过程落 `subagent_trace` 表（G3 §2.3 / §11.11 留痕兑现）
4. **灾难恢复 rebuild**（议题 1d 决策：本期落地）：读 L2 → 灌新 thread → 续聊可验证（G3 §11.11「G4 定消费方」兑现）
5. **前端聊天界面**（议题 7）：聊天页路由 + SSE 客户端 + 消息流 UI（subagent 来源标签 / 工具调用折叠 / HIL 审批卡片）

### 1.2 非目标

- **workflow 引擎接入**（议题 1c 排除决策）：`WorkflowAppRuntime` 激活、registry 生命周期与 chat 服务层接通，归独立 spec（`docs/workflow-reimpl-plan/` 体系自身或后续 G5）；G4 不改动 `app/workflow/` 任何文件
- L0-L2 存储层改造（G3 已定案；G4 纯消费：钩子已挂、read_or_rebuild_l2 已有）
- 消息编辑 / 重新生成 / 多轮分支等高级聊天交互（后续 phase）
- 多模态消息（图片 / 文件上传）
- mem0 长期记忆（L3）改造
- 跨设备实时同步（会话锁 / WebSocket 推送）

## 2. 现状基础（2026-08-27 探索核实）

### 2.1 runtime 执行层已就绪（G4 零改动直接消费）

| 能力 | 接口 | 备注 |
|---|---|---|
| 非流式执行 | `ainvoke(messages, session_id=, user_id=, username=)` | interrupt 命中时返回单条 assistant Message（文本） |
| 流式执行 | `astream(...)` → `StreamChunk{content, source}` | source = subagent 名 / `coordinator` / `system`（interrupt 尾块） |
| 历史读取 | `get_chat_history(session_id)` | L1 checkpoint 投影（user/assistant） |
| HIL resume | `_build_resume_value`（deepagents 版） | 用户回复 JSON `{"decisions":[...]}` 解析；否则安全默认**全 reject** |
| 中断探测 | `_pending_interrupt_value` | `state.next` 非空 + `tasks[0].interrupts[0].value` |
| L2 写入钩子 | `_fire_context_record`（G3 落盘，挂 ainvoke/astream 成功路径） | chat 端点调用即触发，无需额外接线 |
| 记忆写回 | `_fire_memory_add` | 成功路径 fire-and-forget |

### 2.2 旧 chatbot 实现存档（git `b7af39a^`，G1 清空前）

| 端点 | 形态 | G4 处置 |
|---|---|---|
| `POST /chat` | `ApiResponse[ChatResponse]` | ✅ 等价恢复（寻址改 header） |
| `POST /chat/stream` | SSE `data: {StreamResponse json}\n\n` | ✅ 恢复 + 协议升级（议题 2） |
| `GET /messages` | `ApiResponse[ChatResponse]`（L1 get_chat_history） | ✅ 恢复 + 数据源升级为 L2 优先（议题 4） |
| `DELETE /messages` | `ApiResponse[None]`（clear_chat_history） | ❌ **砍掉**（议题 1b，理由见 §3.2） |

**旧实现的已知缺陷（G4 修复项）**：

- interrupt 结构丢失：interrupt_value（结构化 `action_requests` dict）被 `str()` 化塞进 StreamResponse.content，前端无法渲染审批 UI（议题 2/3 修复）
- 旧 `maybe_name_session` 挂点已随 G3 议题 6 删除（LLM 起名判断归本 spec 议题 6）

### 2.3 限流 key 现状（config.py 已核实）

`chat`（30/min）、`chat_stream`（20/min）、`messages`（50/min）三个 key **G1 未删、现成可用**；G4 仅需新增 `rebuild` key。

## 3. 端点拓扑（议题 1 定案）

### 3.1 寻址方式：X-Session-Id header（用户决策，G1 原案兑现）

```
POST /api/v1/chat            X-Session-Id: <session_id>   非流式
POST /api/v1/chat/stream     X-Session-Id: <session_id>   SSE
GET  /api/v1/messages        X-Session-Id: <session_id>   历史
POST /api/v1/rebuild         X-Session-Id: <session_id>   灾难恢复（详设议题 4）
```

| 设计点 | 决策 |
|---|---|
| 寻址风格 | **交互面端点族统一 X-Session-Id header**（G1 §1.1 原案）；资源管理面（CRUD）保持 path 参数——两种视图并存：管理面 `/sessions/{sid}` 资源层级 RESTful，交互面 `/chat` `/messages` 端点族 URL 稳定、不随 CRUD 资源结构演化 |
| header 校验 | FastAPI 依赖 `Header(...)` 强制必填：缺失 → 422（请求校验层报错） |
| 归属校验 | `_resolve_session_or_404` 的 header 版（同 G3 语义）：session 不存在或 `user_id != current_user.id` → **404 防枚举**（不区分两种错误） |
| 鉴权 | 全端点 `Depends(get_current_user)` |
| G1 留痕兑现 | G1 §1.1 目标 2「Chat 类端点通过 X-Session-Id 显式接收 session_id」自此兑现；G3 §12.2.1 复核注「X-Session-Id 为 chat 预留」同步闭合 |

### 3.2 端点清单（3 + 1）

| # | 端点 | 限流 key | 响应 | 详设 |
|---|---|---|---|---|
| 1 | `POST /chat` | `chat`（现成） | `ApiResponse[ChatResponse]`（200；执行语义非资源创建） | 议题 2 |
| 2 | `POST /chat/stream` | `chat_stream`（现成） | `StreamingResponse(text/event-stream)`，**信封豁免**（request.ts 对 SSE 豁免端点原样透传，惯例已载 frontend-spec） | 议题 2 |
| 3 | `GET /messages` | `messages`（现成） | `ApiResponse[MessagesResponse]`（L2 优先，schema 议题 4 定） | 议题 4 |
| 4 | `POST /rebuild` | `rebuild`（**新增 key**，建议 5/min，同低频运维端点档位） | `ApiResponse[RebuildResult]` | 议题 4 |

**砍掉 clear 的决策记录**（议题 1b）：旧 `DELETE /messages` 的语义是「L0 行保留、L1/L2 清空」。与 G3 DELETE /sessions/{sid} 三层级联删除相比：(a) 代码路径重复（同一套 L1 delete_thread + L2 unlink）；(b) 「清空重开」的用户路径 = 删除会话 + 新建会话，一步可达；(c) 保留 clear 会引入「半空会话」状态（message_count 归零但 thread_id 不变），L2 重建语义复杂化。`runtime.clear_chat_history` 方法本身保留（G3 议题 6 已留）但不暴露端点。

### 3.3 workflow 排除留痕（议题 1c）

`app/workflow/` 是独立契约体系（`docs/workflow-reimpl-plan/`：CONTRACT + spec-00..09，自带 router / registry 注入 / CLI）。`WorkflowAppRuntime` 目前是 reserved placeholder（全部原语 raise NotImplementedError）。chat 服务层接通 workflow 运行时 = registry 生命周期管理 + 引擎适配 + 独立验证链，体量等于一个独立 G-spec，**不纳入 G4**。overview.md Phase 4 的「工作流引擎接入」表述在 G4 收口时同步修正（归独立计划）。

### 3.4 议题 1 DoD 增量

- [ ] `app/api/v1/chat.py` 新文件（4 端点骨架；chatbot.py 空 stub 的处置归议题 8 服务层落点）
- [ ] `X-Session-Id` 归属校验依赖（header 版 `_resolve_session_or_404`，404 防枚举）
- [ ] `config.py`：`RATE_LIMIT_ENDPOINTS` 新增 `rebuild` key（chat / chat_stream / messages 复用现成）

## 4. 流式协议与事件模型（议题 2 定案）

### 4.1 SSE 事件协议（type 多事件）

帧格式沿用 SSE 惯例 `data: {json}\n\n`，每帧 JSON 携带 `type` 字段；旧扁平 `StreamResponse{content, source, done}` 废弃（全新端点、全新客户端，无兼容包衢）。

| type | 触发时机 | 载荷 | 说明 |
|---|---|---|---|
| `message` | 正文分片（AIMessage） | `{content, source}` | source = subagent 名 / `coordinator` / `system` |
| `tool_call` | 工具调用（ToolMessage） | `{name, content, source}` | 由 runtime _stream 检测 ToolMessage 时触发；前端可渲染为折叠面板 |
| `interrupt` | 轮末检测到 pending interrupt（未自动批准路径） | `{action_requests: [{tool, args}]}` | 投影 schema（§4.2）；修复旧 str() 化缺陷 |
| `summary` | 轮末检测到本轮发生压缩（§4.3） | `{summary_text}` | 在 done 帧前发出；summary 文本与 L2 summary 行同源 |
| `error` | 执行异常 | `{message}` | 发出后仍发 done 帧（终止语义不丢） |
| `done` | 流终止 | `{message_count, compressed}` | 终止元数据；compressed = 本轮是否发生压缩 |

**心跳**：每 15 秒发 SSE 注释帧 `: ping\n\n`（防中间代理空闲断连；长工具执行期间可能分钟级无 chunk。项目首例，uvicorn 透传无碍）。

### 4.2 interrupt 投影 schema

```text
# runtime 原始 interrupt_value（deepagents HITL middleware）：
{"action_requests": [{"tool": "write_file", "args": {...}, ...内部字段...}], ...}

# G4 投影（稳定契约，不随 deepagents 版本漂移）：
{"type": "interrupt", "action_requests": [{"tool": "write_file", "args": {...}}]}
```

投影规则：每项只取 `tool` + `args` 两字段；args_schema 等内部字段不透出。前端审批 UI（议题 3/7）只依赖此契约。

### 4.3 压缩事件推送（本期落地，轮末检测实现）

**事实依据**（.venv deepagents/middleware/summarization.py 源码核实）：`SummarizationEvent` 是 **state 更新**（wrap_model_call 返回的 state update 写入 `_summarization_event` 私有键，字段 cutoff_index / summary_message / file_path），**不是 message chunk**——无法从现有 messages 流直接拿到。

**实现路径（轮末检测）**：`runtime.astream` 尾部已有 `aget_state`（memory 钩子同源），增读 `_summarization_event` 与轮初快照对比，有新事件 → SSE 在 done 帧前发 `type=summary` 帧。零额外成本、不动 `_stream` 的 messages 单模式。

- 即时性说明：压缩发生在轮中 model call 前；轮末推送的延迟 = 本轮剩余执行时长（秒级），对前端「上下文已压缩」提示足够
- 载荷：只透出 summary 文本；cutoff_index / file_path 内部字段不透出
- 备选方案（复合流 `astream(stream_mode=["messages","updates"])` 实时分发）留待轮末方案体验不满足时（记待办，见议题 9）

### 4.4 非流式端点的 interrupt 自动批准（用户决策）

**语义**：`POST /chat` 遇 interrupt → 自动构造 approve decisions resume → 继续执行 → 若再中断再批准……直到本轮完成。

| 设计点 | 决策 |
|---|---|
| 循环上限 | **10 次**（默认；`settings.CHAT_AUTO_APPROVE_MAX_ROUNDS` 可配）防失控 |
| 超限行为 | 终止循环，响应携带 interrupt 投影 + 超限标记；thread 停在中断态（下次调用可继续） |
| 安全边界 | **仅非流式端点**；流式端点（SSE）保持人工审批——人在环，runtime 安全默认「非结构化回复全 reject」不变 |
| 信任模型 | 调用方 = session owner（用户 token 鉴权），自动批准等价于 owner 信任自己的工具调用；AgentApp.interrupt_on 白名单仍生效（未配置的 app 根本不会中断） |
| 实现层次 | runtime 公开只读探测方法 `get_pending_interrupt(session_id)`（返回投影或 None）；**循环编排在 chat service 层**（auto-approve 是 API 层语义，非执行层语义，保持 runtime 模板纯粹）。resume 用合法 decisions JSON（非 fake message hack） |

### 4.5 非流式响应 schema（schemas/chat.py 扩展）

```python
class InterruptPayload(BaseModel):
    action_requests: list[ActionRequest]   # 同 SSE 投影 schema

class ChatResponse(BaseResponse):
    messages: list[Message]                  # 正常完成的回复（自动批准成功路径）
    interrupt: InterruptPayload | None = None  # 仅超限终止时非空
```

超限响应示例：`ApiResponse.success(ChatResponse(messages=[], interrupt=...), message="auto_approve_limit_exceeded")`——信封 message 携带原因，调用方可编程判断。

### 4.6 议题 2 DoD 增量

- [ ] SSE 事件协议实现（6 类 type 帧：message/tool_call/interrupt/summary/error/done + 15s 心跳注释帧）
- [ ] `runtime.get_pending_interrupt(session_id)` 公开探测方法（返回 §4.2 投影或 None）
- [ ] `runtime._stream` 扩展支持 tool_call 帧（检测 ToolMessage 时触发）
- [ ] `StreamChunk` 扩展 `type` 和 `name` 字段
- [ ] summary 轮末检测推送（`_summarization_event` 快照对比）
- [ ] 非流式 auto-approve 循环（chat service 层；上限 `CHAT_AUTO_APPROVE_MAX_ROUNDS`）
- [ ] `schemas/chat.py`：`StreamEvent`（SSE 帧）/ `InterruptPayload` / `ChatResponse` 扩展；旧 `StreamResponse` 废弃

## 5. HIL 交互闭环（议题 3 定案）

### 5.1 交互时序（流式端点）

```text
用户发消息 → POST /chat/stream
  ├─ message 帧×N（正文分片，含 subagent 来源）
  ├─ interrupt 帧（结构化 action_requests）        ← agent 暂停，等审批
  └─ done 帧（interrupted=true；连接关闭，thread 保持 pending 态，见下方终止语义）

用户在审批卡片点「批准/拒绝」→ 前端构造 decisions JSON 塞消息 content
  → POST /chat/stream（与普通发消息同一端点）
  ├─ runtime 检测 pending → Command(resume=decisions)
  ├─ message 帧×N（继续执行）
  └─ done 帧（本轮完成）
```

**interrupt 帧后的流终止语义**（上时序图末行）：interrupt 帧发出后紧跟 done 帧（`interrupted=true` 进 done 元数据）——SSE 连接关闭，但 **thread 停在 pending 态**（checkpoint 保存），下次 POST /chat/stream 即 resume。不保持长连接等审批（连接资源与审批时长解耦）。

### 5.2 审批提交（复用消息通道，零新端点）

前端把用户决策序列化为 decisions JSON 塞进消息 content，正常 POST /chat/stream：

```json
{"role": "user", "content": "{\"decisions\":[{\"type\":\"approve\"},{\"type\":\"reject\"}]}"}
```

| 设计点 | 决策 |
|---|---|
| 通道 | 复用 POST /chat/stream（与 runtime `_build_resume_value` 解析语义零适配） |
| 本期类型 | **approve + reject**（每卡独立选择，按 action_requests 顺序一对一）；edit（改 args）/ respond（文本代执行）记待办（后端 decisions JSON 天然支持，随时可启用，仅前端 UI 增量） |
| 普通文本 pending 语义 | 用户直接打字（非 JSON）→ runtime 安全默认**全 reject** 并继续——「算了别做了」的自然语义，无需额外处理 |
| content 长度限制 | approve/reject 的 decisions JSON 极小（不带 args 回传），3000 上限充裕；edit 启用时再评估（记入待办注记） |
| L2 留档 | decisions JSON 原文照记（审计价值：原始决策留档）；前端渲染时识别 `role=user 且 content 以 {"decisions": 开头` → 投影为「已批准/已拒绝 N 个操作」胶囊（纯展示层，后端不改） |

### 5.3 刷新恢复（GET /messages 扩展）

`MessagesResponse` 增可选字段 `pending_interrupt: InterruptPayload | None`：

- 打开会话页一次拉齐：历史消息（L2）+ 中断态（runtime `get_pending_interrupt`）
- pending 非空 → 前端重建 HIL 审批卡片，输入框切换审批模式（议题 7）
- 成本：一次 runtime state 读（同 thread），零新端点

### 5.4 议题 3 DoD 增量

- [ ] interrupt 帧后 done 帧（interrupted=true 元数据）+ 连接关闭/thread 保持 pending 语义
- [ ] decisions JSON 消息路径贯通（前端构造 → POST → resume → 续流）
- [ ] `MessagesResponse.pending_interrupt` 字段（读 `get_pending_interrupt`）
- [ ] 单测：pending 时普通文本 → 全 reject 继续；decisions JSON → 对应 resume

## 6. 历史渲染与 L2 衔接 + rebuild（议题 4 定案）

### 6.1 GET /messages：L2 行投影

数据源 = `read_or_rebuild_l2`（G3 §11.5.3：L2 优先，缺失时从 L1 现算重建并自愈写回）。呈现模型直接投影 L2 行（type 维度，信息零丢失）：

```python
class HistoryItem(BaseModel):
    type: Literal["message", "tool_call", "summary"]   # L2 行类型（G3 §4.1.1）
    seq: int
    ts: str
    role: str | None = None      # message 行：user | assistant
    content: str | None = None   # message / summary 行
    name: str | None = None      # tool_call 行：工具名
    summary: str | None = None   # tool_call 行：摘要

class MessagesResponse(BaseResponse):
    messages: list[HistoryItem]
    pending_interrupt: InterruptPayload | None = None   # 议题 3：刷新拉齐中断态
```

- 前端渲染：`tool_call` 行 → 折叠面板（工具名 + 摘要）；`summary` 行 → 「上下文已压缩」提示条；decisions JSON 胶囊识别（§5.2）
- 旧 `ChatResponse`（纯 user/assistant）不再用于历史端点（POST /chat 响应仍用，语义不同：本轮回复非历史）

### 6.2 rebuild 详设（议题 1d：本期落地）

```python
async def rebuild_session(db, session_row) -> RebuildResult:
    # 1. 边界检查：L2 无可读行 → 422（nothing to rebuild）
    #    thread 处于 pending interrupt → 409（先处理中断再重建）
    # 2. 读 L2 全量行（context_store 流式读）
    # 3. delete_thread_checkpoint(session_id)   # 清旧 thread（G3 helper 复用）
    # 4. 组装初始 messages 并 graph.aupdate_state(config, {"messages": [...]}) 同 id 重灌：
    #    - message 行 → HumanMessage / AIMessage 照灌
    #    - summary 行 → HumanMessage（与 SummarizationMiddleware 的 summary_message 形态一致）
    #    - tool_call 行 → 跳过（依赖 tool_call_id 配对，不可恢复），计数记入结果
    # 5. 返回 RebuildResult
```

```python
class RebuildResult(BaseModel):
    rebuilt_messages: int      # 实际灌入条数（message + summary）
    skipped_tool_calls: int    # 跳过的 tool_call 行数
    l2_source_lines: int       # L2 总行数
```

| 设计点 | 决策 |
|---|---|
| 权限 | session owner 自己（同 chat 鉴权；灾难自救场景，无 admin 门槛） |
| 幂等性 | 重复 rebuild：删旧重灌，结果确定；L2 不变则结果不变 |
| message_count | 重建后 L1 恢复，GET /sessions/{sid} 的 message_count 自然刷新 |
| L2 保真 | rebuild **不写** L2（L2 是真相源，只读）；自愈写回仅 read_or_rebuild_l2 的 fallback 路径 |
| 探索项 | `aupdate_state` 灌入行为需实施前 spike 验证（messages 追加语义 / config 形态），记入验证清单 |

### 6.3 议题 4 DoD 增量

- [ ] `GET /messages` 端点（L2 行投影 + pending_interrupt）
- [ ] `POST /rebuild` 端点（含边界 422/409；RebuildResult）
- [ ] rebuild service 编排（复用 delete_thread_checkpoint + context_store 读 + aupdate_state 灌入）
- [ ] `aupdate_state` spike 验证记录（messages 追加语义）
- [ ] 单测：L2 缺失自愈、rebuild 后续聊闭环（重建 → 发消息 → 历史连贯）

## 7. RunTracer 挂 chat 调用链（议题 5 定案）

### 7.1 落表模型（每轮一行）

| 设计点 | 决策 |
|---|---|
| 粒度 | **每轮 chat 一行**：`name` = AgentApp 名；events 内每事件补 `agent` 字段（`coordinator` \| subagent 名，从 callback metadata 的 `lc_agent_name` 取，空 namespace → coordinator，与 `_stream` 同源逻辑） |
| 表结构增量 | `subagent_trace` 加两字段：`source: Literal["test", "chat"]`（默认 `test`，兼容存量行）+ `session_id: str | None`（仅 chat 行）；alembic **与 G3 更名迁移合并**（均未实施，一次迁移到位） |
| 同构性 | 与 test 行同构（status/prompt/model/turns/duration_seconds/final_message/events/error/created_by），前端 trace 渲染组件可复用 |
| G3 §2.3 兑现 | 主会话消息流只进 subagent 最终结果；中间过程（llm_call/tool_call 事件流）落本表 |

### 7.2 挂载与落表时机

```text
chat service 每轮：
  tracer = RunTracer(model_name=resolved_model)   # events 增 agent 字段（RunTracer 小改）
  runtime.ainvoke/astream(..., extra_callbacks=[tracer])   # runtime 加可选参数
  # 轮末（成功/失败都落，test_runner 惯例）：
  events = tracer.finish(status, final_messages, turns=..., duration_seconds=...)
  if settings.CHAT_TRACE_ENABLED: 写 subagent_trace 行（source="chat", session_id=..., name=app 名）
```

- **runtime 改动最小化**：`ainvoke` / `astream` 加可选 `extra_callbacks: list[BaseCallbackHandler]` 参数，并入 `_build_config` 的 callbacks（langfuse 逻辑不动）
- **开关**：`settings.CHAT_TRACE_ENABLED` 默认 **true**；表增长靠 created_at 索引 + 后续清理任务待办（同 G3 孤儿清理先例，记议题 9）
- **字段截断**：MAX_FIELD_CHARS 20k 已有（RunTracer），无新增风险

### 7.3 查询端点（会话维度）

`GET /chat/traces` + `X-Session-Id` → `ApiResponse[list[ChatTraceItem]]`（created_at 倒序，默认 limit 100）

```python
class ChatTraceItem(BaseModel):
    id: int              # trace_id
    status: str          # success | error
    turns: int
    duration_seconds: float
    error: str | None
    created_at: str
    events: list[dict]   # 含 agent 字段的完整事件流
```

- 与现有 `GET /subagents/{name}/traces`（G3 更名后）互不干扰：后者按 subagent 名查 test 行；本端点按 session 查 chat 行（source 过滤内部处理）
- 前端：聊天页「运行轨迹」抽屉（议题 7），复用现有 trace 详情渲染模式（事件展开）

### 7.4 议题 5 DoD 增量

- [ ] `RunTracer` 事件补 `agent` 字段（callback metadata lc_agent_name）
- [ ] `runtime.ainvoke/astream` 加 `extra_callbacks` 可选参数
- [ ] `subagent_trace` 表加 `source` + `session_id`（与 G3 更名迁移合并）；模型同步
- [ ] `GET /chat/traces` 端点（X-Session-Id，倒序 limit 100）
- [ ] `CHAT_TRACE_ENABLED` 配置项（默认 true）
- [ ] 单测：chat 轮落表（source/session_id/agent 字段）；开关关闭不落

**lc_agent_name 传递机制验证**（2026-08-27 调查确认）：
- deepagents 创建 subagent 时通过 `with_config` 设置 `metadata: {"lc_agent_name": spec["name"]}`（`langchain/agents/factory.py:1834`）
- LangGraph 的 `ensure_config` 会将 parent config 的 metadata 传递给 subagent（`langgraph#7926` 合并策略）
- RunTracer 的 `on_chat_model_start` 等回调接收的 metadata 中包含 `lc_agent_name`
- 结论：lc_agent_name 可正确传递到 RunTracer，需修改 RunTracer 实现从 metadata 中提取 agent 字段

## 8. 会话自动起名（议题 6 定案：截断 + LLM 覆盖两级）

### 8.1 两级策略

| 级 | 时机 | 动作 | 成本 |
|---|---|---|---|
| 即时级 | 首轮 chat 成功路径（session.name 为空时） | name = 首条 user 消息前 20 字符（字符非字节，中文友好；空消息兕底「新会话」）；同步 DB update | 零 LLM |
| 优雅级 | 同轮 fire-and-forget | LLM structured output 起名成功后覆盖 update（仿 `_fire_memory_add` 模式：任务集锚定防 GC，失败仅记日志不重试） | 每会话一次 |

- 后续轮不再起名（name 非空即跳过）；用户手动 PATCH 重命名后同样不再覆盖
- 开关：`settings.SESSION_NAMING_ENABLED` 沿用（实施时检查 G1/G3 是否残留该 settings 项：若在则复用，若已删则恢复；默认值随旧值）

### 8.2 恢复清单（git 历史找回 + 新架构适配）

G3 议题 6 删除的 4 处全部恢复，适配点：

| 项 | 恢复 + 适配 |
|---|---|
| `app/services/session_naming.py`（90 行） | 调用面适配：旧 `database_service.update_session_name` → `sessions_service.update_session_name`（G3 新实现）；LLM 调用走 app 解析的 model config |
| `SESSION_TITLE_PROMPT` + txt | 原样恢复 |
| `session_names_generated_total` counter | 原样恢复（metrics.py） |
| `SessionTitle` schema | 原样恢复（schemas/chat.py；G3 删除后本 spec 恢复） |

### 8.3 议题 6 DoD 增量

- [ ] 首轮截断起名（同步，空 name 才触发）
- [ ] session_naming 链恢复 + sessions_service 适配 + 开关检查
- [ ] fire-and-forget 起名覆盖（失败不阻断不重试）
- [ ] 单测：截断规则（20 字/空消息兕底/非空跳过）；开关关闭仅截断

## 9. 前端聊天界面（议题 7 定案）

### 9.1 路由与页面结构（两路由跳转）

| 设计点 | 决策 |
|---|---|
| 路由 | 新增平级路由 `/chat/:sessionId`（name `chatSession`，懒加载 `ChatSessionView.vue`）；`/chat` 列表页（G3 议题 8 已定）零改动 |
| 导航 | 列表页行内「进入聊天」→ `router.push`；聊天页顶栏返回列表 |
| 页面形态 | 独立全页（非对话框）：聊天是高频长时操作，消息流滚动 / HIL 审批 / 轨迹抽屉均需稳定空间 |
| 组件拆分 | 按项目惯例功能拆文件（参照 views/agent/ SubAgent* 拆法）：`ChatSessionView.vue`（页面骨架 + 编排）+ `ChatMessageList.vue`（消息流）+ `ChatHilCard.vue`（审批卡片）+ `ChatTraceDrawer.vue`（轨迹抽屉） |
| 状态编排 | `composables/useChatStream.ts`（发送 / 帧分发 / 中断 / 审批提交状态机）——延续 composables 惯例，视图层不碰流细节 |

### 9.2 SSE 客户端（utils/sse.ts 自研 fetch-based，零依赖）

**排除项留痕**：原生 `EventSource` 无法携带自定义 header（Authorization / X-Session-Id 都带不上），token 走 query 有进服务器访问日志的泄露风险——排除；第三方库（@microsoft/fetch-event-source 等）新增运行时依赖，自研约 100 行收益不足——排除。

```ts
// utils/sse.ts 接口形态
export interface SseOptions {
  url: string
  headers?: Record<string, string>          // X-Session-Id 等
  signal?: AbortSignal                       // 中断控制（切会话 / 用户点停止）
  onEvent: (data: string) => void            // data: 行载荷（JSON）
  onError?: (error: unknown) => void
}
export async function sseFetch(options: SseOptions): Promise<void>
```

| 设计点 | 决策 |
|---|---|
| token 注入 | `getUserToken()`（authStorage，同源 request.ts）→ `Authorization: Bearer`；未登录不发，让 401 走错误路径 |
| 401 处理 | 连接建立时 401（响应非 `text/event-stream`）→ `refreshUserToken()` 成功后重试一次（同 request.ts `_retried` 模式）；再失败 `clearAuth` + 动态 import router 跳 login |
| 分帧解析 | `ReadableStream` + `TextDecoder` 逐块读，缓冲区按 `\n\n` 切帧；帧内 `data:` 前缀行拼接为载荷；`:` 开头注释行（15s 心跳 `: ping`）跳过；跨 chunk 半帧靠缓冲区拼接处理 |
| 中断 | `AbortController`：切换会话 / 用户点「停止」/ 组件卸载时 abort |
| 断线语义 | 网络中断（未收到 done 帧）→ `onError` 回调，前端提示「连接中断，可重新发送消息恢复」；**不自动重连**——G4 语义：恢复靠用户重发消息触发 resume（§5.1），非浏览器自动重连流 |

### 9.3 消息流 UI（P0 清单，全部本期）

| 元素 | 渲染规则 |
|---|---|
| 消息气泡 | user 右对齐 / assistant 左对齐；streaming 时 assistant 气泡尾部闪烁光标 |
| source 标签 | `message` 帧 / L2 行的 source：`coordinator` 不显示（默认）；subagent 名显示胶囊标签（区分多 agent 输出） |
| tool_call 行 | 折叠面板（工具名 name + 摘要 summary，点击展开详情）——L2 历史行（§6.1） |
| summary 提示条 | `type=summary` 帧 / L2 summary 行 → 「上下文已压缩」灰色细条（§4.3 轮末推送的消费端） |
| HIL 审批卡片 | `type=interrupt` 帧 → `ChatHilCard`：action_requests 逐卡列出（tool 名 + args JSON 折叠查看）+ 每卡「批准 / 拒绝」独立选择（§5.2） |
| decisions 胶囊 | `role=user 且 content 以 {"decisions": 开头` → 投影为「已批准/已拒绝 N 个操作」胶囊（纯展示层识别） |
| pending 恢复 | 进入页面 `GET /messages` → `pending_interrupt` 非空 → 直接重建审批卡片（§5.3 刷新拉齐）；输入框 placeholder 切换提示 |
| 输入区 | Enter 发送 / Shift+Enter 换行；streaming 时发送钮变「停止」（abort）；pending 审批模式下输入框**保持可用**（placeholder 提示「发送文本将拒绝所有待审批操作」——§5.2 自然语义，不禁用） |

### 9.4 运行轨迹抽屉（本期落地）

- 入口：聊天页顶栏「运行轨迹」按钮 → `el-drawer`（右滑出）
- 数据：`GET /chat/traces`（议题 5）→ 列表（status / turns / duration / created_at 倒序）→ 选中行展开完整事件流（含 agent 字段区分 coordinator / subagent）
- 渲染**复用模式非复用组件**：SubAgentTraceDetailDialog 是 Dialog 形态，抽屉内事件展开列表参照其渲染实现（时间线 / 折叠），不直接引组件

### 9.5 渲染策略：纯文本先行（用户决策）

- assistant 消息 `white-space: pre-wrap` 保留换行，零渲染依赖
- Markdown 渲染（markdown-it + DOMPurify + 代码高亮 + 流式半渲染态处理）是独立一套问题，**记待办**（议题 9 收口汇总）；启用时纯消息流数据无需变更（渲染层局部替换）

### 9.6 前端文件清单与 API 包装

```text
agent-web/src/
  api/chat.ts                    # sendChat / fetchMessages / rebuildSession / fetchChatTraces
                                 # （axios 包装；X-Session-Id 走自定义 header，拦截器自动补 Authorization）
  utils/sse.ts                   # §9.2 fetch-based SSE 客户端
  composables/useChatStream.ts   # 流编排状态机（帧分发 / 审批提交 / abort / pending 态）
  views/chat/ChatSessionView.vue # 路由页（§9.1）
  views/chat/ChatMessageList.vue # 消息流（§9.3）
  views/chat/ChatHilCard.vue     # 审批卡片（§9.3）
  views/chat/ChatTraceDrawer.vue # 轨迹抽屉（§9.4）
  types/index.ts                 # 后端 schema 镜像类型（StreamEvent / HistoryItem / InterruptPayload / ChatTraceItem / RebuildResult）
```

- rebuild 入口：聊天页顶栏「更多」菜单内低频按钮 + `useConfirm` 确认（「将清除现有 checkpoint 并从 L2 重建」）；结果 RebuildResult 用 notify 呈现
- `ChatView.vue` 列表页（G3）行内操作追加「进入聊天」按钮——G3 实施时若 G4 已定案可一次做全，否则 G4 增量补

### 9.7 议题 7 DoD 增量

- [ ] 路由 `/chat/:sessionId` + 列表页「进入聊天」入口
- [ ] `utils/sse.ts`（分帧含心跳跳过与跨 chunk 半帧 / abort / 401 重试一次）
- [ ] `useChatStream.ts` 帧分发状态机（message / interrupt / summary / error / done 五类 → UI 状态）
- [ ] 消息流 P0：气泡 + source 标签 + tool_call 折叠 + summary 提示条 + HIL 审批卡片 + decisions 胶囊 + pending 恢复 + 停止按钮
- [ ] `ChatTraceDrawer`（列表 + 事件展开，agent 字段区分）
- [ ] `api/chat.ts` 4 函数 + types 镜像类型
- [ ] 测试：`tests/utils/sse.spec.ts`（分帧 / 心跳跳过 / 半帧拼接 / abort / 401 重试）；`useChatStream` 帧分发；消息流渲染（mock SSE 流）；api 包装

## 10. 服务层与 schema 落点（议题 8 定案）

### 10.1 chat_service 落点与职责清单

**落点**：`app/services/agents/chat_service.py`（与 runtime / sessions_service（G3 新建）/ context_store / run_tracer 同包，依赖零跨包——G3 §11.7 sessions_service 落点同款理由）。

| 职责函数 | 内容 | 议题回指 |
|---|---|---|
| `chat(...)` 非流式 | auto-approve 循环（上限 `CHAT_AUTO_APPROVE_MAX_ROUNDS`）+ RunTracer 挂载落表 + 起名钩子 → ChatResponse | §4.4 / §7 / §8 |
| `chat_stream(...)` 流式 | async generator 产出 SSE 帧（5 类 type + 15s 心跳）+ 轮末 summary 检测 + interrupt 投影 + RunTracer + 起名钩子 | §4 / §7 / §8 |
| `get_history(...)` | L2 行投影（read_or_rebuild_l2）+ pending_interrupt 拉齐 → MessagesResponse | §6.1 / §5.3 |
| `rebuild(...)` | 议题 8 决策归 chat 域：本质是 L2→L1 checkpoint 灌入（graph.aupdate_state），与 chat 链同域；维持 sessions_service 不碰 graph 对象的轻量边界（G3 设计它只调 delete_thread_checkpoint helper） | §6.2 |
| `get_traces(...)` | 查 subagent_trace（source="chat" + session_id 过滤，倒序 limit）→ list[ChatTraceItem] | §7.3 |

**依赖层次（单向无环）**：

```text
api/v1/chat.py → chat_service → runtime（ainvoke / astream / get_pending_interrupt / delete_thread_checkpoint）
                             → context_store（L2 读，G3 新建）
                             → sessions_service（update_session_name 起名回写）
                             → run_tracer（RunTracer 类 + 落表）
                             → services/llm（起名 LLM 调用）
```

### 10.2 chatbot.py 处置（G3 衔接，无需重新决策）

- G3 议题 6 遗留清理三件套已定：**删除** chatbot.py 空 stub（26 行，全仓无 import，G3 grep 确认）+ test_chatbot_runtime.py skip 测试；`/chatbot/*` 404 断言（test_lifespan_smoke / test_chat_flow）保留（不依赖文件存在，G4 新端点为 `/chat` 无 bot 前缀，断言依旧成立）
- G4 **不复活** chatbot.py：新端点进新文件 `app/api/v1/chat.py`，`api.py` 注册 router
- 顺序保障：G3（Phase 3）先实施则文件已删；若 G4 先行实施，chatbot.py 删除动作随 G4 执行——两 spec 不冲突（删除的是空 stub，与新文件无重叠）

### 10.3 api/v1/chat.py 路由层职责（thin）

- `X-Session-Id` Header 强制依赖 + 归属校验（404 防枚举，§3.1）
- 限流装饰器（chat / chat_stream / messages / rebuild 四 key，§3.2）
- SSE 端点：`StreamingResponse` 包装（media_type=`text/event-stream`；headers 加 `Cache-Control: no-cache` + `X-Accel-Buffering: no` 防代理缓冲——生产 nginx 部署必需，开发态 Vite 代理透传无碍）；generator 本体在 chat_service
- 非 SSE 端点：ApiResponse 信封包装（auto-approve 超限时 message 携带原因，§4.5）

### 10.4 schemas/chat.py 重组（单文件继续，域内聚合惯例）

| schema | 处置 |
|---|---|
| `Message` | 保留（role/content 校验 + script 标签校验沿用） |
| `ChatRequest` | 保留 |
| `ChatResponse` | 扩展（messages + interrupt 可选字段，§4.5） |
| `StreamResponse` | **废弃删除**（旧扁平格式，被 StreamEvent 取代） |
| `SessionTitle` | 保留（议题 6 恢复链） |
| 新增 7 个 | `ActionRequest` / `InterruptPayload` / `StreamEvent`（SSE 帧模型）/ `HistoryItem` / `MessagesResponse` / `RebuildResult` / `ChatTraceItem` |

- `StreamEvent` 取单 schema 可选字段 + `model_dump(exclude_none=True)` 序列化（帧字段少，discriminated union 收益不抵复杂度）；前端 types 镜像同构（§9.6）

### 10.5 议题 8 DoD 增量

- [ ] `app/services/agents/chat_service.py`（5 职责函数，§10.1）
- [ ] `app/api/v1/chat.py`（thin 路由 + `api.py` 注册 + SSE headers）
- [ ] chatbot.py 删除衔接确认（G3 已删则验证无引用；未删则随 G4 删）
- [ ] `schemas/chat.py` 重组（新增 7 / 删 StreamResponse / 扩展 ChatResponse）
- [ ] 测试：`tests/unit/api/test_chat.py`（鉴权 / 限流 / 信封 / SSE headers，mock chat_service）；`tests/unit/services/agents/test_chat_service.py`（auto-approve 循环 / SSE 帧序列含心跳 / rebuild 编排 / 起名钩子触发条件 / trace 落表）

## 11. 收口（议题 9 定稿）

### 11.1 待办汇总（后续 phase，按优先级）

| 待办 | 来源 | 说明 |
|---|---|---|
| edit / respond 决策类型前端 UI | §5.2 | 后端 decisions JSON 天然支持，仅前端表单增量（edit 需评估 content 3000 上限带 args 回传） |
| Markdown 渲染整链 | §9.5 | markdown-it + DOMPurify + 代码高亮 + 流式半渲染态处理；数据层无需变更，渲染层局部替换 |
| 复合流实时 summary | §4.3 | `astream(stream_mode=["messages","updates"])` 分发；轮末推送体验不满足时启用 |
| subagent_trace 增长清理 | §7.2 | created_at 索引已有；定期清理任务（同 G3 孤儿清理先例） |
| 高级聊天交互 | §1.2 | 消息编辑 / 重新生成 / 多轮分支 |
| 多模态消息 | §1.2 | 图片 / 文件上传 |
| 跨设备实时同步 | §1.2 | 会话锁 / WebSocket 推送 |
| workflow 引擎接入 | §3.3 | 独立 spec 体系（`docs/workflow-reimpl-plan/` 自身或后续 G5） |

### 11.2 终评（议题 9）

- **风险：中**（维持初评）——新语义面（SSE 首例 / auto-approve 反转安全默认 / rebuild spike）与利好面（runtime 零改动直接消费 / 回滚干净 revert 即回 404 现状 / 前后端两路由独立互不拖累）相抵
- **工时：3 周**（初评 2.5 周上调）——后端轻（4+1 端点 + 单 service，runtime 现成）但前端重（8 文件 + SSE 客户端 + 五类帧状态机 + 全套测试）；rebuild spike 有不确定性

### 11.3 验证策略（集成：全闭环，用户决策）

- **单测**：各节 DoD（§3.4 / §4.6 / §5.4 / §6.3 / §7.4 / §8.3 / §9.7 / §10.5）
- **集成**（`tests/integration/` 下 chat 流闭环，全部 mock LLM——R7 零真实网络 / LLM 调用）：
  1. 非流式：发送 → 信封 ChatResponse → 历史落 L2
  2. 流式帧序：message×N → summary（触发压缩场景）→ done（compressed=true）
  3. HIL：interrupt 帧 → done(interrupted=true) → decisions JSON resume → 完成
  4. pending 恢复：interrupt 后重开 → GET /messages pending_interrupt 非空
  5. rebuild：删 checkpoint → POST /rebuild → 续聊历史连贯
- **手动测试文档**：`docs/agentapp-manual-testing.md` 新增「/chat 调试」章节（接第 7 节 chatbot 退役说明之后）

### 11.4 回滚方案

| 变更面 | 回滚动作 | 回落后状态 |
|---|---|---|
| 后端端点 + 服务 | revert `chat.py` / `chat_service.py` / `schemas/chat.py` / config keys | /chat 族 404（G4 前现状） |
| runtime 小改 | revert `get_pending_interrupt` / `extra_callbacks` / 轮末 summary 检测 | runtime 回 G3 形态（L2 钩子照旧） |
| 迁移（与 G3 合并） | alembic downgrade（subagent_trace source / session_id 列删除） | test 行写入不受影响（source 有默认值） |
| 前端 | revert 8 文件 + 路由注册 | `/chat` 列表页（G3）不受影响（两路由独立） |
| 文档 | revert manual-testing 新章节 | — |

### 11.5 实施顺序（依赖拓扑）

1. **spike**：`aupdate_state` 灌入验证（§6.2，rebuild 前置）
2. **后端**：schemas 重组 → runtime 小改（get_pending_interrupt / extra_callbacks / summary 轮末检测）→ chat_service → api 路由 + 注册 → 迁移（随 G3 合并）
3. **前端**：sse.ts → useChatStream → 消息流组件 → 轨迹抽屉 → 列表页入口
4. **验证**：单测各节 → 集成闭环 → 手动文档

> 前置检查：sessions_service / context_store / read_or_rebuild_l2 / delete_thread_checkpoint（G3 交付物）就绪；G3 未实施部分先行补齐或与 G4 并行实施时对齐接口。

### 11.6 DoD 总索引

§3.4（端点拓扑）/ §4.6（流式协议）/ §5.4（HIL）/ §6.3（历史 + rebuild）/ §7.4（RunTracer）/ §8.3（起名）/ §9.7（前端）/ §10.5（服务层）+ §11.3 集成清单。
