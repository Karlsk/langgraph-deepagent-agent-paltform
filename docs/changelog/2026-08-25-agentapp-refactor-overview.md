# AgentApp 三层重构 总览（2026-08-25）

> **状态**：本期**仅落盘评估与 spec**，不实施任何代码。
> **日期**：2026-08-25
> **范围**：本次评估**不涉及 Workflow 引擎**（Workflow 部分后续单独设计）。
> **结论契合度**：认证简化 ✅ 100% 主流 / 三级 Workspace ⚠️ 比主流精细 / JSON Session 存储 ❌ 非主流。
> **建议**：严格分三阶段实施（Phase 1 认证解耦 → Phase 2 三级 Workspace → Phase 3 Session 选型）；Session 存储**保留 PostgreSQL 主存储**。

---

## 1. 文档导航

本文档为总览入口。详细 spec 按主题分文件：

| 文件 | 内容 |
|---|---|
| `docs/changelog/2026-08-25-agentapp-refactor-overview.md`（本文） | 背景、对标、阶段路线图、关键洞察 |
| `docs/changelog/agentapp-three-layer-refactor/overview.md` | 子目录总览（含 nav / Phase 3 描述 / Q5-Q7 决策状态） |
| `docs/changelog/agentapp-three-layer-refactor/spec-g1-auth.md` | **G1 认证体系简化** — API 契约、依赖、前端、双轨期、Refresh Token 设计 |
| `docs/changelog/agentapp-three-layer-refactor/spec-g2-workspace.md` | **G2 三级 Workspace 文件系统** — 目录结构、复制时机、alembic、fingerprint |
| `docs/changelog/agentapp-three-layer-refactor/spec-g3-session.md` | **G3 Session 存储选型 + 全 CRUD API** — 三方案对比、推荐方案 A、新 `/sessions` 端点 |
| `docs/changelog/agentapp-three-layer-refactor/files-risks.md` | 受影响文件清单（含改动量）+ 风险点 R1-R17 与监控 |
| `docs/changelog/agentapp-three-layer-refactor/open-questions.md` | 遗留问题 Q1-Q7（含已决策记录） |

---

## 2. 背景与目标

### 2.1 当前架构现状（截至 2026-08-25）

```text
auth 体系  ─ 两层：user token + session token（业务端点 Depends(get_current_session)）
workspace ─ 两层：{SKILLS_ROOT}/global/ + {SKILLS_ROOT}/users/<uid>/
session   ─ PG：Session 表（元数据）+ AsyncPostgresSaver（消息历史）
subagent  ─ RunTracer 收集 llm_call/tool_call/run_finished → PG SubAgentTestTrace
publish   ─ 双段校验（tool + provider/model）+ fingerprint 锁定
```

### 2.2 重构目标

| # | 目标 | 业界对标契合度 |
|---|---|---|
| G1 | **认证简化**：Token 层仅基础鉴权（user/password）；Session 层独立承载 chat 概念，在创建会话时由 API 显式传入 | LangGraph Platform / OpenAI Assistants / Bedrock Agents / CrewAI Enterprise 全部采纳 |
| G2 | **Workspace 三级文件系统**：Global（共享基础）/ Agent（Agent 专属）/ User（用户个性化）；publish + 关联用户时 Global+Agent→User 复制；Agent/SubAgent 测试读 Global+Agent，User Chat 读 User | 比主流更精细（主流多为两层）；与 LangGraph "图 + 配置" 分离思路一致 |
| G3 | **Session 存储选型**：评估是否引入 SQLite 替代 PG，或继续 PG；session 文件应放在 Agent 还是 User 目录 | 主流 100% 用 PG/SQLite；JSON 日志式存储**非主流** |
| G4 | **明确边界**：subagent/agent log = 单次运行过程；session 文件 = 大的会话历史（两者概念严格分离） | 内部概念澄清 |

---

## 3. 业界对标

| 平台 / 产品 | 认证模型 | Workspace 文件隔离 | 会话 / Run 日志存储 | 与本 spec 契合点 |
|---|---|---|---|---|
| **LangGraph Platform** | 单层 JWT (LangSmith token)；`thread_id` 是客户端提供的字符串，承载在 config 中而非 token 字段 | `assistant_id` + `thread_id` 双键；图编译产物按 assistant 隔离，运行时配置按 thread 隔离 | `thread_id` → PG `checkpoints` 表；LangSmith 持久化全链路 trace | ✅ 完全契合 G1 |
| **CrewAI Enterprise** | 单层 JWT；flow run 是显式创建的资源 | Crew 模板 + User workspace 双层 | Run 历史存 PG；trace 是分立事件流 | ✅ G1；⚠️ G2 仅两级 |
| **AutoGen Studio** | 单层 JWT；session 创建时显式指定 agent_config | 团队工作区单层 | 会话历史存 PG/SQLite；run log 是事件流 | ⚠️ G1 部分；❌ G2 无分层 |
| **OpenAI Assistants API v2** | 单层 Bearer（user/org-scoped API key） | 无文件隔离（依赖 vector store 隔离） | `thread` 由 API 显式创建；messages 存 PG | ✅ G1；❌ G2 无层级 |
| **AWS Bedrock Agents** | Cognito/IAM 单层 | Action Groups + Knowledge Base 双层 | Session attribute 在调用时传入；历史存 CloudWatch/S3 | ✅ G1；✅ G2 部分契合 |

**对标结论**：
1. **"Token 单层 + Session 显式创建" 是绝对主流** —— LangGraph / OpenAI / Bedrock / CrewAI 全部采用。G1 **完全符合业界规范**。
2. **三级 Workspace 比主流更精细**。LangGraph Platform 的"图 + 配置"分离与此接近；G2 可视为 LangGraph 思路的扩展 —— 但需评估是否过度设计（详见 `spec-g2-workspace.md`）。
3. **JSON 日志式 Session 存储 是非主流** —— 主流全部用 PG/SQLite 持久化消息历史。仅调试 / 可读场景才用 JSON（详见 `spec-g3-session.md`）。

---

## 4. 分阶段实施路线图（汇总）

```
Week 1: Phase 1 认证简化（双轨兼容，低风险）
Week 2-3: Phase 2 三级 Workspace（先迁移，后切流量，中风险）
Week 4-5: Phase 3 Session 存储评估（先观察，再决策，中风险）
Week 6+: Phase 4（可选）前端 ChatView 完整实现 + 工作流引擎接入
```

| Phase | 周数 | 目标 | 风险 | 详见 |
|---|---|---|---|---|
| **Phase 1** | 1 | 认证解耦：session 从 token 层下沉到 API 层；user token 成为唯一鉴权 | 低（双轨兼容期） | `spec-g1-auth.md` |
| **Phase 2** | 2 | 引入 Agent 层；publish + 关联用户时 Global+Agent→User 复制；测试读 Global+Agent，Chat 读 User | 中（DB schema + 目录结构 + 发布流程） | `spec-g2-workspace.md` |
| **Phase 3** | 2 | Session 存储评估与落地；**保留 PG 主存储** + 可选 JSON 视图层 | 中（仅在评估后认为必要才改主存储） | `spec-g3-session.md` |

### 4.1 Phase 1 关键 DoD（详见 `spec-g1-auth.md`）

- [ ] `POST /auth/session` 不再返回 `token` 字段；`SessionResponse` schema 移除 `token`
- [ ] 后端新增依赖 `get_current_user_with_session_id`：user token 取 user，`X-Session-Id` 头取 session；未传则自动创建（兼容路径）
- [ ] chatbot 端点改为 `Depends(get_current_user_with_session_id)`；`get_current_session` 保留 30-60 天双轨
- [ ] 前端 `authStorage.ts` 移除 `SESSION_TOKEN_KEY`；`useAuth.ts` 不再持久化 session token
- [ ] 前端 `request.ts` 拦截器改为注入 user token；session_id 通过 `X-Session-Id` 头传递
- [ ] 文档：`docs/authentication.md` 重写认证章节

### 4.2 Phase 2 关键 DoD（详见 `spec-g2-workspace.md`）

- [ ] alembic 迁移：`skill_asset.scope`、`agent_app.agent_dir`、`agent_app.workspace_hash`
- [ ] `skills_store.py` 新增 `_agent_skill_file(app_id, name)`、`materialize_for_agent(app_id, skill_names)`、`materialize_to_user_combined(user_id, app_id, skill_names)`
- [ ] `apps.py` publish 流程新增"复制到 Agent 层"步骤；`/apps/{id}/associate-user/{uid}` 端点做"Agent→User 复制"
- [ ] `runtime.py`/`assembly.py`：chatbot runtime 读 User 层；test_runner 读 Global + Agent 层
- [ ] `bootstrap.py` 新增 `ensure_default_agent_workspace` 创建 Agent 层骨架
- [ ] fingerprint 计算新增 `agent_skill_dir` 字段；Agent 层变更后 publish 自动重哈希
- [ ] 前端 `AgentList.vue`（已在 stub）扩展为支持"绑定到用户"操作
- [ ] 兼容策略：已发布 AgentApp 启动时自动补建 Agent 层目录
- [ ] 文档：`docs/agentapp-manual-testing.md` 新增第 6.6 节"三级 Workspace 同步"

### 4.3 Phase 3 关键 DoD（详见 `spec-g3-session.md`）

**推荐方案**（强烈建议）：
- [ ] **保留 PG `Session` 表与 AsyncPostgresSaver**（不改造主存储）
- [ ] 新增可选调试视图：`GET /chatbot/sessions/{session_id}/export` 返回 JSON 格式的完整消息历史
- [ ] 前端 ChatView 支持"导出当前会话为 JSON"功能
- [ ] 文档：`docs/observability.md` 新增"会话调试导出"章节

**备选方案**（仅在评估后认为必要才执行）：
- [ ] 调研 LangGraph 1.x `SqliteSaver` 兼容性
- [ ] 实现 SqliteSaver checkpointer 适配层（per-user 或 per-agent）
- [ ] 双写期：PG + SQLite 同步写入 1-2 个版本后切读 SQLite
- [ ] 数据回填脚本：从 PG checkpointer 历史导出到 SQLite

---

## 5. 关键洞察总结

1. **认证简化方向绝对主流** —— LangGraph / OpenAI / Bedrock / CrewAI 全部单层 token，session 显式 API。G1 **完全符合业界规范**。最大风险是兼容期与 token 有效期设计。
2. **三级 Workspace 是创新设计** —— 比主流多一层，但符合"资源分层管理"思路（与云原生的 namespace/project/pod 三层映射相似）。关键问题：Agent 层内容由谁维护、何时同步、如何保证 User Chat 看到最新快照 —— 必须严格设计 `published_hash` 锁定语义。
3. **Session 存储强烈建议保留 PG** —— LangGraph 的 checkpointer 机制天然依赖 SQL 持久化（HIL 中断恢复必需），改用 JSON/SQLite 需要实现自适配层，成本高、收益小。如有"可读 / 可调试"诉求，**用 JSON 视图层而非主存储**。
4. **subagent run trace ≠ session** —— 前者是单次执行的事件流（当前 `SubAgentTestTrace` 表），后者是 chat 历史。两者概念必须严格区分，避免 API 命名混淆。
5. **分阶段必须严格** —— Phase 1 是低风险改造，但 Phase 2 涉及 DB schema + 目录结构 + 发布流程变更，风险陡增。**建议 Phase 1 上线后观察至少 1 个版本**再启动 Phase 2。

---

## 6. 等待用户决策的开放问题

详见 `docs/changelog/agentapp-three-layer-refactor/open-questions.md`：

| # | 问题 | 选项 |
|---|---|---|
| Q1 | Phase 1 双轨兼容期长度 | 30 天 / 60 天 / 一个完整 release cycle |
| Q2 | user token 有效期策略 | 缩短到 7 天 + refresh token / 保持 30 天 / 保持现状 |
| Q3 | Agent 层 Skill 物理路径命名 | `<app_id>/skills/<name>`（推荐）/ `<app_name>/skills/<name>` |
| Q4 | Agent 层与 Global 冲突时优先级 | Agent 覆盖 Global（推荐）/ Global 永远优先 |
| Q5 | Session 存储是否真要改造 | 保留 PG（推荐）/ 引入 SQLite / 引入 JSON（不推荐） |
| Q6 | 启动哪个 Phase 的实现细节设计 | Phase 1 alembic 迁移骨架 / 后端 auth.py 重构 spec / 前端 useAuth.ts 重构 spec / Phase 2 / Phase 3 / 暂不启动 |

---

## 7. 不在本次评估范围

- **Workflow 引擎**（`app/workflow/`）：后续单独设计 spec
- **Phase 4+** 前端 ChatView 完整实现、SubAgent 测试前端补全、Agent 详情对话框等 UI 工作
- **MCP 工具目录重构**：与 G1/G2/G3 关联但不耦合，独立演进

---

## 8. 引用

- 评估输入：`docs/agentapp-manual-testing.md`（端到端测试基线）
- 当前实现基线：
  - 后端：`app/api/v1/auth.py`、`app/api/v1/chatbot.py`、`app/api/v1/apps.py`、`app/api/v1/subagents.py`
  - 核心服务：`app/services/agents/skills_store.py`、`app/services/agents/assembly.py`、`app/services/agents/runtime.py`、`app/services/agents/bootstrap.py`、`app/services/agents/run_tracer.py`、`app/services/agents/test_runner.py`
  - 数据模型：`app/models/agent_assets.py`、`app/models/session.py`、`app/models/subagent_trace.py`
  - Schema：`app/schemas/auth.py`
  - 迁移链：`alembic/versions/b25d38b0cd7c_initial_schema.py` → ... → `a9d4e2f7b315_subagent_test_trace.py`
- 前端：`agent-web/src/api/auth.ts`、`agent-web/src/utils/authStorage.ts`、`agent-web/src/composables/useAuth.ts`、`agent-web/src/views/auth/{Login,Register}.vue`
