# 受影响文件清单 + 风险点

> **关联文档**：`overview.md`、`spec-g1-auth.md`、`spec-g2-workspace.md`、`spec-g3-session.md`
> **目标读者**：实施者（用于排期）+ 代码审查者（用于评估改动量）

---

## 1. 改动量图例

| 标记 | 含义 | 估算 |
|---|---|---|
| **无** | 无需变更 | 0 |
| **小** | < 50 行变更或纯配置 | < 0.5 天 |
| **中** | 50-300 行变更或新增独立函数 | 0.5-2 天 |
| **大** | > 300 行变更或新增模块 / alembic 迁移 | > 2 天 |

---

## 2. 后端文件清单

### 2.1 Phase 1：认证简化 + Refresh Token

| 文件 | 当前职责 | 变更 | 改动量 |
|---|---|---|---|
| `app/api/v1/auth.py` | `register`/`login`/`session`/`get_current_user`/`get_current_session`/`update_session_name`/`delete_session`/`get_user_sessions` | 重构：`session` 端点注释（不删，handler noop）；新增 `POST /auth/refresh` + `POST /auth/logout`；**删除** `get_current_session` 与 `create_compat_session_token` | **大** |
| `app/schemas/auth.py` | `Token`/`TokenResponse`/`UserCreate`/`UserResponse`/`SessionCreate`/`SessionResponse` | 新增 `LoginResponse`/`RefreshTokenRequest`/`LogoutRequest`；`SessionResponse`/`SessionCreate` 注释废弃（Phase 3 重做） | 中 |
| `app/models/session.py` | 39 行 Session 表 | 不变（核心字段已具备） | 无 |
| `app/services/database.py` | `create_session`/`get_session`/`update_session_name`/`delete_session`/`get_user_sessions` | 不变 | 无 |
| `app/utils/auth.py` | `create_access_token`/`verify_token` | `create_access_token` 仅服务 user（7 天）；新增 `create_refresh_token`/`hash_refresh_token`（30 天 + sha256 哈希） | 中 |
| `app/models/refresh_token.py`（**新增**） | — | `RefreshToken` 模型（id/user_id/token_hash/expires_at/revoked/created_at/last_used_at） | 大 |
| `app/services/refresh_token_store.py`（**新增**） | — | token 哈希存储 / 旋转 / 撤销 / 全 user 撤销 / 重放检测 | 大 |
| `app/api/v1/chatbot.py` | `chat`/`chat_stream`/`get_session_messages`/`clear_chat_history` | **不在本 Phase 范围**；chatbot 端点与业务代码保留现状；Phase 3 重做（X-Session-Id header + 新会话 CRUD） | 无 |
| `app/api/v1/subagents.py` | subagents CRUD + `test` + `test-traces` | 鉴权从 `get_current_session` 改为 `Depends(get_current_user)`（直接替换，无双轨） | 小 |
| `app/api/v1/skills.py` | Skill CRUD | 同上 | 小 |
| `app/api/v1/api.py`（AgentApp CRUD） | AgentApp CRUD + publish | 鉴权适配（不依赖 session） | 小 |
| `app/api/v1/mcp_servers.py` | MCP CRUD | 鉴权适配 | 小 |
| `app/api/v1/providers.py` | Provider/ModelConfig CRUD | 鉴权适配 | 小 |
| `app/api/v1/tools.py` | Tools 列表与详情 | 鉴权适配 | 小 |

### 2.2 Phase 2：三级 Workspace（v3.2 修订版）

> 详细 spec 见 [`spec-g2-workspace.md`](./spec-g2-workspace.md)；审查记录见 [`spec-g2-review.md`](./spec-g2-review.md)。本节仅列文件清单与改动量。

| 文件 | 当前职责 | v3.2 变更 | 改动量 |
|---|---|---|---|
| `app/services/agents/skills_store.py` | 611 行双存实现（DB+disk） | **扩展**：5 路径 helpers（`_agent_dir`/`_agent_skill_dir`/`_agent_skill_file`/`_user_skill_dir`/`_user_skill_file`）+ 5 函数（`materialize_for_agent`/`materialize_to_user_combined` v3.2 新签名 `(app_cfg, user_id, subagent_cfgs)`/`materialize_into_combined_directory`/`ensure_user_workspace_up_to_date`/`_hash_compare_or_write`）+ 2 workspace_hash 计算（`compute_workspace_hash`/`_compute_user_workspace_hash`）+ 1 prune（`_prune_stale_user_skills`）；**删除**：`_read_agent_dir_skill_names`（v3 否决） | **大**（> 400 行新增） |
| `app/services/agents/agent_apps_service.py`（**新建**） | — | 6 个编排函数：`publish_agent_app`/`associate_user_with_app`/`disassociate_user_from_app`/`patch_agent_app`（解读 B）/`delete_agent_app`（连锁清理）/`ensure_user_workspace_up_to_date` | **大** |
| `app/services/agents/agents_service.py`（**新建**） | — | 2 个函数：`list_subagent_cfgs`/`validate_subagent_skill_visibility` | 中 |
| `app/services/db_service.py` | 既有 user/session CRUD | **扩展**：3 个 association 函数（`_get_or_create_association`/`_get_association`/`_invalidate_user_layer_cache`） | 小 |
| `app/services/agents/bootstrap.py` | `ensure_default_agent_app` + `_backfill_legacy_sessions` | **重命名 + 扩展**：`ensure_default_agent_workspace` → `ensure_all_agent_workspaces`（覆盖所有 App，含 published active 校验）；单 App 异常 try/except 隔离；legacy backfill 保留 | 中 |
| `app/services/agents/runtime.py` | `AgentAppRuntime`/`get_runtime`/`DeepAgentsAppRuntime`/`WorkflowAppRuntime` | **删除** `_COMPILE_USER_ID = "system"`；**扩展** `_runtime_cache` key 为三元组 `(app_id, user_id, fingerprint)`；**扩展** `get_runtime` 接受 `user_id` 参数 + 调用 `ensure_user_workspace_up_to_date`；cache 淘汰按 `(app_id, user_id)` 维度 | 中 |
| `app/services/agents/assembly.py` | `compile_agent_app`/`compile_standalone_subagent`/`compute_fingerprint` | **FilesystemBackend 路径**：nesting user 层（`{DATA_ROOT}/agents/<app_id>/users/<user_id>/`）；**签名扩展** `compile_agent_app`/`get_or_compile` 接受 `user_id` 透传；**否决** `compute_fingerprint` 纳入 `workspace_hash`（维持 5 输入字段） | 中 |
| `app/services/agents/test_runner.py` | `run_subagent_once` | **MVP 限制**：standalone runner 维持 Global-only 调用 `materialize_into_directory`；**新增** `materialize_into_combined_directory` 函数（combined 调用后续 G3+ 启用）；**新增** docstring MVP 说明 | 小 |
| `app/api/v1/apps.py` | AgentApp CRUD + publish | **API 层仅参数校验**：publish 调用 `agent_apps_service.publish_agent_app`；新增 `POST /apps/{id}/associate-user/{uid}` 端点（参数校验 + service 调用）；PATCH 端点走 `patch_agent_app`（解读 B）；delete 端点走 `delete_agent_app`（连锁清理 Agent 层 + User 层 + CASCADE 关联表） | 中 |
| `app/api/v1/chatbot.py` | `chat`/`chat_stream`/`get_session_messages`/`clear_chat_history` | **v3 必改**：调用 `get_runtime(..., user_id=current_user.id)`；其余业务逻辑 Phase 3 重做（X-Session-Id header + 新会话 CRUD） | 小 |
| `app/main.py` lifespan | startup/shutdown 编排 | **新增**：`ensure_all_agent_workspaces(session)` 调用（启动期补建所有 App 的 Agent 层） | 小 |
| `app/models/agent_assets.py` | `SkillAsset`/`SubAgentConfig`/`AgentApp`/`McpServerConfig` | **扩展**：`SkillAsset.scope`（默认 `global`）；`AgentApp.agent_dir`/`workspace_hash`/`agent_workspace_status`；**新增表** `UserAgentAppAssociation`（含 `last_synced_workspace_hash` + CASCADE） | 中 |
| `app/core/config.py` | Pydantic Settings | **新增** `DATA_ROOT`（默认 `./data`）；**保留** `SKILLS_ROOT`（DEPRECATED 注释）；**新增** 兼容检测 `_check_legacy_skills_root`；**否决** `AGENTS_ROOT` 配置项（MVP 简化，路径直接拼接） | 小 |
| `scripts/migrate_workspace.py`（**新建**） | — | 一次性迁移脚本：`--dry-run` 默认开启；备份 `archive/` 子目录；迁移 Global + User 到新路径；数据回填（agent_dir/workspace_hash/agent_workspace_status） | 中 |
| `alembic/versions/<rev>_agent_workspace.py`（**新建**） | — | 1 个迁移：`add_agent_workspace_fields`（4 字段）+ `add_user_agent_app_association`（1 表） | 中 |

**v3 关键否决项**（详见 `spec-g2-review.md` §6.2）：

| 否决项 | spec 原始提议 | v3 否决理由 |
|---|---|---|
| `_read_agent_dir_skill_names` | 实现 | 与 `app_cfg.skill_names` 冗余 |
| `compute_fingerprint` 纳入 `workspace_hash` | 纳入 | 与 `_load_skill_hashes` 冗余 |
| `_validate_user_workspace` 启动期校验 | 实现 | 与 lazy 校验 + 启动期校验职责重叠 |
| `AGENTS_ROOT` 配置项 | 新增 | MVP 简化，路径直接拼接 |

### 2.3 Phase 3：Session 存储（推荐方案 A）+ 全 CRUD API

| 文件 | 当前职责 | 变更 | 改动量 |
|---|---|---|---|
| `app/api/v1/sessions.py`（**新增，从 chatbot.py 拆出**） | — | 5 个 CRUD 端点（list/get/create/patch/delete）+ `GET /sessions/{session_id}/export`（JSON 视图层）；鉴权统一 `Depends(get_current_user)` + 函数内 `X-Session-Id` 校验 | 大 |
| `app/schemas/session.py`（**新增**） | — | `SessionRead` / `SessionCreate` / `SessionUpdate` / `SessionListResponse` 4 个 Pydantic schema | 中 |
| `app/services/database.py` | `create_session`/`get_session`/`update_session_name`/`delete_session`/`get_user_sessions` | 新增 6 个 session CRUD 方法：`list_user_sessions`/`count_user_sessions`/`get_session`/`create_session`/`update_session_name`/`delete_session` | 中 |
| `app/services/agents/checkpointer_store.py` | LangGraph AsyncPostgresSaver 封装 | 新增 `delete_thread(thread_id)` 方法（DELETE 端点级联清理 checkpoint） | 小 |
| `app/api/v1/chatbot.py` | chat/messages 端点 | 注释全部端点 + 文件保留（chatbot 整体废弃）；Session CRUD 端点全部迁出至 `sessions.py` | 中（注释） |
| `app/services/agents/runtime.py` | `AgentAppRuntime` | `export_session_history` 方法（从 checkpointer 读完整消息流） | 中 |
| `app/models/subagent_trace.py` | SubAgentTestTrace | **不变**（澄清：属于 run 而非 session） | 无 |
| `app/services/session_naming.py` | 自动会话命名 | 不变 | 无 |
| `app/core/observability.py` | Langfuse 集成 | 不变 | 无 |

> **决策**：chatbot 端点（`/chat/chat_stream/get_session_messages/clear_chat_history`）整体**直接废弃**（不在 Phase 3 范围）；如未来重启，需要单独的 chatbot spec。Session CRUD 由 `sessions.py` 提供。

### 2.4 Phase 3 备选方案 B（仅在决策后启动）

| 文件 | 变更 | 改动量 |
|---|---|---|
| `app/services/agents/checkpointer.py`（新增） | SqliteSaver 适配层 | **大** |
| `app/services/agents/bootstrap.py` | 双写配置开关 | 中 |
| `alembic/versions/` 新增 | （可选）双写期标记字段 | 小 |
| `scripts/migrate_checkpointer_pg_to_sqlite.py`（新增） | 数据回填脚本 | 中 |
| `scripts/backup_sqlite_checkpoints.sh`（新增） | 备份策略 | 小 |

---

## 3. 前端文件清单

### 3.1 Phase 1：认证简化 + Refresh Token

| 文件 | 当前职责 | 变更 | 改动量 |
|---|---|---|---|
| `agent-web/src/api/auth.ts` | `login`/`createSession`/`register` | `createSession` 返回值改为占位（`data=null`）；新增 `refreshToken()` / `logout()` API 包装 | 中 |
| `agent-web/src/utils/authStorage.ts` | `auth.sessionToken` + `auth.user` 双 key | 移除 `SESSION_TOKEN_KEY`；**新增** `USER_TOKEN_KEY`（access_token）；**保留** `USER_KEY`；双 key storage | 中 |
| `agent-web/src/composables/useAuth.ts` | login 流程串联 + logout + bootstrap | `login` 简化为写 user + userToken；**新增** `refreshUserToken()`；**删除** `exchangeSession`/`currentSessionId` | 中 |
| `agent-web/src/utils/request.ts` | axios 拦截器 | 改为注入 user token（无 X-Session-Id）；**新增 refresh 拦截器**（401 → 自动 refresh + 重发原请求 + `_retried` 防递归） | 中 |
| `agent-web/src/views/auth/Login.vue` | 登录表单 | 不变 | 无 |
| `agent-web/src/views/auth/Register.vue` | 注册表单 | 不变 | 无 |
| `agent-web/tests/auth.spec.ts` | 登录态单测 | 更新断言（userToken 持久化；无 sessionToken）；新增 refresh 拦截器用例 + 重放检测 fallback | 小 |

### 3.2 Phase 2：三级 Workspace（v3 修订版 · MVP 暂缓）

> **v3 决策**：前端绑定用户 UI MVP **暂缓**（frontend stub 状态，G2 范围外）。后续由 G3 Session CRUD 阶段统一处理（详见 `spec-g3-session.md` §11）。本节仅保留后端 API 占位说明。

| 文件 | v3 状态 | 变更 | 改动量 |
|---|---|---|---|
| `agent-web/src/views/agent/AgentList.vue` | **MVP 暂缓** | 不在 v3 范围；后续 G3 决定 | — |
| `agent-web/src/api/apps.ts` | **MVP 暂缓** | 不在 v3 范围（`associateUserWithApp` 后续按需启用） | — |
| `agent-web/tests/api/apps.spec.ts` | **MVP 暂缓** | 不在 v3 范围 | — |

> **后续路径**（G3 启动时设计）：
>
> - 在 `AgentList.vue` 详情对话框添加「绑定用户」按钮 + `WebAgentConfirmDialog`
> - 调用 `POST /api/v1/apps/{id}/associate-user/{uid}` 后端 API
> - 需后端补充 `GET /api/v1/apps/{id}/associations`（查询已绑定用户列表）才能在 UI 展示状态

### 3.3 Phase 3：Session 存储（推荐方案 A）+ 全 CRUD API

| 文件 | 变更 | 改动量 |
|---|---|---|
| `agent-web/src/api/sessions.ts`（**新增，从 chatbot.ts 迁出**） | 5 个 CRUD API：`listSessions` / `getSession` / `createSession` / `updateSession` / `deleteSession`；保留 `exportSessionHistory(sessionId, format?)`（从 chatbot.ts 迁入） | 中 |
| `agent-web/src/api/chatbot.ts` | 注释所有现有 chatbot API 函数（chatbot 整体废弃）；文件保留备查 | 中（注释） |
| `agent-web/src/views/chat/ChatView.vue`（stub） | 集成"导出 JSON"按钮 + 新 CRUD UI（list / create / rename / delete） | 中 |
| `agent-web/tests/api/sessions.spec.ts`（**新增**） | 5 个端点单测 + 越权 404 场景 + export 格式 | 中 |

---

## 4. 数据库迁移清单

### 4.0 Phase 1 新增迁移（`alembic/versions/<rev>_refresh_token.py`）

```python
def upgrade():
    op.create_table(
        "refresh_token",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_refresh_token_user_id", "refresh_token", ["user_id"])
    op.create_index("ix_refresh_token_expires_at", "refresh_token", ["expires_at"])
    op.create_unique_constraint("uq_refresh_token_token_hash", "refresh_token", ["token_hash"])


def downgrade():
    op.drop_table("refresh_token")
```

> 注：本 Phase 的 refresh_token 哈希字段（sha256 hex = 64 字符）与未来可能的扩展字段预留。

### 4.1 Phase 2 新增迁移（`alembic/versions/<rev>_agent_workspace.py` · v3 修订版）

> v3 修订版增加 `user_agent_app_association` 表 + agent_dir 路径调整（`SKILLS_ROOT/agents/<id>/skills` → `DATA_ROOT/agents/<id>`，不再仅存 skills 子目录）。`compute_fingerprint` **不**依赖 `workspace_hash`，所以迁移后 `agent_workspace_status='pending'` 启动期补建不会触发大量 fingerprint cache miss。

```python
# alembic/versions/<rev>_agent_workspace.py
def upgrade():
    # ====== 字段新增 ======

    # SkillAsset.scope
    op.add_column(
        "skill_asset",
        sa.Column("scope", sa.String(16), nullable=False, server_default="global"),
    )
    op.create_index("ix_skill_asset_scope", "skill_asset", ["scope"])

    # AgentApp.agent_dir（路径调整：指向 DATA_ROOT/agents/<id> 而非仅 skills 子目录）
    op.add_column(
        "agent_app",
        sa.Column("agent_dir", sa.String(255), nullable=True),
    )

    # AgentApp.workspace_hash（Agent 层内容指纹；不纳入 compute_fingerprint）
    op.add_column(
        "agent_app",
        sa.Column("workspace_hash", sa.String(64), nullable=True),
    )

    # AgentApp.agent_workspace_status（'pending' / 'active'）
    op.add_column(
        "agent_app",
        sa.Column(
            "agent_workspace_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
    )

    # ====== 新表 user_agent_app_association（v3 关键基础设施） ======

    op.create_table(
        "user_agent_app_association",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_app_id",
            sa.Integer,
            sa.ForeignKey("agent_app.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "last_synced_workspace_hash",
            sa.String(64),
            nullable=True,
        ),
        sa.Column(
            "associated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "agent_app_id", name="uq_user_agent_app"),
    )
    op.create_index(
        "ix_user_agent_app_user_id",
        "user_agent_app_association",
        ["user_id"],
    )
    op.create_index(
        "ix_user_agent_app_agent_app_id",
        "user_agent_app_association",
        ["agent_app_id"],
    )

    # ====== 数据回填 ======

    conn = op.get_bind()
    data_root = settings.DATA_ROOT  # 从 app.core.config 读取
    for row in conn.execute(sa.text("SELECT id, skill_names FROM agent_app")):
        conn.execute(
            sa.text("UPDATE agent_app SET agent_dir = :dir WHERE id = :id"),
            {"dir": f"{data_root}/agents/{row.id}", "id": row.id},
        )


def downgrade():
    # 反向顺序：先删表，后删字段
    op.drop_table("user_agent_app_association")
    op.drop_index("ix_skill_asset_scope", table_name="skill_asset")
    op.drop_column("skill_asset", "scope")
    op.drop_column("agent_app", "agent_workspace_status")
    op.drop_column("agent_app", "workspace_hash")
    op.drop_column("agent_app", "agent_dir")
```

**迁移后处理**：

1. 执行迁移后，所有现有 AgentApp 行的 `agent_workspace_status='pending'`
2. 服务启动期 `ensure_all_agent_workspaces` 自动补建 Agent 层 + workspace_hash + active 状态
3. 用户首次访问 chatbot 触发 lazy 校验 + (Global + Agent) → User 层复制
4. 运行 `scripts/migrate_workspace.py --apply` 处理旧路径文件迁移（备份在 archive/）

### 4.2 Phase 3 备选方案 B（仅在决策后启动）

```python
# alembic/versions/<rev>_session_checkpointer_dual_write.py（可选）
def upgrade():
    op.add_column("chat_session", sa.Column("sqlite_synced_at", sa.DateTime, nullable=True))
```

---

## 5. 风险点 R1-R18 与缓解措施（v3 修订版）

> v3 修订后：R3 重新定义（lazy 校验 + 启动期补建 双层保障）；R5 重新定义（ensure_all_agent_workspaces）；新增 R18（嵌套 User 层路径隔离）。

| # | 风险 | 等级 | 影响 | 缓解措施 |
|---|---|---|---|---|
| **R1** | access_token 缩短为 7 天后，refresh_token 机制被滥用（重放、批量爆破） | 中 | Phase 1 短期 | 重放检测：已撤销 refresh_token 再次使用 → 全 user 强制重新登录（`auth_refresh_replay_total` 告警）；`/auth/refresh` 速率限制 10/min/IP；sha256 哈希存储防批量爆破（详见 §6 R11） |
| **R2** | refresh_token DB 哈希泄漏后被批量反推 raw token | 低 | Phase 1 长期 | sha256 哈希存储 + 64 字符 base64 raw token（煽 384 位）→ DB 泄漏无法反推；refresh 旋转 + 重放检测兜底；refresh_token 定期清理（expires_at < now - 7d） |
| **R3（v3 修订）** | 三级 Workspace 引入后，Agent 层复制时机不清导致 User Chat 看到过期文件 | 中 | Phase 2 | **v3 双层保障**：① 启动期 `ensure_all_agent_workspaces` 补齐 Agent 层 + workspace_hash；② session 启动入口 `ensure_user_workspace_up_to_date` lazy 校验（hash 命中跳过，不一致增量同步）。DB 中以 `workspace_hash` 锁定 Agent 层快照；`user_agent_app_association.last_synced_workspace_hash` 加速同步判断 |
| **R4** | Agent 层命名空间与 AgentApp id 关联后，跨 AgentApp 共享 Skill 文件冲突 | 中 | Phase 2 | Agent 层 Skill 物理隔离：`{DATA_ROOT}/agents/<app_id>/skills/<name>/SKILL.md`；不允许两个 AgentApp 共享同一 Agent 层 skill（除非显式引用 Global） |
| **R5（v3 修订）** | 现有 AgentApp 行 `agent_dir` 为 NULL 或 `agent_workspace_status='pending'` 时，新代码如何处理 | 中 | Phase 2 启动期 | **v3 设计**：alembic 迁移时为所有已发布应用填充 `agent_dir = "{DATA_ROOT}/agents/<id>"`；启动时 `ensure_all_agent_workspaces` 检查并补齐缺失目录 + rematerialize 已 active 状态（防目录丢失）；**单 App 异常 try/except 隔离**（不阻断其他 App）；日志记录 `agent_workspace_backfilled` |
| **R6** | Session 改用 JSON/SQLite 后，LangGraph AsyncPostgresSaver 不再适配，HIL 中断恢复失效 | 高 | Phase 3（若改主存储） | **不推荐**改 Session 主存储；如坚持改造，**必须**为 LangGraph 实现 SQLite/JSON 自定义 Checkpointer（社区有 `SqliteSaver` 但 1.x 兼容性需验证）；双写期确保 PG 仍承担恢复职责 |
| **R7** | refresh_token 在前端如何存储，防 XSS 偷取 | 中 | Phase 1 前端 | refresh_token 仅在 Vue 内存态（不持久化 localStorage/sessionStorage）；access_token 短期 7 天降低 XSS 窗口；logout 立即撤销 refresh_token；`/auth/refresh` 端点速率限制兜底 |
| **R8** | 前端 refresh 拦截器无限递归刷新（refresh 端点本身返回 401） | 中 | Phase 1 前端 | 拦截器标记 `_retried=true` 防递归；refresh 失败 → 清空登录态 + 跳登录页；单元测试覆盖 `_retried` 标记生效（详见 §6 R12） |
| **R9** | subagent 测试运行 trace 是单次运行还是 session？概念混淆导致 API 命名冲突 | 低 | 概念澄清 | **明确**：保留 `SubAgentTestTrace` 表为 run trace；Chat 历史走 session 概念；前端 `/subagents/<name>/test-traces` 与 `/chatbot/messages` 互不干扰（详见 `spec-g3-session.md` §2） |
| **R10** | 业界主流（LangGraph/OpenAI）单层 token 模型的"会话"概念实际承载在 checkpointer 而非 token 字段；与本 spec 完全一致 —— 但实施细节差异大 | 低 | Phase 1 实施细节 | 调研 LangGraph 1.x `SqliteSaver` 与自定义 `JsonSaver` 的可行性，作为未来 Phase 4 选项（详见 `spec-g3-session.md` §5） |
| **R11（Refresh）** | `auth_refresh_total{status}` 计数 | — | Phase 1 监控 | status ∈ {success, replay_detected, invalid, expired} |
| **R12（Refresh）** | `refresh_token_active_count` Gauge | — | Phase 1 监控 | 当前活跃数；监控异常增长（> 100/用户） |
| **R13（Refresh）** | `auth_logout_total` 计数 | — | Phase 1 监控 | 登出频次（无阈值，仅观测） |
| **R14（Session CRUD）** | `session_create_total{agent_app_status}` 计数 | — | Phase 3 监控 | published vs unpublished 创建数 |
| **R15（Session CRUD）** | `session_delete_total` 计数（含 checkpoint 清理状态） | — | Phase 3 监控 | delete_total = delete_with_checkpoint_total（清理成功比例 100%） |
| **R16（Session CRUD）** | `session_export_total{format}` 计数 | — | Phase 3 监控 | json vs jsonl 导出分布（用于评估调试需求） |
| **R17（Session CRUD）** | `session_ownership_check_404_total` 计数 | — | Phase 3 监控 | 越权访问尝试（应远低于正常访问；> 0 提示探测） |
| **R18（Workspace v3 新增）** | 三级嵌套后 User 层路径跨（app, user）交叉污染 | 中 | Phase 2 | **v3 缓解**：User 层路径 `{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/` 严格嵌套，不同 app 不会交叉污染；`UserAgentAppAssociation` 表 CASCADE 保证删 App/User 同步清理；`delete_agent_app` 连锁调用 `shutil.rmtree(_agent_dir(app_id))` 清 Agent 层 + 所有嵌套 User 层；集成测试 `test_cross_user_isolation` 覆盖 |

---

## 6. 风险监控指标（v3 修订版）

> v3 修订后新增多项 Workspace 专属指标（lazy 校验、缓存淘汰、Agent 补建等）。

### 6.1 现有指标（保留）

| 风险 | 监控指标 | 阈值 |
|---|---|---|
| **R1** | `auth_refresh_replay_total` 计数 | > 0 立即告警（重放检测触发） |
| **R2** | `auth_refresh_total{status="invalid"}` 频次 / 日 | < 0.1% / 日 |
| **R3** | `user_workspace_drift` 日志频次 / 日 | < 1% / 日 |
| **R5** | `agent_workspace_backfilled` 日志频次 / 日 | 仅启动首日，非零；后续为零 |
| **R7** | 前端 refresh_token 内存态被 XSS 触达的次数（前端日志） | 0（refresh_token 不进 localStorage） |
| **R8** | 前端 `_retried` 标记触发次数（refresh 失败的 fallback） | < 0.1% / 日 |
| **R9** | `SubAgentTestTrace` 与 `Session` 写入路径混淆 PR 数 | 0 |
| **R11（Refresh）** | `auth_refresh_total{status}` 计数 | status ∈ {success, replay_detected, invalid, expired} |
| **R12（Refresh）** | `refresh_token_active_count` Gauge | 当前活跃数；监控异常增长（> 100/用户） |
| **R13（Refresh）** | `auth_logout_total` 计数 | 登出频次（无阈值，仅观测） |
| **R14（Session CRUD）** | `session_create_total{agent_app_status}` 计数 | published vs unpublished 创建数 |
| **R15（Session CRUD）** | `session_delete_total` 计数（含 checkpoint 清理状态） | delete_total = delete_with_checkpoint_total（清理成功比例 100%） |
| **R16（Session CRUD）** | `session_export_total{format}` 计数 | json vs jsonl 导出分布（用于评估调试需求） |
| **R17（Session CRUD）** | `session_ownership_check_404_total` 计数 | 越权访问尝试（应远低于正常访问；> 0 提示探测） |

### 6.2 v3 新增指标（Workspace 专属）

| 指标 | 说明 | 阈值 / 告警 |
|---|---|---|
| `agent_workspace_bootstrap_total{status}` | 启动期补建计数 | status ∈ {success, failed, skipped} |
| `agent_workspace_backfilled_count` | 补建目录计数（启动期） | 仅首日启动非零，后续为 0 |
| `agent_workspace_hash_drift_total{app_id}` | hash drift 检测 | > 0 记录警告（已 active 状态出现 drift） |
| `user_workspace_lazy_sync_total{outcome}` | lazy 校验计数 | outcome ∈ {hit, resynced, skipped} |
| `user_workspace_lazy_sync_duration_seconds` | lazy 校验耗时直方图 | p95 < 100ms |
| `agent_workspace_materialize_total{app_id, scope}` | materialize 调用计数 | scope ∈ {global→agent, combined→user} |
| `agent_workspace_materialize_duration_seconds{scope}` | materialize 耗时直方图 | scope=global→agent p95 < 200ms；scope=combined→user p95 < 300ms |
| `agent_workspace_files_written_total{scope}` | materialize 写入文件计数 | 仅 hash 不一致时增 1 |
| `agent_workspace_files_skipped_total{scope}` | hash 命中跳过计数 | 反映性能优化效果 |
| `agent_workspace_prune_total{app_id, user_id}` | 过期清理计数 | 偶发为 0，例行有少量 |
| `runtime_cache_size` | runtime cache 当前大小（Gauge） | < 64 |
| `runtime_cache_evict_total{reason}` | 缓存淘汰计数 | reason ∈ {stale_fingerprint, publish, patch, manual} |
| `runtime_cache_miss_total{app_id}` | cache miss 计数 | miss / hit < 30% |
| `user_agent_association_create_total` | 关联创建计数 | 业务指标，无阈值 |
| `user_agent_association_delete_total{trigger}` | 关联删除计数 | trigger ∈ {cascade_user, cascade_app, manual} |

---

## 7. 测试覆盖率要求

参考 `AGENTS.md` 与 `docs/development/*`：

| 项 | 覆盖率要求 |
|---|---|
| 后端单元测试 | >= 80% |
| 前端单元测试（vitest） | >= 70% |
| 集成测试 | 关键路径 100% 覆盖（auth flow / publish flow / chat flow / session export） |
| E2E 手工冒烟 | 每次 Phase 上线前完整跑一遍 `docs/agentapp-manual-testing.md` |

---

## 8. 不变更范围（保证兼容的边界 · v3 修订版）

以下文件/模块**不应在本重构中修改**（除非用户明确同意）：

- `app/main.py` lifespan 启动流程（仅在 Phase 2 **新增** `ensure_all_agent_workspaces` 调用）
- `app/core/mcp_client.py`（MCP 会话池 worker 模型，独立演进）
- `app/services/agents/assembly.py` 的 deepagents 编译逻辑（**仅** fingerprint 维持原签名 + FilesystemBackend nested 路径 + user_id 透传；**不**改 deepagents 主逻辑）
- `app/workflow/` 全部模块（Workflow 引擎单独 spec）
- `alembic/versions/b25d38b0cd7c_initial_schema.py` 既有迁移
- `agent-web/src/components/WebAgentTable.vue` 与 `WebAgentFormDialog.vue`（通用组件）
- `agent-web/src/styles/` 与 `agent-web/src/composables/useRequest.ts`（基础设施）

**v3 修订项**（与原 spec 不同）：

- `app/services/agents/skills_store.py` 中**双存架构核心**（`refresh_disk_from_db` / `read_global` / DB 真相源机制）**不改动**；**仅新增** v3 三层路径 helpers + 复制函数
- `app/services/agents/assembly.py` 中 `_APP_FIELDS`（5 输入字段）**不新增** `workspace_hash`（与 `_load_skill_hashes` 冗余，v3 否决项）
- `app/services/agents/runtime.py` 中 `WorkflowAppRuntime` / `DeepAgentsAppRuntime` 主体逻辑**不改动**；**仅** `_runtime_cache` key 维度升级 + `_COMPILE_USER_ID` 删除
- `app/services/agents/test_runner.py` 中 standalone runner 主体逻辑**不改动**；**仅** docstring 添加 MVP 说明 + 新增 `materialize_into_combined_directory` 函数（不调用）
- 前端 `agent-web/src/views/agent/AgentList.vue` **不扩展**（MVP 暂缓，后续 G3 决定）
