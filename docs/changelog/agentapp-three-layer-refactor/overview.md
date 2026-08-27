# AgentApp 三层重构 总览（2026-08-26）

> **状态**：本期**仅落盘评估与 spec**，不实施任何代码。
> **日期**：2026-08-25（**修订**：2026-08-25 更新 G1 spec，纳入 Refresh Token；移除双轨兼容期；**2026-08-26** 完成 G2 spec v3 修订版 + Q3/Q4 决策闭环 + 三文档同步；**2026-08-26** G2 spec v3.2 修订版 + G3 接口签名同步；**2026-08-27** G3 spec 九议题逐项修订 + 复核定稿（工时 3 周；overview 五处同步）；**2026-08-27** G4 chat spec 九议题定稿（工时 3 周；overview 六处同步，工作流引擎接入从 Phase 4 拆出归独立 spec 体系））
> **范围**：本次评估**不涉及 Workflow 引擎**（Workflow 部分后续单独设计）。
> **结论契合度**：认证简化 ✅ 100% 主流 / 三级 Workspace ⚠️ 比主流精细（v3.2 修订版细化）/ JSON Session 存储 ❌ 非主流。
> **建议**：严格分三阶段实施（Phase 1 认证简化 + Refresh Token → Phase 2 三级 Workspace v3.2 修订版 → Phase 3 Session 选型）；Session 存储**保留 PostgreSQL 主存储**。

---

## 1. 文档导航

本文档为总览入口。详细 spec 按主题分文件（与本文档同目录）：

| 文件 | 内容 |
|---|---|
| `overview.md`（本文） | 背景、对标、阶段路线图、关键洞察 |
| `spec-g1-auth.md` | **G1 认证体系简化** — API 契约、依赖、前端、双层 storage、Refresh Token 详细设计 |
| `spec-g2-workspace.md` | **G2 三级 Workspace 文件系统（v3.2 修订版）** — 嵌套 User 层 + lazy 校验 + 服务层拆分；目录结构、复制时机、alembic、fingerprint、运行时集成、DoD、验证、迁移；user_id 显式传入 + 缓存大小限制 + Phase 分组 DoD |
| `spec-g3-session.md` | **G3 Session 元数据与上下文架构（九议题定稿）** — L0-L3 分层（L2 JSONL 记录层 + 压缩接入）；主存储 PG 定案不动；全新 `/sessions` RESTful 6 端点（list/get/create/patch/delete/export）+ 三层级联删除；SubAgentTestTrace 更名 `subagent_trace` + 遗留清理 + 前端会话列表页 |
| `spec-g4-chat.md` | **G4 Chat 交互层（九议题定稿）** — X-Session-Id 寻址 chat 端点族（POST /chat 非流式 auto-approve + POST /chat/stream SSE type 多事件 + GET /messages L2 投影 + POST /rebuild 灾难重建）；HIL 交互闭环（interrupt 投影 / decisions JSON 复用消息通道 / pending 恢复）；RunTracer 挂 chat 链（subagent_trace 加 source+session_id + GET /chat/traces）；会话自动起名恢复（截断+LLM 覆盖两级）；前端聊天页（/chat/:id 两路由 + fetch-based SSE 客户端 + 消息流 P0 + 轨迹抽屉） |
| `files-risks.md` | 受影响文件清单（含改动量 · v3 修订版）+ 风险点 R1-R18（含 v3 新增 R18 嵌套 User 隔离）与缓解 |
| `open-questions.md` | 遗留问题 Q1-Q7（**Q1/Q2/Q3/Q4/Q5/Q7 已决策**，仅 Q6 待决：实现启动顺序） |
| `spec-g2-review.md`（**新增**） | G2 spec v3 修订版审查记录 + 62 项关键决策（2026-08-26 落盘） |

---

## 2. 背景与目标

### 2.1 当前架构现状（截至 2026-08-25）

```text
auth 体系  ─ 两层：user token + session token（业务端点 Depends(get_current_session)）  [Phase 1 改造后：单层 user token + Refresh Token]
workspace ─ 两层：{SKILLS_ROOT}/global/ + {SKILLS_ROOT}/users/<uid>/  [Phase 2 改造后：三级 · {DATA_ROOT}/global/skills/ + {DATA_ROOT}/agents/<app_id>/skills/ + {DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/]
session   ─ PG：Session 表（元数据）+ AsyncPostgresSaver（消息历史）
subagent  ─ RunTracer 收集 llm_call/tool_call/run_finished → PG SubAgentTestTrace
publish   ─ 双段校验（tool + provider/model）+ fingerprint 锁定  [Phase 2 改造后：fingerprint 不纳入 workspace_hash，启动期补建 Agent 层骨架 + lazy 校验兜底]
```

### 2.2 重构目标

| # | 目标 | 业界对标契合度 |
|---|---|---|
| G1 | **认证简化**：Token 层仅基础鉴权（user/password）；Session 层独立承载 chat 概念，在创建会话时由 API 显式传入 | LangGraph Platform / OpenAI Assistants / Bedrock Agents / CrewAI Enterprise 全部采纳 |
| G2 | **Workspace 三级文件系统（v3.2 修订版）**：Global（共享基础）/ Agent（Agent 专属）/ User（用户个性化，**嵌套在 AgentApp 下**）；publish 时 Global→Agent 复制；关联用户时 (Global + Agent) → User 复制；**启动期补建 Agent 层骨架** + **lazy 校验**兜底；Agent/SubAgent 测试读 Global+Agent，User Chat 读 User；fingerprint **不纳入** `workspace_hash`；API 层仅参数校验、业务在 `agent_apps_service.py` / `agents_service.py` | 比主流更精细（主流多为两层）；与 LangGraph "图 + 配置" 分离思路一致；**v3.2 修订版明确"嵌套 User 层"避免跨 app 冲突**（详见 `spec-g2-workspace.md` §2.1） |
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
Week 1-1.5: Phase 1 认证简化 + Refresh Token（直接替换，新项目无存量客户端；1.5 周含 Refresh Token 设计）
Week 2-3.5: Phase 2 三级 Workspace v3 修订版（嵌套 User 层 + 服务层拆分 + 迁移脚本，2.5 周；中风险）
Week 4-6.5: Phase 3 Session 元数据与上下文架构（3 周定稿，中风险；主存储 PG 不动）
Week 7-9.5: Phase 4 Chat 交互层（3 周定稿，中风险；SSE 首例 / HIL 闭环 / rebuild / 前端聊天页）
Week 10+:   工作流引擎接入（独立 spec 体系，见 docs/workflow-reimpl-plan/；G4 议题 1c 排除决策）
```

| Phase | 周数 | 目标 | 风险 | 详见 |
|---|---|---|---|---|
| **Phase 1** | 1.5 | 认证解耦 + Refresh Token：session 从 token 层下沉到 API 层；user token 成为唯一鉴权（7 天）；refresh token 30 天 + 旋转 + 重放检测 | 低（直接替换，无双轨兼容期） | `spec-g1-auth.md` |
| **Phase 2** | 2.5 | 引入 Agent 层；publish + 关联用户时 Global+Agent→User 复制；**嵌套 User 层**（`agents/<app_id>/users/<user_id>/`）；**启动期补建 Agent 层骨架**（`ensure_all_agent_workspaces`）+ **lazy 校验**兜底（G3 议题 3 定案：`POST /sessions` 入口改自动 associate，`get_runtime` 内部无条件 lazy 校验，见 spec-g3 §12.2）；测试读 Global+Agent（`materialize_into_combined_directory`），Chat 读 User；服务层拆分（`agent_apps_service.py` / `agents_service.py`）；fingerprint **不纳入** `workspace_hash`；**user_id 显式传入** + **Runtime cache 大小限制** | 中（DB schema + 嵌套目录 + 发布流程 + Runtime cache key 三元组 `(app_id, user_id, fingerprint)`） | `spec-g2-workspace.md`（v3.2 修订版）/ `spec-g2-review.md`（决策记录） |
| **Phase 3** | 3 | **Session 元数据与上下文架构 + 新 `/sessions` 全 CRUD API**：主存储定案不动（L0 PG 元数据 + L1 checkpoint）；新增 **L2 JSONL 记录层**（runtime 钩子写入 + export + L1 fallback 自愈）；**压缩接入**（`SummarizationMiddleware`，阈值 `AgentApp.context_size`）；RESTful 6 端点（list/get/create/patch/delete/export）取代已删除的 `/auth/session`；DELETE 三层级联 + AgentApp 硬删全量级联；SubAgentTestTrace 更名 `subagent_trace` + 遗留清理；chatbot 端点整体废弃 | 中（主存储不动；钩子 fire-and-forget / 级联尽力清理 / 迁移均有 downgrade） | `spec-g3-session.md`（2026-08-27 九议题定稿） |
| **Phase 4** | 3 | **Chat 交互层**：`X-Session-Id` 寻址 3+1 端点（POST /chat 非流式 interrupt **auto-approve**（上限 10 可配，流式保持人在环）+ POST /chat/stream **SSE type 多事件协议**（message/interrupt/summary/error/done + 15s 心跳）+ GET /messages L2 行投影 + POST /rebuild 灾难重建）；HIL 交互闭环（interrupt 稳定投影 schema / decisions JSON 复用消息通道 / 刷新 pending 恢复）；RunTracer 挂 chat 链（subagent_trace 加 source+session_id + GET /chat/traces）；会话自动起名恢复（截断+LLM 覆盖两级，G3 议题 6 删而复得）；前端聊天页（/chat/:id 两路由 + 自研 fetch-based SSE 客户端 + 消息流 P0 全量 + 轨迹抽屉；Markdown 纯文本先行）。**工作流引擎接入排除**（独立 spec 体系，归 `docs/workflow-reimpl-plan/` 或后续 G5） | 中（SSE 首例 / auto-approve 反转安全默认 / rebuild spike；利好：runtime 零改动 / 回滚干净 / 前后端两路由独立） | `spec-g4-chat.md`（2026-08-27 九议题定稿） |

---

## 5. 关键洞察总结

1. **认证简化方向绝对主流** —— LangGraph / OpenAI / Bedrock / CrewAI 全部单层 token，session 显式 API。G1 **完全符合业界规范**。Refresh Token 机制（7 天 access + 30 天 refresh + 旋转 + 重放检测）纳入 Phase 1，与业界安全最佳实践一致。
2. **三级 Workspace 是创新设计** —— 比主流多一层，但符合"资源分层管理"思路（与云原生的 namespace/project/pod 三层映射相似）。v3.2 修订版关键设计：
   - **嵌套 User 层**（`{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/`）—— 避免不同 AgentApp 下的同名 skill 互相污染（v2 平铺设计的重大隐患）
   - **启动期补建 Agent 层骨架**（`ensure_all_agent_workspaces`）—— 存量系统迁移时无需全量重写；新装系统首次启动时创建所有 Agent 骨架
   - **User 层就位 + lazy 校验兜底**（G3 议题 3 定案后语义：`POST /sessions` 入口自动 associate（`associate_user_with_app`，幂等建立 association + 物化，强于 lazy 校验）；`GET /sessions/{sid}` 不触发；drift 自愈由 `get_runtime` 内部无条件调用 `ensure_user_workspace_up_to_date` 兜底）—— 2026-08-27 复核同步：原「POST 入口调 lazy 校验」为 G2 审查期旧建议
   - **fingerprint 不纳入 `workspace_hash`** —— 避免运行时缓存被 skill 文件微调 频繁 invalid；DB schema 字段(`workspace_hash`)仅作 G3 lazy 校验辅助
   - **服务层拆分**（`agent_apps_service.py` / `agents_service.py`）—— API 层仅参数校验，业务代码集中在 service 层，遵循项目代码规范
   - **Runtime cache key 三元组** `(app_id, user_id, fingerprint)` —— 避免用户间复用错伍 runtime（v2 二元组的重大隐患）
3. **Session 存储强烈建议保留 PG** —— LangGraph 的 checkpointer 机制天然依赖 SQL 持久化（HIL 中断恢复必需），改用 JSON/SQLite 需要实现自适配层，成本高、收益小。如有"可读 / 可调试"诉求，**用 JSON 视图层而非主存储**。
4. **subagent run trace ≠ session** —— 前者是单次执行的事件流（当前 `SubAgentTestTrace` 表），后者是 chat 历史。两者概念必须严格区分，避免 API 命名混淆。
5. **分阶段必须严格** —— Phase 1 是低风险改造，但 Phase 2 涉及 DB schema + 目录结构 + 发布流程变更，风险陡增。**建议 Phase 1 上线后观察至少 1 个版本**再启动 Phase 2。

---

## 6. 等待用户决策的开放问题

详见 `open-questions.md`（**注**：Q1/Q2/Q3/Q4/Q5/Q7 已决策，仅 Q6 待决；详见 §6.1 Q3/Q4 决策摘要）：

| # | 问题 | 状态 / 选项 |
|---|---|---|
| Q1 | Phase 1 双轨兼容期长度 | ✅ 已决策（Phase 1 落地）：无双轨兼容期（新项目，直接替换）；保留 `/auth/session` 注释路由（不删除） |
| Q2 | user token 有效期策略 | ✅ 已决策（Phase 1 落地）：access_token 7 天 + refresh_token 30 天；旋转 + 重放检测（详见 `spec-g1-auth.md` §10） |
| Q3 | Agent 层 Skill 物理路径命名 | ✅ **已决策（v3 评审，2026-08-26）**：采纳 `<app_id>`（主键稳定；跨环境 name 可能冲突，id 不会；详见 §6.1） |
| Q4 | Agent 层与 Global 冲突时优先级 | ✅ **已决策（v3 评审，2026-08-26）**：采纳 "Agent 覆盖 Global"（快照语义清晰；详见 §6.1） |
| Q5 | Session 存储是否真要改造 | ✅ 已决策（Phase 3 落地）：保留 PG（推荐） |
| Q6 | 启动哪个 Phase 的实现细节设计 | ☐ 待决（Phase 1/2/3/暂不启动） |
| Q7 | 新会话 CRUD API 设计 | ✅ 已决策（Phase 3 落地）：URL 改为 RESTful `/sessions`；鉴权统一 `Depends(get_current_user)` + path 参数归属校验（404 防枚举）；`X-Session-Id` header 为 G1 预留给未来 chat 端点（G4）的机制，不用于 CRUD（2026-08-27 复核修正原误述）；chatbot 端点整体废弃（详见 `spec-g3-session.md` §11） |

---

### 6.1 Q3 / Q4 决策摘要（v3 评审通过 · 2026-08-26）

详见 `spec-g2-review.md`（详绁决策过程）与 `open-questions.md` §7.1（决策记录）。

#### Q3：Agent 层 Skill 物理路径命名

- **采纳选项**：**`<app_id>`**（`{DATA_ROOT}/agents/<app_id>/skills/<name>/SKILL.md`）
- **否决选项**：`<app_name>`（跨环境不稳定）/ `<app_id>_<app_name>`（冗余）
- **核心理由**：
  - 主键稳定性优先：`id` 永不变化，`name` 未来若放开修改会变
  - 跨环境安全：`name` 可能跨 dev/staging/prod 重名，`id` 不会
  - v3 嵌套 User 层后两层命名一致（Agent 层 `<app_id>` + User 层 `<user_id>` 都是 PK）
- **影响**：spec-g2-workspace.md §2.1 + §3.3（alembic `agent_dir` 字段） + files-risks.md §2.2 + §4.1
- **关联决策**：**不引入 `AGENTS_ROOT` 配置项**（MVP 简化，`{DATA_ROOT}/agents/` 作为 agent 根；后期如需独立挂载点可后续调整）

#### Q4：Agent 层与 Global 冲突时优先级

- **采纳选项**：**Agent 覆盖 Global**
- **否决选项**：Global 永远优先（Agent 不可覆盖，违反"私有化定制"语义）/ 按引用顺序（逻辑不明确）
- **核心理由**：
  - **快照语义清晰**：Agent 层是 publish 时的快照，本质是 Agent 拥有者对 Global 的"私有化定制"
  - **符合用户预期**：用户期望"Agent 专属 skill 能覆盖默认 Global skill"
  - **实现位置**：`materialize_to_user_combined` 中 `agent_path if agent_path.exists() else _global_skill_file(name)`
- **影响**：spec-g2-workspace.md §4.3（复制逻辑） + spec-g2-review.md §3.4.3
- **关联决策**：**否决 `_read_agent_dir_skill_names`**（v2 原设计项；不再需要 Agent 层 skill 名查询函数）

---

## 7. 不在本次评估范围

- **Workflow 引擎**（`app/workflow/`）：后续单独设计 spec（G4 议题 1c 排除决策：不接入 chat 服务层，独立契约体系）
- **Phase 4+** 其余 UI 工作：SubAgent 测试前端补全、Agent 详情对话框等；G4 落地后的待办（edit/respond 审批 UI / Markdown 渲染 / 复合流实时 summary，见 spec-g4 §11.1）
- **MCP 工具目录重构**：与 G1/G2/G3 关联但不耦合，独立演进

---

## 8. 引用

- 评估输入：`docs/agentapp-manual-testing.md`（端到端测试基线）
- 当前实现基线：
  - 后端：`app/api/v1/auth.py`、`app/api/v1/chatbot.py`、`app/api/v1/apps.py`、`app/api/v1/subagents.py`
  - 核心服务：`app/services/agents/skills_store.py`、`app/services/agents/assembly.py`、`app/services/agents/runtime.py`、`app/services/agents/bootstrap.py`、`app/services/agents/run_tracer.py`、`app/services/agents/test_runner.py`
  - **v3 新增**：`app/services/agents/agent_apps_service.py`、`app/services/agents/agents_service.py`（API 层拆出业务逻辑）；`scripts/migrate_workspace.py`（存量数据迁移）
  - 数据模型：`app/models/agent_assets.py`、`app/models/session.py`、`app/models/subagent_trace.py`
  - **v3 调整**：`app/models/agent_assets.py` 增加 `scope` / `workspace_hash` / `agent_dir` 字段；**新增** `user_agent_app_association` 表
  - Schema：`app/schemas/auth.py`
  - 迁移链：`alembic/versions/b25d38b0cd7c_initial_schema.py` → ... → `a9d4e2f7b315_subagent_test_trace.py`
  - **v3 新增迁移**：`alembic/versions/<rev>_agent_workspace.py`（SkillAsset.scope + agent_workspace_status + agent_dir + user_agent_app_association 表）
- 前端：`agent-web/src/api/auth.ts`、`agent-web/src/utils/authStorage.ts`、`agent-web/src/composables/useAuth.ts`、`agent-web/src/views/auth/{Login,Register}.vue`
- **G2 spec 体系闭环**（2026-08-26）：
  - `spec-g2-workspace.md`（v3 修订版 · 1427 行）
  - `files-risks.md`（v3 同步 · 388 行）
  - `open-questions.md`（Q3/Q4 已决策 · 323 行）
  - `spec-g2-review.md`（v3 审查记录 · 2967 行，62 项关键决策）
