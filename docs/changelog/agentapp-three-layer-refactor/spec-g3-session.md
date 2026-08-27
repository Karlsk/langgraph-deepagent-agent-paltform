# G3 Spec：Session 元数据与上下文架构（v2 修订版）

> **主题**：Session 元数据定案 PG；会话上下文（Context）的**记录 / 恢复 / 压缩**分层架构。
> **关联文档**：`overview.md`（路线图）、`files-risks.md`（文件清单 + 风险）、`spec-g2-workspace.md`（§12 集成接口）
> **目标读者**：后端架构师
> **风险等级**：中（主存储 PG 不动；新增 L2 JSON 记录层 + 压缩接入；议题 9 复核维持：各风险点均有缓解——钩子 fire-and-forget / 级联尽力清理 / 迁移均有 downgrade）
> **估算工时**：3 周（议题 9 上调，原 2 周基于纯 CRUD+export 旧范围）：L2 层（context_store + 钩子 + 自愈）1 周；压缩接入（字段 + middleware + 可观测）0.5 周；CRUD + 级联 + export 0.5-1 周；更名 + 遗留清理 0.5 周；前端列表页 + 测试收尾 0.5 周
> **修订记录**：
> - 2026-08-27 议题 1 落盘：上下文分层模型（L0-L3）；L2 = JSON 文件（G2 预留路径）；压缩 = deepagents `SummarizationMiddleware`（`AgentApp.context_size` 模型字段 + settings 全局默认）；L2 写入 = runtime 成功路径 fire-and-forget 钩子；SubAgentTrace 定位扩展 + 表更名 `subagent_trace` 纳入本期（chat 场景落表归后续 phase）。
> - 2026-08-27 议题 4 落盘：删除级联 = L1→L2→L0 顺序尽力清理（L0 最后删）；checkpoint 清理 = 独立 helper（`_build_checkpointer` + `adelete_thread`，不经 `get_runtime`）；DELETE 响应 = `ApiResponse[None]` + 200（项目惯例）；AgentApp 硬删 = 全量级联含 checkpoint（`delete_agent_app` 增强，见 §11.5.2）。
> - 2026-08-27 议题 5 落盘：export 读取 = L2 优先 + L1 fallback 自愈（与 §4.1.3 同一份代码）；响应 = 文件下载流式（`Content-Disposition: attachment`，非 envelope，项目首例惯例）；L2 每行 schema 定稿（6 字段 + metadata 预留）；灾难恢复（L2→L1 重建）= 后续 phase 存档草案（§4.1.3）。
> - 2026-08-27 议题 6 落盘：遗留清理三件套（见 §11.5.4）——session_naming 孤儿链删除（4 处，G4 留痕待判断）；chatbot.py 空文件 + 整文件 skip 测试删除；SubAgentTestTrace 彻底更名（表名/类名/API 路径/前端全链）。
> - 2026-08-27 议题 7 落盘：服务层落点（见 §11.5.5）——`agents/sessions_service.py`（业务编排）+ `agents/context_store.py`（L2 纯文件操作）双模块；database.py 旧 5 个 session 方法删除重实现；`SessionListResponse` 作废（`PageResult[SessionRead]` 取代）。
> - 2026-08-27 议题 9 落盘（收口）：DoD 保持双份完整维护（§4.3 / §8.1 / §8.2 / §11.8 各自独立清单，便于后续逐项验证勾选；修订时须同步双改）；回滚策略重写（3 迁移 downgrade + L2 残留说明）；冒烟鸡生蛋修正；后续 phase 待办汇总（§11.11）；工时上调 3 周、风险维持「中」。（注：DoD 结构曾短暂改为 §8.2 指针后按用户指示撤回，恢复双份）
> - 2026-08-27 复核修正：§11.4 三处漂移对齐后续议题定案（表名 subagent_test_trace → subagent_trace 并指向 §11.5.4.C 彻底更名；SessionCreate.agent_app_id 必填 int；SessionListResponse 删除，PageResult[SessionRead] 取代）。
> - 2026-08-27 复核修正（议题 3）：RATE_LIMIT key 统一 `sessions`（POST 原误用 session_create）；DoD 补「config.py 新增 sessions 限流 key」；page_size 补 ge=1；§12.2.1 补错误映射注。
> - 2026-08-27 复核修正（议题 4）：§11.5.1 L0 行改指 delete_session_cascade 内部删行（原引用不存在的 delete_session 方法）；session_deleted 日志改记录 cascade 编排真实结果（CascadeResult，非硬编码 True）。
> - 2026-08-27 复核修正（议题 5）：§11.5.3 补 jsonl 流式语义注记（当前 list 全量返回；大会话优化 = context_store 流式读直对接响应生成器，实施可选，不改端点契约）。
> - 2026-08-27 复核修正（议题 6）：§11.5.4.C 删「subagent_test key 顺带更名」错误句——代码事实：trace 端点限流已用 `subagent` key（无需动）；`subagent_test` 属 POST /subagents/{name}/test 执行测试端点，与表更名无关。
> - 2026-08-27 复核修正（议题 7）：§9.2/§11.9 测试条目同步议题 3 定案——删「不传 agent_app_id 成功」条，改「不传→422」+ 补自动 associate 语义测试。
> - 2026-08-27 复核修正（议题 8）：§11.6 补 PageResult import；listSessions 改 PageQuery & { agentAppId } + toParams 惯例；补「GET /sessions 无 /page 后缀为有意偏离」注记。
> - 2026-08-27 复核修正（议题 9）：§12.4 验证清单对齐议题 3/5 定案（POST=自动 associate、GET=不触发 lazy 校验、路径 .jsonl、单测改测 associate 语义）；§12.1.1 补注指向 §12.2 最终决策（原「G3 调用时机」为 G2 审查期旧建议）。

---

## 1. 目标与非目标

### 1.1 目标

1. **上下文三分**：明确会话上下文的**记录**（持久化明文副本）、**恢复**（渲染历史 / 续聊 / 灾难重建）、**压缩**（长对话 token 治理）三个场景的架构分工
2. **存储选型定案**（Q5）：Session 元数据 + 执行状态保留 PG；上下文产品记录层（L2）采用 JSON 文件；SQLite / 纯 JSON 主存储两方案降级为否决记录
3. **压缩接入**：采用 deepagents 0.7.5 自带 `SummarizationMiddleware`，阈值来源 `AgentApp.context_size` 模型字段（settings 全局默认），压缩事件落日志 / metrics
4. **明确 session 文件位置**：L2 JSONL 文件归属 G2 v3.3 User 嵌套层（`{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/`）
5. **全新会话 CRUD API 设计**：承接 G1 的 `/auth/session` 删除（G1 实际已直接删除端点，非 noop 保留——见 §11.1 事实修正）；提供 RESTful `/sessions` 端点（list / get / create / patch / delete）；鉴权统一用 `Depends(get_current_user)` + path 参数 session 归属校验

### 1.2 非目标

- 替换 LangGraph AsyncPostgresSaver（L1 执行状态层不动；HIL 中断恢复依赖 SQL 持久化）
- 跨设备实时同步 session 文件（不在评估范围）
- Session 内容加密 / 隐私合规（独立 Phase）
- **chatbot 端点改造（chat / chat_stream / clear_chat_history / get_session_messages）**：chatbot 整体**直接废弃**（G1 已落地为空 stub），不在 Phase 3 范围；如未来重启，需要单独的 chat spec
- **chat 场景的 subagent 中间过程落表**（RunTracer 挂 chat 调用链）：归后续 phase，本期仅完成表更名与概念定位（见 §2）
- mem0 长期记忆（L3）的任何改造

---

## 2. 概念边界澄清（2026-08-27 议题 1 修订）

### 2.1 概念定位

| 概念 | 含义 | 当前存储 | 定位 |
|---|---|---|---|
| **subagent trace** | subagent **执行过程记录**：llm_call / tool_call / run_finished 事件流。**不限测试场景**——chat 中主 Agent 调用 subagent 时，中间过程也归属此概念，**只有 subagent 最终运行结果进入主会话消息流** | PG `SubAgentTestTrace`（本期更名 `subagent_trace`，见 §2.3） | ❌ 不是 session；是过程记录 |
| **Chat History（上下文）** | 一次 chat 会话的用户/助手消息流 + 工具调用中间态 | LangGraph checkpoint（`AsyncPostgresSaver`，thread_id = session_id） | ✅ 是 session；**记录 / 恢复 / 压缩三分，见 §4 分层模型** |
| **Session Metadata** | session 的属性（id / user_id / name / agent_app_id / created_at） | PG `Session` | ✅ 是 session；元数据定案 PG |
| **Agent Runtime Fingerprint** | AgentApp 的发布指纹 | PG `AgentApp.published_hash` / `workspace_hash` | ❌ 不是 session；是配置 |
| **长期记忆（L3）** | 跨会话的用户记忆 | mem0 + pgvector | ❌ 不是 session；不在 G3 范围 |

### 2.2 上下文分层模型（议题 1 核心决策）

```
L0  Session 元数据        PG `Session` 表                    —— 定案不动
L1  执行状态/恢复         PG `AsyncPostgresSaver` checkpoint —— 定案不动（HIL 中断恢复 + 续聊增量输入）
L2  产品级对话记录 ★新增   JSON 明文文件（JSONL）              —— 本期核心交付（见 §4）
L3  长期记忆              mem0 + pgvector                    —— 现状不动，不在 G3 范围
```

**L1 与 L2 职责切分**：

| 场景 | 走哪层 |
|---|---|
| 前端打开会话渲染历史 | **L2**（明文直读，不碰 checkpoint 反序列化） |
| 继续发消息（续聊） | **L1**（同 thread 增量输入，现状语义不变） |
| 导出 / 调试 / 审计 | **L2** |
| L2 文件缺失 / 损坏 | fallback：从 L1 现算重建（自愈写回 L2） |
| 压缩（token 治理） | state 层 `SummarizationMiddleware` 改写 L1；**L2 全量留档不受影响**（压缩不可逆地替换 checkpoint 内旧消息，完整历史只在 L2） |

### 2.3 SubAgentTrace 决策（2026-08-27）

- **本期（G3）**：表更名 `SubAgentTestTrace` → `subagent_trace`（alembic + model + 相关 API / 前端字段同步）；概念定位按 §2.1 落盘（subagent 执行过程记录，测试 + chat 内嵌同源）
- **后续 phase**：chat 调用链挂 `RunTracer` 落表（技术上是 `BaseCallbackHandler` 挂载，实现直接；主会话消息流只进 subagent 最终结果）

---

## 3. 选型定案（Q5，2026-08-27 议题 1 闭环）

### 3.1 决策结果

| 对象 | 决策 | 依据 |
|---|---|---|
| Session 元数据（L0） | **保留 PG `Session` 表** | 列表分页 / 归属校验 / join 查询是关系型强项 |
| 执行状态（L1） | **保留 `AsyncPostgresSaver`** | runtime 深度耦合（HIL 中断恢复 `Command(resume)`、共享池 + 进程级一次性 `setup()` DDL）；替换等于重写 runtime 横切语义层 |
| 上下文产品记录（L2） | **新增 JSON 明文文件（JSONL）** | 用户核心诉求：上下文可读、可控、可导出；压缩留档 / 灾难恢复 / 前端渲染性能（详见 §4） |
| 长期记忆（L3） | 现状不动 | 不在 G3 范围 |

### 3.2 原三方案对比（历史评估存档，决策见 §3.1）

| 维度 | A. 保留 PG | B. 引入 SQLite | C. 纯 JSON 文件 |
|---|---|---|---|
| **改造成本** | 极小（不改造） | 中（新增 SQLite 依赖 + 适配层） | 大（自实现文件锁 + 索引） |
| **能力损失** | 无 | 极小（事务/并发略有降级） | 大（无强事务，查询能力退化） |
| **运维负担** | 无变化 | 增加一个 SQLite 文件备份 | 增加文件同步 + 备份策略 |
| **与现有 LangGraph 集成度** | 100%（AsyncPostgresSaver 即用） | 中（需桥接 checkpointer 适配层） | 低（LangGraph 不支持 JSON checkpointer） |
| **可读 / 可调试** | 差（需 psql） | 中 | 极好 |
| **跨设备 / 跨用户恢复** | 自然支持 | 弱（文件需手动同步） | 弱（文件需手动同步） |
| **多进程并发** | PG 行锁 | SQLite WAL | 文件锁（需要 fcntl） |
| **事务一致性** | 强 | 中 | 弱 |
| **业界对标** | LangGraph / CrewAI / OpenAI 全部用 PG | AutoGen Studio 等单机工具 | 几乎无主流案例 |
| **风险** | 无新增风险 | 数据分散（PG + SQLite）；备份策略二选一 | 难以支持 LangGraph HIL 中断恢复（需 checkpoint 机制） |

> **决策解读**：最终采纳的不足任何单选一方案，而是**融合架构**——元数据/执行状态沿用方案 A（PG），"JSON 可读"诉求通过新增 L2 记录层（吸收方案 C 的文件形态，但不承担主存储/checkpointer 职责）满足。方案 B（SQLite）四项触发条件零命中，且 LangGraph 官方 SqliteSaver 为同步实现与本项目全异步链路不匹配，降级为否决记录（§5）。

---

## 4. 上下文分层架构（议题 1 核心设计）

### 4.1 L2：JSON 对话记录层

#### 4.1.1 存储形态

- **格式**：JSONL（每行一条消息事件，append-only）
- **路径**（采纳 G2 v3.3 预留路径，User 嵌套层）：

```
{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.jsonl
```

- **写入策略**：append + 进程内 `asyncio.Lock`（per-file）+ 原子写（tmp + rename，仅全量重建时使用）
- **每行 schema（议题 5 定稿）**：

| 字段 | 类型 | 适用行 | 说明 |
|---|---|---|---|
| `seq` | int | 全部 | 会话内单调递增序号（1 起） |
| `ts` | str | 全部 | ISO8601 UTC（如 `2026-08-27T10:00:00Z`） |
| `type` | str | 全部 | 枚举：`message` / `tool_call` / `summary` |
| `role` | str | message | `user` / `assistant` |
| `content` | str | message, summary | 正文（summary 行为压缩事件留档文本） |
| `name` | str | tool_call | 工具名 |
| `summary` | str | tool_call | 工具调用摘要 |
| `metadata` | object | 可选，全部 | 预留字段，本期不强制填（后续可放 model/token/耗时） |

```json
{"seq": 1, "ts": "2026-08-27T10:00:00Z", "type": "message", "role": "user", "content": "..."}
{"seq": 2, "ts": "2026-08-27T10:00:05Z", "type": "tool_call", "name": "read_file", "summary": "..."}
{"seq": 3, "ts": "2026-08-27T10:00:06Z", "type": "summary", "content": "...（压缩事件留档）"}
```

#### 4.1.2 写入时机（议题 1 决策：方案甲 runtime 钩子）

仿照现有 `_fire_memory_add` 模式，在 `AgentAppRuntime.ainvoke / astream` **成功路径**上挂 fire-and-forget 写入钩子（`_fire_context_record`）：

- 写入内容：本轮 user 消息（入参）、assistant 最终结果、（后续 phase：subagent 中间事件）
- 失败不阻断响应，落日志 + metrics
- 解决「G3 无 chat 端点则 L2 永远没数据」的鸡生蛋问题：基础设施 + 钩子本期落地，未来 chat spec 直接受益

#### 4.1.3 恢复语义

| 场景 | 行为 |
|---|---|
| 前端渲染历史 | 读 L2（明文流式读，不反序列化 checkpoint） |
| 续聊 | 走 L1 同 thread 增量输入（现状语义不变） |
| L2 缺失 / 损坏（fallback 自愈） | 从 L1 `get_chat_history` 现算重建 L2 文件（即原方案 A 的 export 逻辑；议题 5 定案：export 端点与自愈共用同一份 service 代码，见 §11.5.3） |
| checkpoint 丢失 / thread 被清（灾难恢复，可选能力） | 从 L2 重建：旧消息作为初始 messages 灌入新 thread。**议题 5 定案：后续 phase，本期仅存档草案**（原因：chat 端点不在本期，重建后无消费方可验证效果）。草案形态：`POST /sessions/{sid}/rebuild` —— 读 L2 行流 → 组装初始 messages → 新 thread 灌入 → Session 行指向新 thread |

### 4.2 L1 压缩接入（议题 1 决策）

- **方案**：deepagents 0.7.5 自带 `SummarizationMiddleware`（`create_deep_agent(middleware=[...])` 传参，项目已在 `assembly.py` 使用同机制传 `TurnLimitMiddleware`）
- **阈值来源**：`AgentApp.context_size` **模型字段**（新增，int，token 数；NULL = 退回 `settings.DEFAULT_AGENT_CONTEXT_SIZE` 全局默认）
- **fallback 链**（deepagents 自带，无需自研）：`SummarizationMiddleware` 摘要 → `_message_eviction` 尾部驱逐 → `_overflow_clip` 溢出裁剪
- **可观测**：压缩事件（`SummarizationEvent`）落 structlog 日志 + Prometheus metrics（新增 counter，如 `context_compression_total{app_id, status}`）
- **与 L2 的联动**：压缩不可逆地替换 checkpoint 内旧消息 → **完整历史仅存于 L2**（这是 L2 存在的最强理由）
- **实施范围**：本期接入（alembic 加 `AgentApp.context_size` 字段 + assembly 挂 middleware + 可观测）；中文摘要 prompt 定制为可选优化项

### 4.3 DoD（分层架构）

- [ ] L2 JSONL 读写 service（`context_store.py`：append / 流式读 / 全量重建 / 删除清理，见 §11.5.5）
- [ ] `AgentAppRuntime.ainvoke/astream` 成功路径挂 `_fire_context_record` 钩子
- [ ] L2 缺失时从 L1 自愈重建（读侧 fallback）
- [ ] alembic：`AgentApp.context_size` 字段（nullable int）+ `settings.DEFAULT_AGENT_CONTEXT_SIZE`
- [ ] `assembly.py` 编译时挂 `SummarizationMiddleware`（阈值取 `AgentApp.context_size` 或 settings 默认）
- [ ] 压缩事件日志 + metrics
- [ ] SubAgentTestTrace → `SubAgentTrace` 彻底更名（表名/索引/类名/API 路径/前端全链，见 §11.5.4.C）
- [ ] 单测覆盖：钩子写入 / fallback 自愈 / middleware 挂载（阈值解析）/ 表更名迁移

---

## 5. 方案 B（SQLite）：否决记录（2026-08-27 议题 1 降级）

> **状态**：❌ 已否决，不实施。原「备选实施」定位降级为触发条件存档；以下实施清单仅作未来重新评估时的参考，不是可执行任务。

### 5.1 否决理由（结合最新代码事实）

1. **四项触发条件零命中**：无 PG 性能瓶颈证据（chat TPS / p95 从未观测到问题）、无多设备离线需求、历史消息未占 PG 80%+ 空间
2. **异步链路不匹配**：LangGraph 官方 `SqliteSaver` 为同步实现，与本项目全异步链路（AsyncPostgresSaver + 共享 async 池）不匹配
3. **runtime 深度耦合**：`_build_checkpointer()` 走共享池 + 进程级一次性 `setup()` DDL，替换需重写 runtime 横切语义层

### 5.2 未来重新评估的触发门槛（存档，满足任一才重启评估）

- LangGraph 官方**异步** SqliteSaver 发布且兼容性 ≥ 90%
- PG 性能瓶颈明确（chat TPS < 100 / p95 latency > 500ms）
- 多设备离线场景需求明确
- 历史消息占 PG 80%+ 空间且运维侧强烈要求缩减

---

## 6. 方案 C（纯 JSON 主存储）：架构性否决 + 诉求转移（2026-08-27）

> **状态**：❌ 已否决——但用户"JSON 持久化上下文"的诉求**已转移至 L2 记录层**（§4.1），以"职责分层"而非"主存储替换"的方式满足。

### 6.1 主存储替换不可行的原因（保留存档）

1. **LangGraph 不支持 JSON checkpointer** —— 必须自实现 Checkpointer 适配层
2. **HIL 中断恢复必需 SQL 持久化** —— JSON 文件无法支持 thread_id 索引与状态机持久化
3. **文件锁 / 并发控制复杂** —— 多进程场景需 fcntl 串行化
4. **查询能力退化** —— 无法做"用户最近活跃会话""按 agent_app_id 聚合"等分析查询

### 6.2 诉求转移对照

| 原方案 C 诉求 | 转移后的满足方式 |
|---|---|
| 上下文 JSON 可读 / 可调试 | L2 JSONL 明文文件（§4.1） |
| 上下文可控 / 可编辑 | L2 append-only + 全量重建工具 |
| 压缩前完整历史保留 | L2 不受 `SummarizationMiddleware` 影响，全量留档（§4.2） |
| 崩溃后恢复 | L2 fallback 重建 / 灾难恢复路径（§4.1.3） |

---

## 7. L2 JSON 文件位置：定案（议题 1）

**采纳 G2 v3.3 嵌套 User 层路径**（G2 spec §12.3 预留，两端一致）：

```
{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.jsonl
```

- 与 G2 workspace 语义一致：session 上下文天然归属 (app, user) 二元组；删除 AgentApp 清理子树、取消 user 关联清理该 user 目录，L2 文件随于同一套清理逻辑
- 旧行政区讨论（Agent 层 X / User 层 Y / 混合 Z）已随 G2 v3.3 嵌套设计自然消解——嵌入在 Agent 下的 User 层同时满足"按 user 聚合"（`user_id` 路径段）与"按 agent 分层"（`app_id` 路径段）
- **清理时机**：删除 session / 删除 AgentApp / 取消 user 关联时同步清理（详见议题 4 落盘章节，待补）

---

## 8. DoD（Phase 3 推荐方案 A + 新 CRUD API）

> 本节是 G3 推荐方案 A（JSON 视图层）+ 新 CRUD API（§11）的统一 DoD 清单。

### 8.1 推荐方案 A：JSON 视图层（议题 5 定案：export 端点）

- [ ] 后端 `GET /sessions/{session_id}/export` 端点（**全新实现**至 `app/api/v1/sessions.py`；原「从 chatbot.py 拆出」不可能——chatbot.py 已是空 stub）
- [ ] 读取策略：L2 优先 + L1 fallback 自愈（`read_or_rebuild_l2` 统一入口，见 §11.5.3）
- [ ] 响应：文件下载流式（`Content-Disposition: attachment`，非 envelope；json = 元数据头 + messages 数组，jsonl = `application/x-ndjson` 逐行）
- [ ] 支持 `format=json` / `format=jsonl` 两种格式（Query 参数校验，非法值 422）
- [ ] 鉴权模式：`Depends(get_current_user)` + 函数内 session 归属校验（404 防枚举）
- [ ] 前端 ChatView 列表页改造（议题 8 定案，见 §11.6）：会话列表 + 行内导出下载；聊天区占位归 G4
- [ ] 文档：
  - `docs/observability.md` 新增"会话调试导出"章节
  - `docs/agentapp-manual-testing.md` 第 7 节新增"导出 session 历史"小节

### 8.2 新会话 CRUD API（详见 §11；议题 9 定案：保持双份完整清单，便于逐项验证）

> 与 §11.8 同步维护（修订时双改）；§4.3（分层架构）/ §8.1（export）各自独立清单，四处均为可勾选验证点。

- [ ] `app/api/v1/sessions.py` 新文件（6 端点：5 CRUD + export，全新实现——chatbot.py 是空 stub 且议题 6 删除）
- [ ] `config.py`：`RATE_LIMIT_ENDPOINTS` 新增 `sessions` key（全端点统一，含 POST；复核补）
- [ ] `app/schemas/session.py` 新增 3 个 schema：`SessionRead` / `SessionCreate` / `SessionUpdate`（`SessionListResponse` 作废——`PageResult[SessionRead]` 取代，议题 3/7）
- [ ] `app/services/agents/sessions_service.py` 新文件（8 方法，见 §11.7；议题 7：非 database.py）+ `app/services/agents/context_store.py`（L2 纯文件操作）
- [ ] `runtime.py` 新增模块级 `delete_thread_checkpoint(session_id)` helper（`_build_checkpointer` + `adelete_thread`，不经 `get_runtime`；池不可用落警告跳过）
- [ ] DELETE /sessions/{id} 级联三层：L1→L2→L0 顺序，L1/L2 失败落日志 + metrics 不阻断（§11.5.1）
- [ ] `delete_agent_app` 增强：先取 thread_ids → 尽力清 checkpoint → DB 事务删 Session 行 + assoc + app（顺序约束见 §11.5.2）
- [ ] ~~旧 `/auth/session` 端点保留 1 个 release 后删除~~（作废：G1 已直接删除端点，见 §11.1 事实修正）
- [ ] 前端 `agent-web/src/api/sessions.ts` 新增 6 个 API 包装（5 CRUD + exportSessionHistory，全新编写非移入）+ ChatView 列表页改造（议题 8）
- [ ] 前端测试 `agent-web/tests/sessions.spec.ts` 覆盖 6 函数 + 列表页交互（分页 / 过滤 / 删除确认 / 导出）+ 越权场景

---

## 9. 验证

### 9.1 单元测试（推荐方案 A：JSON 视图层；议题 5 对齐文件下载语义）

- `tests/unit/api/test_sessions.py::test_export_*`：
  - `test_export_session_history_returns_messages`（format=json 含元数据头 + Content-Disposition）
  - `test_export_session_history_format_jsonl`（application/x-ndjson 逐行）
  - `test_export_fallback_rebuilds_l2`（L2 缺失时从 L1 现算并写回自愈）
  - `test_export_orphan_session_without_app`（app 已删：仅返回 L2 现存内容，不报错）
  - `test_export_session_history_other_user_forbidden`（404 防枚举）
  - `test_export_session_history_empty_messages`（空会话返空 messages 200）
  - `test_export_invalid_format_rejected`（format=xml → 422）

### 9.2 单元测试（新 CRUD API，详见 §11.9）

- `tests/unit/api/test_sessions.py::test_crud_*`：
  - `test_list_sessions_returns_only_owned`
  - `test_get_session_404_for_other_user`
  - `test_create_session_validates_agent_app_published`
  - `test_create_session_requires_agent_app_id`（复核改：不传→422，议题 3 必填定案）
  - `test_create_session_associates_user`（复核补：自动 associate 幂等语义）
  - `test_update_session_other_user_returns_404`
  - `test_delete_session_cascades_checkpoint`

### 9.3 集成测试

- `tests/integration/api/test_session_export.py`：
  - `test_runtime_invoke_to_export_full_flow`（脚本调 runtime 多轮 → export 内容完整；替代原 test_login_to_chat_to_export——G3 无 chat 端点）
  - `test_export_after_hil_interrupt_includes_decision_history`
  - `test_export_consistent_with_get_messages`（export 与 message_count 同源一致）
- `tests/integration/api/test_session_crud.py`（详见 §11.9）：
  - `test_full_session_lifecycle`
  - `test_concurrent_delete_idempotent`
  - `test_message_count_reflects_langgraph_state`

### 9.4 手工冒烟

1. JSON 导出（注意：G3 无 chat 端点，L2 数据由 runtime 钩子产生——用测试脚本/evals 直接调 `runtime.ainvoke` 多轮）：
   login → create session → 脚本调 runtime 多轮 → `GET /sessions/{sid}/export?format=json`
2. 验证返回 JSON 含完整 user/assistant 消息流 + tool_calls 行 + Content-Disposition 附件头
3. 验证 format=jsonl 行为（application/x-ndjson，每行一条）
4. 验证 fallback 自愈：手动删掉 L2 文件 → 再 export → 文件自动重建且内容一致
4. **新 CRUD 流程**：login → POST /sessions（agent_app_id=1）→ GET /sessions 列表可见 → PATCH /sessions/{sid}（name="新会话"）→ DELETE /sessions/{sid} → 列表已删除
5. **越权场景**：user A 登录后尝试访问 user B 的 session_id → 全部端点返 404（不是 403）
6. **级联清理**：创建 session → chat 几条消息 → 删除 session → LangGraph checkpoint 中无该 thread

---

## 10. 关键决策（详见 `open-questions.md`）

- **Q5**：Session 存储是否真要改造？推荐**保留 PG**，JSON 仅作视图层导出。
- **Q7（新增）**：新会话 CRUD API 设计——**已决策**：URL 改为 RESTful `/sessions`（旧 `/auth/session` 注释 1 个 release 后删除）；鉴权统一 `Depends(get_current_user)` + 函数内 `X-Session-Id` header 校验；chatbot 端点整体直接废弃。详见 §11。

---

## 11. 全新会话 CRUD API（承接 G1 的 `/auth/session` 注释废弃）

> **状态**：Phase 3 实施范围。承接 G1 Phase 1 的 `/auth/session` 注释废弃，落地完整 CRUD。
> **承接关系**：`spec-g1-auth.md` §3.1（注释废弃原 endpoint）→ `spec-g3-session.md` §11（本节，新 CRUD 设计）

### 11.1 URL 设计（2026-08-27 议题 3 事实修正）

| 阶段 | 端点 | 状态 |
|---|---|---|
| Phase 1（G1 实施事实） | `POST /auth/session` 等会话端点 | **已被 G1 直接删除**（非 noop 注释保留——auth.py 尾部注释明确 "The endpoints ... are gone"，路由不存在，调用返 404）；原「保留 1 个 release 后删除」表述作废 |
| **Phase 3（新）** | `GET /sessions`、`GET /sessions/{sid}`、`POST /sessions`、`PATCH /sessions/{sid}`、`DELETE /sessions/{sid}`、`GET /sessions/{sid}/export`（议题 5 已定，见 §11.5.3） | 本 spec 设计 |

> 命名风格：RESTful 资源名 `/sessions`（复数），与 LangGraph Platform `/threads` 风格对齐。

### 11.2 端点清单（议题 3 修订）

| 方法 | URL | 用途 | 鉴权 |
|---|---|---|---|
| `GET` | `/sessions` | 列出当前 user 的 session（`created_at desc`；`?agent_app_id=` 可选过滤；PageResult 分页；message_count 不填——议题 2） | `Depends(get_current_user)` |
| `GET` | `/sessions/{session_id}` | 单个 session 详情（message_count 填充：L2 行数优先 fallback checkpoint；**不触发 lazy workspace 校验**——纯元数据读，export/续聊走 get_runtime 时自然触发） | 同上 + session 归属校验 |
| `POST` | `/sessions` | 创建 session（**agent_app_id 必填 int**；**自动 associate**——见 §11.5；返回 **201**，项目惯例） | `Depends(get_current_user)` |
| `PATCH` | `/sessions/{session_id}` | 更新 name（重命名，1-100） | 同上 + session 归属校验 |
| `DELETE` | `/sessions/{session_id}` | 删除 + 级联清理（L0 行 + L1 checkpoint + L2 JSONL，详见议题 4） | 同上 + session 归属校验 |

### 11.3 鉴权模式（Phase 3 统一，议题 3 修订）

```python
# 模式：Depends(get_current_user) + path 参数 session_id + 函数内归属校验
async def _resolve_session_or_404(
    user: User, session_id: str
) -> Session:
    """根据 session_id 解析 session，校验归属，不属于当前 user 返 404。"""
    target = await sessions_service.get_session(session_id)
    if target is None or target.user_id != user.id:
        # 故意用 404 而非 403：避免泄露 session_id 是否存在
        raise HTTPException(status_code=404, detail="session not found or not owned by user")
    return target
```

> **关键决策**：所有 session 操作的越权统一返 **404 而非 403** —— 与 G1 一致，避免泄露 session_id 是否存在（防 enumeration attack）。
>
> **Q7 表述修正（议题 3）**：原决策文本「函数内 `X-Session-Id` header 校验 session 归属」作废——X-Session-Id 是 G1 §3 为**未来 chat 发消息端点**设计的机制；本 spec 全部端点用 path 参数 `{session_id}` + 归属校验，无 header。

### 11.4 数据模型与 alembic 迁移（2026-08-27 议题 2 落盘）

> 同一批 alembic 迁移覆盖议题 1 + 议题 2 全部 schema 变更。

#### 11.4.1 `Session` 表变更

| 变更 | 类型 | 说明 |
|---|---|---|
| `agent_app_id` str → **int** | 列类型改造 | 存量值已全部为合法数字串（bootstrap 回填保证，"system-default" 占位符已消亡）；迁移 `USING agent_app_id::int`；model 同步改 `Optional[int]`；仍**不加 FK**（弱关联，AgentApp 硬删后孤儿 session 行为见议题 4） |
| 新增 `updated_at` | 加列 | `default now + onupdate now`；PATCH 重命名后前端可感知 |

#### 11.4.2 `AgentApp` 表变更

| 变更 | 类型 | 说明 |
|---|---|---|
| 新增 `context_size` | 加列（nullable int） | token 阈值；NULL → 回退 `settings.DEFAULT_AGENT_CONTEXT_SIZE`；**默认开启压缩**（全局阈值兑底，所有 app 默认挂 `SummarizationMiddleware`，显式配置可覆盖） |

#### 11.4.3 `SubAgentTestTrace` 表更名（议题 1 决策；复核修正表名，范围见 §11.5.4.C）

- 表名 `subagent_test_trace` → **`subagent_trace`**（alembic `ALTER TABLE ... RENAME`；索引同步 `ix_subagent_trace_created_at`）；更名范围为**彻底更名**（表名/类名/API 路径/前端全链，议题 6 定案，见 §11.5.4.C）；chat 场景落表归后续 phase

#### 11.4.4 settings 新增

- `DEFAULT_AGENT_CONTEXT_SIZE: int = 128000`（全局压缩阈值兑底；议题 2 决策：默认挂，非显式开启）

#### 11.4.5 message_count 填充策略（议题 2 决策）

- **详情端点填**：优先 L2 JSONL 行数（轻量流式计数）；L2 缺失 fallback checkpoint `get_chat_history` 现算
- **列表端点不填**（None）：避免 N+1 反序列化 checkpoint state

#### 11.4.6 Schema 设计（`app/schemas/session.py` 新文件，议题 2 修订）

```python
from pydantic import BaseModel, Field
from datetime import datetime

class SessionRead(BaseResponse):
    """GET /sessions 列表项 / GET /sessions/{sid} 详情共用 schema。"""
    session_id: str
    name: str = Field(default="", max_length=100)
    agent_app_id: int | None = None  # 议题 2：已迁移为 int（与 AgentApp.id 对齐）
    created_at: datetime
    updated_at: datetime | None = None  # 议题 2：新增列支撑
    # 仅详情端点填充（议题 2 决策：列表 None，避免 N+1）
    message_count: int | None = Field(default=None)


class SessionCreate(BaseModel):
    """POST /sessions 请求体。"""
    agent_app_id: int = Field(  # 议题 3：必填（无则 422）
        ...,
        description="绑定的 AgentApp id；创建时自动 associate（幂等）"
    )
    name: str = Field(default="", max_length=100)


class SessionUpdate(BaseModel):
    """PATCH /sessions/{sid} 请求体（仅支持重命名）。"""
    name: str = Field(..., min_length=1, max_length=100)

# SessionListResponse：作废（议题 3/7）——列表响应用 PageResult[SessionRead]（app/schemas/base.py 既有），不自定义 items/total
```

> 注：模型主键名为 `id`，API 输出用 `session_id`（Pydantic alias / 构造时映射）；分页风格已在议题 3 统一为项目既有 **PageResult（page/pageSize）**。

### 11.5 端点实现（伪代码，议题 3 修订）

```python
# app/api/v1/sessions.py（全新文件；chatbot.py 已是空 stub，无可拆代码）
# 服务层落点：sessions_service（议题 7 定细节）；DB 会话：Depends(get_db_session)

@router.get("/sessions", response_model=ApiResponse[PageResult[SessionRead]])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def list_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
    agent_app_id: Optional[int] = Query(default=None),  # 可选过滤（议题 3）
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[PageResult[SessionRead]]:
    """created_at desc + PageResult 分页；message_count 不填（议题 2）。"""
    result = sessions_service.list_user_sessions(
        db, user_id=user.id, agent_app_id=agent_app_id, page=page, page_size=page_size
    )
    return ApiResponse.success(result)


@router.get("/sessions/{session_id}", response_model=ApiResponse[SessionRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def get_session(
    request: Request,
    session_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[SessionRead]:
    target = await _resolve_session_or_404(user, session_id)
    # message_count：L2 JSONL 行数优先，fallback checkpoint 现算（议题 2）
    message_count = await sessions_service.count_messages(target)
    # 不触发 lazy workspace 校验（议题 3：纯元数据读；export/续聊走 get_runtime 自然触发）
    return ApiResponse.success(sessions_service.to_read(target, message_count=message_count))


@router.post("/sessions", response_model=ApiResponse[SessionRead], status_code=201)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])  # 复核修正：与全端点统一（原误用 session_create）
async def create_session(
    request: Request,
    body: SessionCreate,  # agent_app_id: int 必填（议题 3）
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[SessionRead]:
    """创建入口：自动 associate（议题 3 决策 i）。

    复用 agent_apps_service.associate_user_with_app（幂等）：
    published 校验 + association upsert + (Global+Agent)→User 物化 + 盖 sync hash 章。
    「用户对已发布 app 开会话」= 默认授权；未发布返 422，app 不存在返 404。
    """
    try:
        await agent_apps_service.associate_user_with_app(
            db, user_id=user.id, app_id=body.agent_app_id, current_user_id=user.id
        )
    except agent_apps_service.AgentAppNotFoundError:
        raise HTTPException(status_code=404, detail="agent_app not found")
    except agent_apps_service.AgentAppNotPublishedError:
        raise HTTPException(status_code=422, detail="agent_app is not published")

    new_session = await sessions_service.create_session(
        db, user_id=user.id, username=user.username,
        agent_app_id=body.agent_app_id, name=body.name,
    )
    logger.info(
        "session_created",
        session_id=new_session.id,
        user_id=user.id,
        agent_app_id=body.agent_app_id,
        auto_associated=True,
    )
    return ApiResponse.success(sessions_service.to_read(new_session))


@router.patch("/sessions/{session_id}", response_model=ApiResponse[SessionRead])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def update_session(
    request: Request,
    session_id: str,
    body: SessionUpdate,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[SessionRead]:
    target = await _resolve_session_or_404(user, session_id)
    updated = await sessions_service.rename_session(db, target.id, body.name)
    logger.info(
        "session_renamed",
        session_id=session_id,
        user_id=user.id,
        new_name=body.name,
    )
    return ApiResponse.success(sessions_service.to_read(updated))


@router.delete("/sessions/{session_id}", response_model=ApiResponse[None])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def delete_session(
    request: Request,
    session_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[None]:
    """议题 4：响应惯例与项目一致（ApiResponse[None] + 200，非 204）——
    全部 7 个现有 DELETE 端点（apps/skills/subagents/providers/mcp_servers）均为
    此惯例，前端 request.ts 统一解包。"""
    target = await _resolve_session_or_404(user, session_id)

    # 级联清理三层（顺序与容错语义见 §11.5.1，议题 4 落盘）：
    # 1. L1：LangGraph checkpoint（独立 helper，不经 get_runtime）
    # 2. L2：JSONL 文件（{DATA_ROOT}/agents/<app_id>/users/<uid>/sessions/<sid>.jsonl）
    # 3. L0：PG Session 元数据行（最后删，删除成功即响应成功）
    result = await sessions_service.delete_session_cascade(db, target)  # 返回 CascadeResult（复核修正）

    logger.info(
        "session_deleted",
        session_id=session_id,
        user_id=user.id,
        checkpoint_cleaned=result.checkpoint_cleaned,  # 编排真实结果（尽力语义下可能 False）
        jsonl_cleaned=result.jsonl_cleaned,
    )
    return ApiResponse.success(None)
```

### 11.5.1 删除级联与容错语义（2026-08-27 议题 4 落盘）

#### 清理顺序与失败语义

三层清理非事务性，定案顺序 **L1 → L2 → L0**（前两层可重入、幂等，放前；L0 是「真相源」，删除成功即向用户返回成功）：

| 层 | 清理动作 | 失败语义 |
|---|---|---|
| L1 checkpoint | `delete_thread_checkpoint(session_id)`（独立 helper，见下） | 尽力：失败落 `session_cascade_checkpoint_failed` 日志 + metrics counter，**不阻断** |
| L2 JSONL | 按 Session 行 `(agent_app_id, user_id, session_id)` 拼路径删除文件；文件不存在视为成功 | 尽力：同上，落 `session_cascade_jsonl_failed`；残留文件不影响 API 语义 |
| L0 PG 行 | `delete_session_cascade` 内部删行（§11.7；无独立 delete_session 方法） | 真正的完成标志：成功 → 200；此后重复 DELETE 返 404（幂等） |

> 编排返回 `CascadeResult(checkpoint_cleaned: bool, jsonl_cleaned: bool)`（复核修正）——尽力清理语义下失败层为 False，供端点日志与 metrics 上报真实结果。

**孤儿注记**：L0 删除后若 L1/L2 清理此前失败，残留 checkpoint/文件成为无主存储——不影响任何 API 语义（session 已 404），仅占存储；后续 phase 可加孤儿扫描任务（本期不做，记为已知债务）。

#### checkpoint 清理独立 helper（议题 4 决策：不经 `get_runtime`）

`get_runtime` 要求 app 存在且 published，但 DELETE session 时 app 可能已被硬删/unpublish——因此清理路径必须与 runtime 解耦：

```python
# app/services/agents/runtime.py（模块级，与 _build_checkpointer 同层）
async def delete_thread_checkpoint(session_id: str) -> None:
    """Delete every checkpoint of one thread WITHOUT requiring an AgentApp.

    与 clear_chat_history 的区别：后者是 AgentAppRuntime 实例方法（依赖已加载
    的 app 配置），本 helper 只依赖共享连接池——app 已删/unpublish 均可调用。
    """
    checkpointer = await _build_checkpointer()
    if checkpointer is None:  # 池不可用（与 runtime 降级语义一致）
        logger.warning("session_cascade_checkpoint_skipped_no_pool", session_id=session_id)
        return
    await checkpointer.adelete_thread(session_id)
```

- 池不可用（`None`）时**跳过并落警告日志**（接受孤儿 + 警告）：池不可用本身就是系统级故障（此时 chat 也不可用），不应阻断 L0 删除导致用户重试风暴。
- 复用方：DELETE /sessions/{id}（本节）与 delete_agent_app（§11.5.2）。

#### 幂等语义（并发删除）

- 第一次 DELETE：200；并发第二次（L0 行已删）：404（`_resolve_session_or_404` 兜底）
- L1/L2 清理动作本身幂等：`adelete_thread` 对不存在的 thread 无操作；文件删除对不存在的路径无操作

### 11.5.2 AgentApp 硬删联动（2026-08-27 议题 4 落盘：全量级联含 checkpoint）

#### 现状缺口

`agent_apps_service.delete_agent_app`（L315）现状：删 DB 行（app + assoc）+ `rmtree(agent_dir)`（级联 User 层，**L2 JSONL 自然覆盖**）——但**不清 Session 行、不清 checkpoint**，留下两类孤儿。

#### 增强后流程（顺序约束关键）

```python
# app/services/agents/agent_apps_service.py — delete_agent_app 增强
async def delete_agent_app(session, *, app_id, current_user_id):
    app_cfg = ...  # 现状：404 / 默认 app 保护检查不变

    # ① 先取 thread_ids（顺序约束：Session 行删除后即失去清理入口！）
    thread_ids = [row.id for row in session.exec(
        select(Session).where(Session.agent_app_id == app_id)
    ).all()]

    # ② 尽力清理每个 checkpoint（复用 §11.5.1 helper；失败落日志+metrics 不阻断）
    for tid in thread_ids:
        try:
            await runtime.delete_thread_checkpoint(tid)
        except Exception:
            logger.exception("app_delete_checkpoint_cleanup_failed", thread_id=tid, app_id=app_id)

    # ③ DB 事务删行（Session 行 + assoc + app，单事务）
    # ④ rmtree(agent_dir)（L2 随之消失，现状不变）
```

| 设计点 | 决策 |
|---|---|
| 顺序约束 | thread_ids 必须在删 Session 行**之前**取出（弱关联无 FK，行删了就找不到 thread 了） |
| checkpoint 清理失败 | 尽力语义（与 §11.5.1 一致）：落日志 + metrics，不阻断 DB 删除——app 删除是管理操作，不应被个别 checkpoint 残留阻断 |
| 性能注记 | 海量 session 时同步逐个 `adelete_thread` 变慢；本期接受（管理员操作低频）。可选优化（后续 phase）：批量 SQL `DELETE FROM checkpoints WHERE thread_id IN (...)`——直接耦合 LangGraph 表 schema，非官方 API，仅在性能实测不达标时考虑 |
| L2 文件 | 无需新增代码：`rmtree(agent_dir)` 已级联覆盖 `users/<uid>/sessions/` |

### 11.5.3 export 端点设计（2026-08-27 议题 5 落盘）

#### 读取策略：L2 优先 + L1 fallback 自愈（与 §4.1.3 同一份代码）

```python
async def read_or_rebuild_l2(session_row) -> list[dict]:
    """读侧统一入口（export / message_count / 前端渲染历史共用）：
    1. L2 文件存在且可解析 → 逐行读出（明文直读，不碰 checkpoint）
    2. 文件缺失/损坏 → 从 L1 get_chat_history 现算 → 原子写回 L2（自愈）→ 返回
    注意：get_chat_history 需要 runtime（get_runtime），此时 app 必须存在且 published；
    app 已删的孤儿 session 只能返回 L2 现存内容（无 fallback，不报错）。
    """
```

> 自愈写回用 §4.1.1 的原子写（tmp + rename）；app 已删场景降级为「仅返回 L2 现存内容」。

#### 响应形态：文件下载流式（项目首例惯例）

```python
@router.get("/sessions/{session_id}/export")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["sessions"][0])
async def export_session(
    request: Request,
    session_id: str,
    format: str = Query(default="json", pattern="^(json|jsonl)$"),
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
):
    target = await _resolve_session_or_404(user, session_id)
    rows = await sessions_service.read_or_rebuild_l2(target)
    if format == "jsonl":
        # Content-Type: application/x-ndjson；逐行流式（大 session 不全量拼内存）
        return StreamingResponse(iter_jsonl(rows), media_type="application/x-ndjson",
                                 headers={"Content-Disposition": f'attachment; filename="{session_id}.jsonl"'})
    # format=json：元数据头 + L2 行数组（单 JSON 文档）
    payload = {
        "session_id": target.id, "name": target.name,
        "agent_app_id": target.agent_app_id, "created_at": target.created_at,
        "exported_at": now_iso(), "message_count": len(rows), "messages": rows,
    }
    return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="{session_id}.json"'})
```

| 设计点 | 决策 |
|---|---|
| 非 envelope | 导出语义天然是文件下载：`Content-Disposition: attachment` + 文件名；**不走 ApiResponse 信封**（项目内文件下载首例，此处定惯例，后续同类端点照此） |
| 鉴权 | `Depends(get_current_user)` + 归属 404（防枚举，与其他端点一致） |
| format=json | 元数据头（session_id/name/agent_app_id/created_at/exported_at/message_count）+ `messages` 数组（L2 行原样） |
| format=jsonl | `application/x-ndjson` 逐行流式，L2 行原样 |
| 与 message_count 的关系 | 议题 2 已定 message_count 走 L2 行数优先 fallback checkpoint 现算——即同一 `read_or_rebuild_l2` 入口，两者天然一致 |
| 空会话 | L2 无文件且 L1 无 thread：export 返回空 messages（200，非 404——session 行存在即合法导出） |

> 注（复核补）：jsonl「逐行流式」当前指**响应构造阶段**（iter_jsonl 包装 list；read_or_rebuild_l2 返回完整 list，读取阶段仍全量物化）。大会话优化 = context_store 流式读（§11.5.5）直接对接响应生成器、避免全量物化——实施时可选，不改变端点契约。

### 11.5.4 遗留清理（2026-08-27 议题 6 落盘）

#### A. session_naming 孤儿链删除（4 处）+ G4 留痕

| # | 删除对象 | 说明 |
|---|---|---|
| 1 | `app/services/session_naming.py`（90 行） | 原子 claim + placeholder + LLM 后台起名；**全仓无调用方**（chatbot 时代遗留） |
| 2 | `app/core/metrics.py::session_names_generated_total` | Counter，仅 #1 使用 |
| 3 | `app/core/prompts/__init__.py::SESSION_TITLE_PROMPT`（+ 对应 txt 文件） | 仅 #1 使用 |
| 4 | `app/schemas/chat.py::SessionTitle` | 结构化输出 schema，仅 #1 使用 |

- **保留**（非孤儿）：`database_service.update_session_name`（PATCH /sessions 复用）；`schemas/chat.py` 其余类（`Message` 有活引用：runtime / test_hil_reassembly）
- **G4 留痕（用户决策）**：孤儿链删除但需记录到 G4 chat 改造待办——`overview.md` §4 Phase 4 行已登记；G4 启动时判断是否恢复「LLM 自动起名」能力（需求可能变化；git 历史可找回，以删除 commit 为准）

#### B. chatbot 文件遗留删除（2 处）

| # | 删除对象 | 说明 |
|---|---|---|
| 1 | `app/api/v1/chatbot.py`（26 行空 stub） | 无任何模块 import（grep 确认）；docstring 自述「Phase 2 清理」已到期 |
| 2 | `tests/unit/api/test_chatbot_runtime.py` | 整文件 `pytest.mark.skip` 的 deprecated 测试 |

- **保留**（不依赖文件存在）：`test_lifespan_smoke.py` / `test_chat_flow.py` 的 `/chatbot/*` 404 断言——验证路由不存在，删文件后依旧成立

#### C. SubAgentTestTrace 彻底更名（议题 6 定案：含 API 路径，一步到位）

| 层 | 现状 | 更名后 |
|---|---|---|
| 表名 + 索引 | `subagent_test_trace` / `ix_subagent_test_trace_created_at` | `subagent_trace` / `ix_subagent_trace_created_at` |
| 模型类 | `SubAgentTestTrace`（`app/models/subagent_trace.py`，文件名不变） | `SubAgentTrace`；docstring 同步概念扩展（测试 + chat 内嵌同源，见 §2.1） |
| API 路径 | `GET /subagents/{name}/test-traces[/{trace_id}]` | `GET /subagents/{name}/traces[/{trace_id}]` |
| 后端函数 | `list_subagent_test_traces` / `get_subagent_test_trace`（subagents.py L384/L433） | `list_subagent_traces` / `get_subagent_trace` |
| 前端 | `listSubAgentTestTraces` / `getSubAgentTestTrace`（api/subagents.ts）+ `test-traces` 路径 | `listSubAgentTraces` / `getSubAgentTrace` + `traces` 路径；2 个 Vue 组件（SubAgentTraceDetailDialog / SubAgentTraceHistoryDialog）组件名已无 test 字样，不改 |
| 测试 | `test_agent_apps_api.py` / `test_runner.py` 类名引用 | 同步 |
| 迁移 | alembic rename（RENAME TABLE + RENAME INDEX）+ `env.py` import 更新 | 新迁移文件 |

- 前后端同仓同步改，内部产品无外部兼容包衢；限流不变（复核修正）：trace 端点限流已用 `RATE_LIMIT_ENDPOINTS["subagent"]` key，更名无需动限流；`subagent_test` key 属 `POST /subagents/{name}/test`（执行测试端点），与表更名无关，**不动**。

### 11.5.5 服务层落点（2026-08-27 议题 7 落盘）

#### 模块分层

```
app/api/v1/sessions.py            # 端点（鉴权 / 参数校验 / 响应包装）
  └─ app/services/agents/sessions_service.py  # 业务编排（议题 7 定案）
       ├─ PG CRUD（Session 表；新实现，不委托 database.py）
       ├─ 级联删除编排（L1→L2→L0，§11.5.1）
       ├─ read_or_rebuild_l2 编排（§11.5.3：L2 优先 + L1 fallback 自愈）
       ├─ context_store（L2 文件读写）
       └─ runtime（delete_thread_checkpoint / get_chat_history 经 get_runtime）
app/services/agents/context_store.py          # L2 纯文件操作（议题 7 定案）
  ├─ append / 流式读 / 原子重建（tmp+rename）/ 删除
  ├─ 路径构造（{DATA_ROOT}/agents/<app_id>/users/<uid>/sessions/<sid>.jsonl）
  └─ per-file asyncio.Lock（进程内写串行化）
```

| 设计点 | 决策 |
|---|---|
| sessions_service 落点 | `app/services/agents/sessions_service.py`（与 agent_apps_service 并列）：依赖同包（runtime / skills_store 路径 / context_store）无跨包 import；延续 G2 函数式服务惯例 |
| context_store 落点 | `app/services/agents/context_store.py` 独立纯文件模块（仿 skills_store 定位，无 DB 依赖）：runtime 钩子（`_fire_context_record`）**只 import 它**——轻依赖，不拉 DB / 业务编排链 |
| database.py 旧方法 | **删除全部 5 个**（get_session / get_user_sessions / create_session / delete_session / update_session_name）：唯一调用方是议题 6 待删的 session_naming 孤儿，删后即死代码。新实现进 sessions_service（签名按 G3 新设计：分页 / agent_app_id 过滤 / 级联）。DatabaseService 类本身保留（user 相关方法有活引用） |
| SessionListResponse | **作废**：议题 3 已定 `PageResult[SessionRead]` 直接用，§8.2/§11.8 旧条目同步修正 |
| import 约束 | context_store 不 import sessions_service / runtime（反向依赖禁止）；runtime import context_store（钩子写 L2）；sessions_service import context_store + runtime —— 层次单向：api → sessions_service → (context_store / runtime) |

### 11.6 前端（议题 8 定案：ChatView 列表页改造）

**范围**：`agent-web/src/api/sessions.ts`（6 函数，全新编写——旧 spec「exportSessionHistory 从 chatbot 前端移入」作废，前端从未有过）+ ChatView.vue 改造为会话列表页（现状 15 行占位；默认路由 `/` → `/chat` 不变）；聊天区（消息流 / 流式 / HIL）占位归 G4。

```ts
// agent-web/src/api/sessions.ts
import { get, post, patch, del } from '@/utils/request'
import { toParams } from '@/utils/paginate'
import type { PageQuery, PageResult } from '@/types'  // 复核补：项目惯例（5 个现有 api 文件同源）

export interface SessionRead {
  session_id: string
  name: string
  agent_app_id: number
  created_at: string
  updated_at: string | null
  message_count?: number
}

// 复核改：遵循 PageQuery + toParams 惯例；agentAppId 为 session 特有过滤（交叉类型扩展）
export function listSessions(
  query: PageQuery & { agentAppId?: number } = {}
): Promise<PageResult<SessionRead>> {
  return get<PageResult<SessionRead>>('/sessions', { params: toParams(query) })  // 议题 3：分页 + 过滤
}

export function getSession(sessionId: string): Promise<SessionRead> {
  return get<SessionRead>(`/sessions/${sessionId}`)
}

export function createSession(body: { agent_app_id: number; name: string }): Promise<SessionRead> {
  return post<SessionRead>('/sessions', body)  // 议题 3：agent_app_id 必填
}

export function updateSession(sessionId: string, body: { name: string }): Promise<SessionRead> {
  return patch<SessionRead>(`/sessions/${sessionId}`, body)
}

export function deleteSession(sessionId: string): Promise<void> {
  return del<void>(`/sessions/${sessionId}`)  // 议题 4：ApiResponse[None] envelope，解包后 void
}

// 议题 5：文件下载响应（非 envelope，blob 接收）
export function exportSessionHistory(
  sessionId: string, format: 'json' | 'jsonl' = 'json'
): Promise<Blob> {
  return get<Blob>(`/sessions/${sessionId}/export`, {
    params: { format }, responseType: 'blob',
  })
}
```

**ChatView 列表页交互（遵循 agent-web 五件套惯例，参照 AgentList.vue 模式）**：WebAgentTable 分页列表（列：name / agent_app_id / message_count / created_at / 操作）+ agent_app 过滤下拉 + 「新建会话」对话框（选 app + 名称，WebAgentFormDialog）+ 行内重命名 + 删除确认（useConfirm，提示「将级联删除对话记录」）+ 行内导出下载（json / jsonl 二选一下拉，blob → a[download] 触发浏览器保存）。

> 注（复核补）：`GET /sessions` 在资源根直接分页，**无 `/page` 后缀**——为有意偏离（议题 3 RESTful 定案，对齐 LangGraph `/threads` 风格）；现有 `/skills/page`、`/apps/page` 等后缀风格不变，不迁移。

### 11.7 服务层方法清单（议题 7 修订：sessions_service，非 database.py）

```python
# app/services/agents/sessions_service.py（业务编排；DB 会话由调用方传入，函数式无类）

async def list_user_sessions(
    session: DBSession, *, user_id: int,
    agent_app_id: int | None = None, page: int, page_size: int,
) -> PageResult[Session]:
    """列出 user 的 session（created_at desc + agent_app_id 可选过滤 + 分页；议题 3）。"""

async def get_session(session: DBSession, session_id: str) -> Session | None:
    """按 session_id 查询（调用方自行校验 user_id 归属，404 防枚举）。"""

async def create_session(
    session: DBSession, *, user_id: int, username: str | None,
    agent_app_id: int, name: str,
) -> Session:
    """创建新 session（agent_app_id 必填 int；id 服务端生成 UUID；议题 2/3）。"""

async def rename_session(session: DBSession, session_id: str, new_name: str) -> Session | None:
    """重命名（同步更新 updated_at；议题 2）。"""

async def delete_session_cascade(session: DBSession, target: Session) -> CascadeResult:
    """级联删除编排（§11.5.1：L1 checkpoint → L2 JSONL → L0 PG 行，尽力清理语义）。

    返回 CascadeResult(checkpoint_cleaned, jsonl_cleaned)——失败层 False，
    供端点日志与 metrics 记录真实结果（复核修正）。
    """

async def read_or_rebuild_l2(target: Session) -> list[dict]:
    """读侧统一入口（§11.5.3：L2 优先 + L1 fallback 自愈写回；app 已删时仅返回 L2 现存内容）。"""

async def count_messages(target: Session) -> int | None:
    """message_count：L2 行数优先，fallback checkpoint 现算（议题 2）。"""

def to_read(row: Session, *, message_count: int | None = None) -> SessionRead:
    """ORM → 响应 schema 投影。"""
```

### 11.8 DoD（Phase 3 新 CRUD API；议题 4 补充级联条目）

- [ ] `app/api/v1/sessions.py` 新文件（6 端点：5 CRUD + export，全新实现——chatbot.py 是空 stub 且议题 6 删除）
- [ ] `config.py`：`RATE_LIMIT_ENDPOINTS` 新增 `sessions` key（全端点统一，含 POST；复核补）
- [ ] `app/schemas/session.py` 新增 3 个 schema：`SessionRead` / `SessionCreate` / `SessionUpdate`（`SessionListResponse` 作废——`PageResult[SessionRead]` 取代，议题 3/7）
- [ ] `app/services/agents/sessions_service.py` 新文件（8 方法，见 §11.7；议题 7：非 database.py）+ `app/services/agents/context_store.py`（L2 纯文件操作）
- [ ] `runtime.py` 新增模块级 `delete_thread_checkpoint(session_id)` helper（`_build_checkpointer` + `adelete_thread`，不经 `get_runtime`；池不可用落警告跳过）
- [ ] DELETE /sessions/{id} 级联三层：L1→L2→L0 顺序，L1/L2 失败落日志 + metrics 不阻断（§11.5.1）
- [ ] `delete_agent_app` 增强：先取 thread_ids → 尽力清 checkpoint → DB 事务删 Session 行 + assoc + app（顺序约束见 §11.5.2）
- [ ] ~~旧 `/auth/session` 端点保留 1 个 release 后删除~~（作废：G1 已直接删除端点，见 §11.1 事实修正）
- [ ] 前端 `agent-web/src/api/sessions.ts` 新增 6 个 API 包装（5 CRUD + exportSessionHistory，全新编写非移入）+ ChatView 列表页改造（议题 8）
- [ ] 前端测试 `agent-web/tests/sessions.spec.ts` 覆盖 6 函数 + 列表页交互（分页 / 过滤 / 删除确认 / 导出）+ 越权场景

### 11.9 验证（Phase 3 新 CRUD API）

#### 单元测试（`tests/unit/api/test_sessions.py`）

```python
async def test_list_sessions_returns_only_owned(user_a, user_b):
    """user_a 只能看到自己的 session，user_b 的 session 不可见。"""

async def test_get_session_404_for_other_user(user_a, user_b):
    """user_a 查询 user_b 的 session 返回 404（不是 403）。"""

async def test_create_session_validates_agent_app_published():
    """创建时若 agent_app 未发布，返回 422。"""

async def test_create_session_requires_agent_app_id():
    """不传 agent_app_id 返回 422（复核改：议题 3 必填定案，原「None 允许」条作废）。"""

async def test_create_session_associates_user(user_a):
    """创建时自动 associate（复检补）：association 行建立 + User 层物化 + 幂等（重复创建不报错不重建）。"""

async def test_update_session_other_user_returns_404(user_a, user_b):
    """user_a PATCH user_b 的 session 返回 404。"""

async def test_delete_session_cascades_checkpoint(user_a):
    """删除 session 后 LangGraph checkpoint 也被清理（断言 checkpointer 中无该 thread）。"""
```

#### 集成测试（`tests/integration/api/test_session_crud.py`）

```python
async def test_full_session_lifecycle(user_token):
    """login → create → list → patch → delete 完整闭环。"""

async def test_concurrent_delete_idempotent(user_token, session_id):
    """同一 session 被并发删除两次：第一次 200（议题 4：ApiResponse[None]），第二次 404。"""

async def test_delete_session_cascades_jsonl_file(user_token, session_id):
    """删除 session 后 L2 JSONL 文件同步消失（议题 4）。"""

async def test_delete_agent_app_cascades_sessions_and_checkpoints(admin_token):
    """硬删 AgentApp 后：该 app 的 Session 行全删 + 对应 checkpoint thread 全清
    + agent_dir（含 L2）消失（议题 4 §11.5.2 全量级联）。"""

async def test_message_count_reflects_langgraph_state(user_token, session_id):
    """SessionRead.message_count 与 LangGraph checkpoint 一致。"""
```

#### 手工冒烟（参考 `docs/agentapp-manual-testing.md` 第 7 节更新版）

1. 新 CRUD 流程：login → POST /sessions（agent_app_id=1）→ GET /sessions 列表可见 → PATCH /sessions/{sid}（name="新会话"）→ DELETE /sessions/{sid} → 列表已删除
2. 越权场景：user A 登录后尝试访问 user B 的 session_id → 全部端点返 404（不是 403）
3. 级联清理（议题 9 修正鸡生蛋）：创建 session → 测试脚本调 `runtime.ainvoke` 多轮（产生 L1 checkpoint + L2 JSONL）→ DELETE /sessions/{sid} → 验证：checkpoint 无该 thread + JSONL 文件消失 + 再 GET 返 404

### 11.10 回滚策略（议题 9 重写：按 v2 范围）

如 Phase 3 上线后有重大问题，分模块回滚：

| 模块 | 回滚动作 | 说明 |
|---|---|---|
| CRUD / export 端点 | revert `sessions.py` + `sessions_service.py` + `context_store.py` + `schemas/session.py` | 纯新增文件，无存量依赖 |
| runtime 钩子 | revert `_fire_context_record`（runtime/assembly 挂载点） | 钩子失败本就不阻断；回滚后 L2 停止写入 |
| checkpoint helper + app 硬删增强 | revert `runtime.py::delete_thread_checkpoint` + `agent_apps_service.py` | 回到孤儿现状（可接受，与 G2 前一致） |
| 压缩接入 | revert assembly 的 `SummarizationMiddleware` 挂载 | 回到无压缩现状（现状语义） |
| 3 个迁移 | alembic downgrade：Session 表（agent_app_id int FK + updated_at）；subagent_trace 更名；AgentApp.context_size | 均有 downgrade 路径；更名 downgrade rename 回原表名 |
| 前端 | revert `api/sessions.ts` + ChatView.vue | ChatView 回到 15 行占位 |
| L2 JSONL 残留文件 | 不处理或手动清理 | 不影响任何 API 语义（孤儿注记 §11.5.1） |

- **无可恢复的旧端点**：旧 `/auth/session`（G1 已删）与旧前端 `chatbot.ts`（从未有过）均无源；回滚 = revert G3 自身提交，不涉及恢复旧实现（原「旧 /auth/session 注释端点恢复为简化版」表述作废）。

### 11.11 后续 phase 待办汇总（议题 9 收口）

| 待办 | 出处 | 触发条件 |
|---|---|---|
| 灾难恢复：`POST /sessions/{sid}/rebuild`（L2→L1 重建） | §4.1.3（议题 5） | chat spec（G4）定消费方后 |
| chat 调用链挂 RunTracer 落 `subagent_trace` 表 | §2.3 | G4 chat 改造 |
| 判断是否恢复 LLM 自动起名（session_naming 孤儿链 4 处） | §11.5.4.A / overview.md Phase 4 | G4 启动时 |
| 孤儿 checkpoint / JSONL 扫描清理任务 | §11.5.1 孤儿注记 | 存储残留实测成问题时 |
| disassociate 时的 Session 行 / checkpoint 级联语义 | §12.3 清理时机 | 关联管理需求明确时 |
| L2 `metadata` 字段启用（model / token / 耗时） | §4.1.1（议题 5） | 可观测性增强需求 |
| app 硬删批量 checkpoint 删除（直接 SQL） | §11.5.2 性能注记 | 硬删性能实测不达标时 |
| 压缩中文摘要 prompt 定制 | §4.2 | 压缩质量实测不满意时 |

---

## 12. G2 集成接口预留（2026-08-25 追加；2026-08-26 更新签名）

> 本节是 G2（spec-g2-workspace.md）审查期间由 G2 团队指定的集成接口。G3 实施时**必须按本节约定**调用 G2 提供的函数 / 端点。
> **签名一致性说明**：此接口签名与 G2 spec §4.3、§9.3、§12.1 完全一致。

### 12.1 G2 提供的接口

G2 实施完成后，将提供以下接口供 G3 使用：

#### 12.1.1 `ensure_user_workspace_up_to_date` 函数

```python
# app/services/agents/agent_apps_service.py（统一入口）
async def ensure_user_workspace_up_to_date(
    session: AsyncSession,
    *,
    user_id: int,
    app_id: int,
) -> bool:
    """Lazy 校验：User 层与 (Global + Agent) 集合是否一致，不一致则增量同步。

    G3 在以下时机调用：
    - POST /sessions（创建 session，不带 session_id）入口
    - GET /sessions/{session_id}（加载 session，带 session_id）入口
    - 其他需要 user 层 skill 最新副本的场景

    内部行为：
    - 比对 AgentApp.workspace_hash（DB）与 user 层实际 hash
    - 不一致 → 调用 materialize_to_user_combined 增量同步，返回 True
    - 一致 → 跳过，返回 False

    Args:
        session: 数据库会话（AsyncSession）
        user_id: 用户 ID（int，从 API 层 get_current_user 获取）
        app_id: AgentApp ID

    Returns:
        bool: True 表示执行了重新复制；False 表示 hash 命中跳过。

    Raises:
        AgentAppNotFoundError: app_id 不存在
    """
```

> **注（2026-08-27 复核补）**：上述 docstring 中「G3 在以下时机调用」为 G2 审查期（2026-08-25）的原始建议，已被议题 3 逐端点决策取代——`POST /sessions` 改用更强的自动 associate（`associate_user_with_app`，§12.2.1）；`GET /sessions/{sid}` 不触发 lazy 校验（纯元数据读，§12.2.2）。实际调用时机以 §12.2 为准。

#### 12.1.2 `get_runtime` 签名（2026-08-27 事实修正）

G2 实际实现中 `get_runtime` **没有** `lazy_workspace_sync` 参数——内部**无条件**先执行 lazy 校验（`runtime.py` get_runtime → `ensure_user_workspace_up_to_date`）：

```python
# app/services/agents/runtime.py get_runtime（实际签名，G2 已实现）
async def get_runtime(session: Session, app_id: int, *, user_id: int) -> AgentAppRuntime:
    ...
    # Lazy user-layer validation (D21): 无条件执行，不可关闭
    await agent_apps_service.ensure_user_workspace_up_to_date(
        session, user_id=user_id, app_id=app_cfg.id
    )
    ...
```

> **推论**：G3 端点内若再显式调用一次 `ensure_user_workspace_up_to_date`，是双重调用（hash 命中时代价小，但语义冗余）。议题 3 已逐端点决策，见 §12.2。

### 12.2 G3 集成点（2026-08-27 议题 3 修订）

#### 12.2.1 `POST /sessions`（创建）：自动 associate（比 lazy 校验更强）

```python
# app/api/v1/sessions.py
async def create_session(
    payload: SessionCreate,  # agent_app_id: int 必填（议题 3）
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[SessionRead]:
    """创建入口：自动 associate（议题 3 决策 i，取代原「仅 lazy 校验」方案）。

    未 associate 用户直接 lazy 校验会静默跳过（G2 §4.3），导致 User 层缺失、
    skills 读空目录。自动 associate 复用幂等的 associate_user_with_app，
    保证 User 层就位（覆盖 G2 §12.2 的「POST /sessions 入口确保 User 层就位」诉求，
    且更强：首次调用即建立 association + 物化）。
    """
    # G2 集成：幂等 associate（published校验 + upsert + 物化 + 盖hash章）
    await agent_apps_service.associate_user_with_app(
        db, user_id=user.id, app_id=payload.agent_app_id, current_user_id=user.id
    )

    # 创建 session 记录（agent_app_id 必填 int，无 default app 解析）
    session_row = await sessions_service.create_session(
        db, user_id=user.id, username=user.username,
        agent_app_id=payload.agent_app_id, name=payload.name,
    )
    return ApiResponse.success(sessions_service.to_read(session_row))
```

> 注（复核补）：错误映射完整版见 §11.5——app 不存在→404（AgentAppNotFoundError），未发布→422（AgentAppNotPublishedError）；本节为 G2 集成点简化视图。

#### 12.2.2 `GET /sessions/{session_id}`（加载）：**不触发** lazy 校验（议题 3 决策）

```python
async def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[SessionRead]:
    """纯元数据读：不调 ensure_user_workspace_up_to_date（议题 3）。

    理由：该函数是纯文件操作，对读一行元数据无必要；export/续聊走
    get_runtime 时内部已无条件触发 lazy 校验，drift 自愈不会遗漏。
    """
    session_row = await sessions_service.get_session(session_id)
    if session_row is None or session_row.user_id != user.id:
        raise HTTPException(404, "session not found or not owned by user")
    message_count = await sessions_service.count_messages(session_row)
    return ApiResponse.success(sessions_service.to_read(session_row, message_count=message_count))
```

#### 12.2.3 `chatbot` 端点（若未来重启 chat spec）

若 G3 §1.2 的"chatbot 整体废弃"决策**未来被推翻**，chat 端点无需自行 lazy 校验——
`get_runtime` 内部已无条件执行（§12.1.2）：

```python
async def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
):
    # get_runtime 内部已 lazy 校验（无需重复调用）
    runtime = await get_runtime(db, payload.agent_app_id, user_id=user.id)
    ...
```

### 12.3 G3 session JSON 文件路径（议题 1 已定案：L2 JSONL）

按 G2 §1.1（v3.3）目录结构，session 上下文记录文件（L2 层，§4.1）：

```
{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.jsonl
```

**清理时机**（议题 4 落盘后更新）：
- 删除 session 时同步清理：L1 checkpoint（独立 helper）+ L2 JSONL 文件 + L0 PG 行（DELETE /sessions/{id}；顺序与容错见 §11.5.1）
- 删除 AgentApp 时：L2 随 `rmtree(agent_dir)` 级联消失（G2 已有逻辑）；**G3 新增**——L0 Session 行进 DB 事务级联删除 + L1 checkpoint 尽力清理（§11.5.2，全量级联决策）
- 取消 user 关联时清理该 app 下该 user 的目录（含 sessions/，G2 已有逻辑覆盖 L2；Session 行 / checkpoint 是否级联：本期**不处理**，记为后续 phase 待办——关联表无 session 外键，语义待定）

**主存储关系**（议题 1 定案）：L2 是产品级记录层，不是主存储；主存储 = PG 元数据（L0）+ AsyncPostgresSaver checkpoint（L1）。

### 12.4 G3 集成验证清单

G3 实施时必须验证：

- [ ] `POST /sessions` 入口调用 `associate_user_with_app`（自动 associate，取代旧「入口 lazy 校验」建议——议题 3，见 §12.2.1）
- [ ] `GET /sessions/{session_id}` 入口**不**调用 `ensure_user_workspace_up_to_date`（纯元数据读，见 §12.2.2；drift 自愈由 `get_runtime` 内部无条件 lazy 校验兜底）
- [ ] session JSONL 文件路径符合 `{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.jsonl`（复核改：原 .json 后缀为旧文案）
- [ ] 删除 session 时同步清理 JSONL 文件（L1→L2→L0 顺序，见 §11.5.1）
- [ ] 单元测试覆盖自动 associate 语义（`test_create_session_associates_user`：association 建立 + User 层物化 + 幂等，见 §11.9；原「mock `ensure_user_workspace_up_to_date`」条目作废）
- [ ] 集成测试覆盖 user 在不同 agent 下 session 隔离

### 12.5 不在 G3 集成范围

- G2 路径 helper（`_data_root` / `_agent_skill_dir` / `_user_skill_file` 等）由 G2 实现，G3 不直接调用
- G2 启动校验（`ensure_all_agent_workspaces`）由 G2 在 lifespan 触发，G3 不重复
- G2 的 `UserAgentAppAssociation` 表由 G2 管理，G3 只读
