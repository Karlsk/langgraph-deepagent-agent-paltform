# G3 Spec：Session 存储选型

> **主题**：评估 Session 存储方案（PG / SQLite / JSON），并给出推荐 + 备选实施。
> **关联文档**：`overview.md`（路线图）、`files-risks.md`（文件清单 + 风险）
> **目标读者**：后端架构师
> **风险等级**：中（仅在评估后认为必要才改主存储）
> **估算工时**：2 周（含调研 + 评估 + 推荐方案实施）

---

## 1. 目标与非目标

### 1.1 目标

1. **澄清概念边界**：subagent/agent log ≠ session 历史；前者是单次执行 trace，后者是 chat 消息流
2. **给出存储选型推荐**：保留 PG / 引入 SQLite / 纯 JSON 三方案对比
3. **设计 JSON 视图层**：即便保留 PG，也提供 JSON 导出口用于调试 / 离线分析
4. **明确 session 文件位置**（若改 JSON）：推荐 User 层（与 `sessions.user_id` 外键语义一致）
5. **全新会话 CRUD API 设计**：承接 G1 的 `/auth/session` 注释废弃；提供 RESTful `/sessions` 端点（list / get / create / patch / delete）；鉴权统一用 `Depends(get_current_user)` + 函数内 `X-Session-Id` header 校验 session 归属

### 1.2 非目标

- 替换 LangGraph AsyncPostgresSaver（不推荐；详见 §3）
- 跨设备实时同步 session 文件（不在评估范围）
- Session 内容加密 / 隐私合规（独立 Phase）
- **chatbot 端点改造（chat / chat_stream / clear_chat_history / get_session_messages）**：chatbot 整体**直接废弃**，不在 Phase 3 范围；如未来重启，需要单独的 chatbot spec

---

## 2. 概念边界澄清

| 概念 | 含义 | 当前存储 | 是否属于 Session 概念 |
|---|---|---|---|
| **SubAgent Test Trace** | 一次 `run_subagent_once` 的完整事件流（llm_call/tool_call/run_finished） | PG `SubAgentTestTrace` | ❌ 不是 session；是 run |
| **Chat History** | 一次 chat 会话的用户/助手消息流 | PG via LangGraph `AsyncPostgresSaver` | ✅ 是 session |
| **Session Metadata** | session 的属性（id / user_id / name / agent_app_id / created_at） | PG `Session` | ✅ 是 session |
| **Agent Runtime Fingerprint** | AgentApp 的发布指纹 | PG `AgentApp.published_hash` | ❌ 不是 session；是配置 |

**结论**：
- **subagent/agent log = 单次运行过程**（`SubAgentTestTrace` 表）→ **保留 PG 不变**
- **session 文件 = 大的会话历史**（`Session` 表 + checkpointer）→ **选型对象**

---

## 3. 三方案对比

| 维度 | A. 保留 PG（推荐） | B. 引入 SQLite | C. 纯 JSON 文件 |
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
| **结论** | ✅ **推荐保留 PG** | ⚠️ 仅在"显式单机调试"场景使用 | ❌ 不推荐作为生产方案 |

---

## 4. 推荐方案 A：保留 PG + JSON 视图层

### 4.1 设计原则

1. **主存储不动**：PG `Session` 表 + LangGraph `AsyncPostgresSaver` 继续承担生产数据
2. **新增 JSON 导出端点**：供调试 / 离线分析 / 用户导出使用
3. **不改 LangGraph checkpointer**：保留 AsyncPostgresSaver（HIL 中断恢复依赖 SQL 持久化）

### 4.2 JSON 视图层设计

#### 后端端点

```python
# app/api/v1/sessions.py（Phase 3 新文件；从 chatbot.py 拆出）

from fastapi import Header, HTTPException

@router.get("/sessions/{session_id}/export", response_model=ApiResponse[dict])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def export_session_history(
    request: Request,
    session_id: str,
    user: User = Depends(get_current_user),
    format: Literal["json", "jsonl"] = Query(default="json"),
) -> ApiResponse[dict]:
    """导出 session 的完整消息历史为 JSON（调试 / 离线分析用）。

    鉴权：user token（HTTPBearer） + X-Session-Id header；归属校验在函数内完成。

    Returns:
        ApiResponse[dict]: 包含 session_id / user_id / agent_app_id / messages 数组的 JSON。

    Raises:
        HTTPException: 404 session 不存在或不属于当前 user。
    """
    try:
        # 1. session 归属校验（函数内）
        target_session = await db_service.get_session(session_id)
        if target_session is None or target_session.user_id != user.id:
            raise HTTPException(status_code=404, detail="session not found or not owned by user")

        # 2. 从 LangGraph checkpointer 读取完整消息历史
        async with db_service.get_async_session() as db_session:
            runtime_obj = await get_runtime(db_session, target_session.agent_app_id)
        messages = await runtime_obj.get_chat_history(target_session.id)

        export_data = {
            "session_id": target_session.id,
            "user_id": target_session.user_id,
            "username": target_session.username,
            "agent_app_id": target_session.agent_app_id,
            "name": target_session.name,
            "created_at": target_session.created_at.isoformat(),
            "exported_at": datetime.now(UTC).isoformat(),
            "format": format,
            "message_count": len(messages),
            "messages": [
                {
                    "type": msg.type,
                    "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                    "timestamp": getattr(msg, "additional_kwargs", {}).get("timestamp"),
                }
                for msg in messages
            ],
        }
        logger.info(
            "session_history_exported",
            session_id=target_session.id,
            user_id=user.id,
            message_count=len(messages),
        )
        return ApiResponse.success(export_data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("session_export_failed", session_id=session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

#### 前端导出按钮

```vue
<!-- agent-web/src/views/chat/ChatView.vue（Phase 3 实现） -->
<el-button @click="exportSession" :icon="Download">导出 JSON</el-button>

<script setup>
async function exportSession() {
    const blob = await exportSessionHistory(currentSessionId.value)
    saveAs(blob, `session-${currentSessionId.value}.json`)
}
</script>
```

```ts
// agent-web/src/api/sessions.ts（Phase 3 新增；文件命名从 chatbot.ts 改为 sessions.ts）
export function exportSessionHistory(sessionId: string): Promise<Blob> {
    return get<Blob>(`/sessions/${sessionId}/export?format=json`, { responseType: 'blob' })
}
```

### 4.3 DoD（推荐方案 A）

- [ ] 后端 `GET /sessions/{session_id}/export` 端点（从 `chatbot.py` 拆出至 `app/api/v1/sessions.py`）
- [ ] 支持 `format=json` / `format=jsonl` 两种格式
- [ ] 鉴权模式：`Depends(get_current_user)` + 函数内 session 归属校验
- [ ] 前端 ChatView 集成"导出 JSON"按钮（Phase 3 实现）
- [ ] 文档：
  - `docs/observability.md` 新增"会话调试导出"章节
  - `docs/agentapp-manual-testing.md` 第 7 节新增"导出 session 历史"小节

---

## 5. 备选方案 B：引入 SQLite（仅在评估后认为必要才执行）

### 5.1 触发条件

满足以下**任一**条件时启动方案 B：

- LangGraph 1.x 官方 `SqliteSaver` 兼容性 ≥ 90%
- PG 性能瓶颈明确（具体指标：chat TPS < 100 / p95 latency > 500ms）
- 多设备离线场景需求明确（用户能容忍跨设备同步延迟）
- 运维侧希望降低 PG 体积（历史消息占 PG 80%+ 空间）

### 5.2 实施要点

#### 调研阶段（1 周）

- [ ] 验证 LangGraph 1.x `SqliteSaver` 与本项目 `AsyncPostgresSaver` 行为一致性
- [ ] 评估 HIL 中断恢复在 SqliteSaver 下的行为
- [ ] 评估多设备 SQLite 文件同步方案（NFS / rclone / 自实现 sync daemon）

#### 实施阶段（1 周）

- [ ] 实现 SqliteSaver checkpointer 适配层（per-user 或 per-agent）
- [ ] 双写期：PG + SQLite 同步写入 1-2 个版本后切读 SQLite
- [ ] 数据回填脚本：从 PG checkpointer 历史导出到 SQLite
- [ ] 备份策略：SQLite 文件按日备份至对象存储

### 5.3 关键风险

| 风险 | 缓解 |
|---|---|
| SqliteSaver 与现有 LangGraph 1.x API 不兼容 | 调研阶段充分验证；不兼容则回退方案 A |
| HIL 中断恢复失败 | 双写期确保 PG 仍承担恢复职责 |
| SQLite 文件损坏 | WAL + 日备份；客户端启动校验 |
| 跨设备同步延迟 | 引入乐观锁与冲突检测；冲突时以最新版本为准 |

### 5.4 DoD（备选方案 B，仅在决策后启动）

- [ ] 调研报告：`docs/research/langgraph-sqlite-checkpointer.md`
- [ ] `app/services/agents/checkpointer.py` SqliteSaver 适配层
- [ ] `bootstrap.py` 双写配置开关（`SESSION_CHECKPOINTER_BACKEND=pg|sqlite|both`）
- [ ] 数据回填脚本：`scripts/migrate_checkpointer_pg_to_sqlite.py`
- [ ] 备份策略：`scripts/backup_sqlite_checkpoints.sh`
- [ ] 文档：`docs/observability.md` 新增"SqliteSaver 部署指南"章节

---

## 6. 方案 C：纯 JSON 文件（**不推荐**）

### 6.1 不推荐理由

1. **LangGraph 不支持 JSON checkpointer** —— 必须自实现 Checkpointer 适配层，工作量等同于方案 B + LangGraph 适配
2. **HIL 中断恢复必需 SQL 持久化** —— JSON 文件无法支持 LangGraph 的 thread_id 索引与状态机持久化
3. **文件锁 / 并发控制复杂** —— 多进程 / 多线程场景需 fcntl 或 asyncio lock 串行化
4. **查询能力退化** —— 无法做 "找出用户最近活跃会话"、"按 agent_app_id 聚合" 等分析查询
5. **备份 / 同步策略复杂** —— 文件级同步不如 DB 复制透明

### 6.2 若坚持 JSON 的最小实现（仅供决策参考）

| 项 | 路径 |
|---|---|
| 文件位置 | `{USER_ROOT}/<uid>/sessions/<sid>.json`（**推荐 User 层**） |
| 锁策略 | asyncio.Lock per file + fcntl flock |
| 写入策略 | append-only journal + 定期 snapshot |
| 读取 | 启动时 load 整个文件到内存 |

**仍然不推荐用于生产** —— 仅可作为单机开发环境的玩具实现。

---

## 7. Session 文件位置决策（若走 JSON 方案）

| 选项 | 路径 | 优势 | 劣势 |
|---|---|---|---|
| **方案 X：Agent 层** | `{AGENT_ROOT}/<app_id>/sessions/<sid>.json` | 概念清晰（会话归属 Agent） | 用户切换 Agent 时历史碎片化；不利于"用户级 chat 历史聚合" |
| **方案 Y：User 层**（**推荐**） | `{USER_ROOT}/<uid>/sessions/<sid>.json` | 符合"user_id 是会话归属主键"语义；聚合所有 Agent 的历史 | 用户级目录需包含 agent_app_id 索引 |
| **方案 Z：混合** | `{USER_ROOT}/<uid>/agents/<app_id>/sessions/<sid>.json` | 既可按 user 聚合，也可按 agent 分层 | 路径嵌套深（4 层） |

**结论**：如果保留 PG（方案 A），位置问题不存在 —— `Session` 表天然按 `user_id` 索引，`agent_app_id` 是列。如果坚持走 JSON（不推荐），选 **Y：User 层** —— 与当前 `sessions.agent_app_id` 外键语义一致，且支持"用户级 chat 历史聚合"视图。

---

## 8. DoD（Phase 3 推荐方案 A + 新 CRUD API）

> 本节是 G3 推荐方案 A（JSON 视图层）+ 新 CRUD API（§11）的统一 DoD 清单。

### 8.1 推荐方案 A：JSON 视图层

- [ ] 后端 `GET /sessions/{session_id}/export` 端点（从 `chatbot.py` 拆出至 `app/api/v1/sessions.py`）
- [ ] 支持 `format=json` / `format=jsonl` 两种格式
- [ ] 鉴权模式：`Depends(get_current_user)` + 函数内 session 归属校验
- [ ] 前端 ChatView 集成"导出 JSON"按钮（Phase 3 实现）
- [ ] 文档：
  - `docs/observability.md` 新增"会话调试导出"章节
  - `docs/agentapp-manual-testing.md` 第 7 节新增"导出 session 历史"小节

### 8.2 新会话 CRUD API（详见 §11）

- [ ] `app/api/v1/sessions.py` 新文件（5 个端点 + export 端点从 chatbot.py 拆出）
- [ ] `app/schemas/session.py` 新增 4 个 schema：`SessionRead` / `SessionCreate` / `SessionUpdate` / `SessionListResponse`
- [ ] `app/services/database.py` 新增 6 个 session CRUD 方法
- [ ] `app/services/agents/checkpointer_store.py` 新增 `delete_thread(thread_id)` 方法（级联清理）
- [ ] 旧 `/auth/session` 端点保留 1 个 release 后删除（注释阶段不删）
- [ ] 前端 `agent-web/src/api/sessions.ts` 新增 5 个 API 包装（含 exportSessionHistory 移入）
- [ ] 前端测试 `agent-web/tests/sessions.spec.ts` 覆盖 5 个端点 + 越权场景

---

## 9. 验证

### 9.1 单元测试（推荐方案 A：JSON 视图层）

- `tests/unit/api/test_sessions.py::test_export_*`：
  - `test_export_session_history_returns_messages`
  - `test_export_session_history_format_jsonl`
  - `test_export_session_history_other_user_forbidden`
  - `test_export_session_history_empty_messages`

### 9.2 单元测试（新 CRUD API，详见 §11.9）

- `tests/unit/api/test_sessions.py::test_crud_*`：
  - `test_list_sessions_returns_only_owned`
  - `test_get_session_404_for_other_user`
  - `test_create_session_validates_agent_app_published`
  - `test_create_session_default_agent_app_none_allowed`
  - `test_update_session_other_user_returns_404`
  - `test_delete_session_cascades_checkpoint`

### 9.3 集成测试

- `tests/integration/api/test_session_export.py`：
  - `test_login_to_chat_to_export_full_flow`
  - `test_export_after_hil_interrupt_includes_decision_history`
  - `test_export_consistent_with_get_messages`
- `tests/integration/api/test_session_crud.py`（详见 §11.9）：
  - `test_full_session_lifecycle`
  - `test_concurrent_delete_idempotent`
  - `test_message_count_reflects_langgraph_state`

### 9.4 手工冒烟

1. JSON 导出：login → create session → chat（多轮） → `GET /sessions/{sid}/export?format=json`
2. 验证返回 JSON 含完整 user/assistant 消息流 + tool_calls
3. 验证 format=jsonl 行为（每行一条消息）
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

### 11.1 URL 设计

| 阶段 | 端点 | 状态 |
|---|---|---|
| Phase 1（已注释） | `POST /auth/session` | handler noop，路由保留 1 个 release 后删除 |
| **Phase 3（新）** | `GET /sessions`、`GET /sessions/{sid}`、`POST /sessions`、`PATCH /sessions/{sid}`、`DELETE /sessions/{sid}` | 本 spec 设计 |

> 命名风格：RESTful 资源名 `/sessions`（复数），与 LangGraph Platform `/threads` 风格对齐。

### 11.2 端点清单

| 方法 | URL | 用途 | 鉴权 |
|---|---|---|---|
| `GET` | `/sessions` | 列出当前 user 的所有 session（按 created_at desc） | `Depends(get_current_user)` |
| `GET` | `/sessions/{session_id}` | 获取单个 session 详情（含 agent_app_id / name / created_at） | 同上 + session 归属校验 |
| `POST` | `/sessions` | 创建 session（body 含 agent_app_id） | `Depends(get_current_user)` |
| `PATCH` | `/sessions/{session_id}` | 更新 name（重命名） | 同上 + session 归属校验 |
| `DELETE` | `/sessions/{session_id}` | 删除 session + 级联清理 LangGraph checkpoint | 同上 + session 归属校验 |

### 11.3 鉴权模式（Phase 3 统一）

```python
# 模式：Depends(get_current_user) + 函数内 session 归属校验
async def _resolve_session_or_404(
    user: User, session_id: str
) -> Session:
    """根据 session_id 解析 session，校验归属，不属于当前 user 返 404。"""
    target = await db_service.get_session(session_id)
    if target is None or target.user_id != user.id:
        # 故意用 404 而非 403：避免泄露 session_id 是否存在
        raise HTTPException(status_code=404, detail="session not found or not owned by user")
    return target
```

> **关键决策**：所有 session 操作的越权统一返 **404 而非 403** —— 与 G1 一致，避免泄露 session_id 是否存在（防 enumeration attack）。

### 11.4 Schema 设计（`app/schemas/session.py` 新文件）

```python
from pydantic import BaseModel, Field
from datetime import datetime

class SessionRead(BaseResponse):
    """GET /sessions 列表项 / GET /sessions/{sid} 详情共用 schema。"""
    session_id: str
    name: str = Field(default="", max_length=100)
    agent_app_id: int | None = None
    created_at: datetime
    updated_at: datetime | None = None
    # 可选：仅列表端点填充
    message_count: int | None = Field(default=None)


class SessionCreate(BaseModel):
    """POST /sessions 请求体。"""
    agent_app_id: int | None = Field(
        default=None,
        description="绑定的 AgentApp id；None 表示未关联任何 AgentApp"
    )
    name: str = Field(default="", max_length=100)


class SessionUpdate(BaseModel):
    """PATCH /sessions/{sid} 请求体（仅支持重命名）。"""
    name: str = Field(..., min_length=1, max_length=100)


class SessionListResponse(BaseResponse):
    """GET /sessions 响应。"""
    items: list[SessionRead]
    total: int
```

### 11.5 端点实现（伪代码）

```python
# app/api/v1/sessions.py（新文件；从 chatbot.py 拆出）

@router.get("/sessions", response_model=ApiResponse[SessionListResponse])
@limiter.limit("20/minute")
async def list_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[SessionListResponse]:
    items = await db_service.list_user_sessions(user.id, limit=limit, offset=offset)
    total = await db_service.count_user_sessions(user.id)
    return ApiResponse.success(SessionListResponse(items=items, total=total))


@router.get("/sessions/{session_id}", response_model=ApiResponse[SessionRead])
@limiter.limit("60/minute")
async def get_session(
    request: Request,
    session_id: str,
    user: User = Depends(get_current_user),
) -> ApiResponse[SessionRead]:
    target = await _resolve_session_or_404(user, session_id)
    return ApiResponse.success(SessionRead.model_validate(target))


@router.post("/sessions", response_model=ApiResponse[SessionRead], status_code=201)
@limiter.limit("10/minute")
async def create_session(
    request: Request,
    body: SessionCreate,
    user: User = Depends(get_current_user),
) -> ApiResponse[SessionRead]:
    # 1. 校验 agent_app_id（若提供）
    if body.agent_app_id is not None:
        app_cfg = await db_service.get_agent_app(body.agent_app_id)
        if app_cfg is None:
            raise HTTPException(status_code=404, detail="agent_app not found")
        if app_cfg.status != "published":
            raise HTTPException(status_code=422, detail="agent_app is not published")

    # 2. 创建 session
    session_id = str(uuid.uuid4())
    new_session = await db_service.create_session(
        session_id=session_id,
        user_id=user.id,
        username=user.username,
        agent_app_id=body.agent_app_id,
        name=body.name,
    )
    logger.info(
        "session_created",
        session_id=session_id,
        user_id=user.id,
        agent_app_id=body.agent_app_id,
    )
    return ApiResponse.success(SessionRead.model_validate(new_session))


@router.patch("/sessions/{session_id}", response_model=ApiResponse[SessionRead])
@limiter.limit("20/minute")
async def update_session(
    request: Request,
    session_id: str,
    body: SessionUpdate,
    user: User = Depends(get_current_user),
) -> ApiResponse[SessionRead]:
    target = await _resolve_session_or_404(user, session_id)
    updated = await db_service.update_session_name(target.id, body.name)
    logger.info(
        "session_renamed",
        session_id=session_id,
        user_id=user.id,
        new_name=body.name,
    )
    return ApiResponse.success(SessionRead.model_validate(updated))


@router.delete("/sessions/{session_id}", status_code=204)
@limiter.limit("10/minute")
async def delete_session(
    request: Request,
    session_id: str,
    user: User = Depends(get_current_user),
) -> None:
    target = await _resolve_session_or_404(user, session_id)

    # 1. 删除 PG Session 元数据行
    await db_service.delete_session(target.id)

    # 2. 级联清理 LangGraph checkpoint（按 thread_id = session_id）
    async with db_service.get_async_session() as db_session:
        await checkpointer_store.delete_thread(db_session, thread_id=session_id)

    logger.info(
        "session_deleted",
        session_id=session_id,
        user_id=user.id,
        cascade_checkpoint=True,
    )
    return None
```

### 11.6 前端 API（`agent-web/src/api/sessions.ts`）

```ts
import { get, post, patch, del } from '@/utils/request'

export interface SessionRead {
  session_id: string
  name: string
  agent_app_id: number | null
  created_at: string
  updated_at: string | null
  message_count?: number
}

export function listSessions(): Promise<{ items: SessionRead[]; total: number }> {
  return get<{ items: SessionRead[]; total: number }>('/sessions')
}

export function getSession(sessionId: string): Promise<SessionRead> {
  return get<SessionRead>(`/sessions/${sessionId}`)
}

export function createSession(body: { agent_app_id?: number; name?: string }): Promise<SessionRead> {
  return post<SessionRead>('/sessions', body)
}

export function updateSession(sessionId: string, body: { name: string }): Promise<SessionRead> {
  return patch<SessionRead>(`/sessions/${sessionId}`, body)
}

export function deleteSession(sessionId: string): Promise<void> {
  return del<void>(`/sessions/${sessionId}`)
}
```

### 11.7 数据库服务（`app/services/database.py` 新增方法）

```python
async def list_user_sessions(user_id: int, limit: int = 50, offset: int = 0) -> list[Session]:
    """列出 user 的 session（按 created_at desc）。"""

async def count_user_sessions(user_id: int) -> int:
    """计数 user 的 session 总数（用于分页）。"""

async def get_session(session_id: str) -> Session | None:
    """按 session_id 查询（注意：调用方需自行校验 user_id 归属）。"""

async def create_session(
    session_id: str, user_id: int, username: str | None,
    agent_app_id: int | None, name: str = "",
) -> Session:
    """创建新 session。"""

async def update_session_name(session_id: str, new_name: str) -> Session | None:
    """更新 session name。"""

async def delete_session(session_id: str) -> bool:
    """删除 session 元数据（调用方需自行级联清理 checkpoint）。"""
```

### 11.8 DoD（Phase 3 新 CRUD API）

- [ ] `app/api/v1/sessions.py` 新文件（5 个端点 + export 端点从 chatbot.py 拆出）
- [ ] `app/schemas/session.py` 新增 4 个 schema：`SessionRead` / `SessionCreate` / `SessionUpdate` / `SessionListResponse`
- [ ] `app/services/database.py` 新增 6 个 session CRUD 方法
- [ ] `app/services/agents/checkpointer_store.py` 新增 `delete_thread(thread_id)` 方法（级联清理）
- [ ] 旧 `/auth/session` 端点保留 1 个 release 后删除（注释阶段不删）
- [ ] 前端 `agent-web/src/api/sessions.ts` 新增 5 个 API 包装（含 exportSessionHistory 移入）
- [ ] 前端测试 `agent-web/tests/sessions.spec.ts` 覆盖 5 个端点 + 越权场景

### 11.9 验证（Phase 3 新 CRUD API）

#### 单元测试（`tests/unit/api/test_sessions.py`）

```python
async def test_list_sessions_returns_only_owned(user_a, user_b):
    """user_a 只能看到自己的 session，user_b 的 session 不可见。"""

async def test_get_session_404_for_other_user(user_a, user_b):
    """user_a 查询 user_b 的 session 返回 404（不是 403）。"""

async def test_create_session_validates_agent_app_published():
    """创建时若 agent_app 未发布，返回 422。"""

async def test_create_session_default_agent_app_none_allowed():
    """不传 agent_app_id 时创建成功，session.agent_app_id 为 None。"""

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
    """同一 session 被并发删除两次：第一次 204，第二次 404。"""

async def test_message_count_reflects_langgraph_state(user_token, session_id):
    """SessionRead.message_count 与 LangGraph checkpoint 一致。"""
```

#### 手工冒烟（参考 `docs/agentapp-manual-testing.md` 第 7 节更新版）

1. 新 CRUD 流程：login → POST /sessions（agent_app_id=1）→ GET /sessions 列表可见 → PATCH /sessions/{sid}（name="新会话"）→ DELETE /sessions/{sid} → 列表已删除
2. 越权场景：user A 登录后尝试访问 user B 的 session_id → 全部端点返 404（不是 403）
3. 级联清理：创建 session → chat 几条消息 → 删除 session → LangGraph checkpoint 中无该 thread

### 11.10 回滚策略

如 Phase 3 上线后 CRUD API 有重大问题：

1. **后端回滚**：revert `app/api/v1/sessions.py` 新文件（保留 schema 注释）；旧 `/auth/session` 注释端点恢复为简化版
2. **前端回滚**：revert `agent-web/src/api/sessions.ts` 新文件；旧 `chatbot.ts` 接口恢复
3. **数据无影响**：本 Phase 不涉及 schema 迁移；新表（refresh_token 表是 Phase 1）已存在

---

## 12. G2 集成接口预留（2026-08-25 追加）

> 本节是 G2（spec-g2-workspace.md）审查期间由 G2 团队指定的集成接口。G3 实施时**必须按本节约定**调用 G2 提供的函数 / 端点。

### 12.1 G2 提供的接口

G2 实施完成后，将提供以下接口供 G3 使用：

#### 12.1.1 `ensure_user_workspace_up_to_date` 函数

```python
# app/services/agents/skills_store.py
async def ensure_user_workspace_up_to_date(
    session: Session,
    *,
    user_id: str,
    app_id: int,
) -> None:
    """Lazy 校验：User 层与 (Global + Agent) 集合是否一致，不一致则增量同步。

    G3 在以下时机调用：
    - POST /sessions（创建 session，不带 session_id）入口
    - GET /sessions/{session_id}（加载 session，带 session_id）入口
    - 其他需要 user 层 skill 最新副本的场景

    内部行为：
    - 比对 AgentApp.workspace_hash（DB）与 user 层实际 hash
    - 不一致 → 调用 materialize_to_user_combined 增量同步
    - 一致 → 跳过
    """
```

#### 12.1.2 `lazy_workspace_sync` 参数

```python
# app/services/agents/runtime.py get_runtime
async def get_runtime(
    session: Session,
    agent_app_id: Optional[str],
    *,
    user_id: int,
    lazy_workspace_sync: bool = True,  # G3 可显式控制
) -> AgentAppRuntime:
    ...
    if lazy_workspace_sync:
        await ensure_user_workspace_up_to_date(
            session, user_id=str(user_id), app_id=app_cfg.id
        )
    ...
```

### 12.2 G3 集成点

#### 12.2.1 `POST /sessions`（创建）

```python
# app/api/v1/sessions.py（建议位置）
async def create_session(
    payload: SessionCreate,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[SessionRead]:
    """创建 session 入口：先 lazy 校验 user 层 workspace。"""
    app_id = payload.agent_app_id or await _resolve_default_agent_app_id(db)

    # G2 集成：lazy 校验 user 层 workspace
    await ensure_user_workspace_up_to_date(
        db, user_id=str(user.id), app_id=app_id
    )

    # 创建 session 记录（既有逻辑）
    session_row = await db_service.create_session(
        user_id=user.id,
        agent_app_id=app_id,
        name=payload.name,
    )

    return ApiResponse.success(session_row)
```

#### 12.2.2 `GET /sessions/{session_id}`（加载）

```python
async def get_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
) -> ApiResponse[SessionRead]:
    """加载 session：先 lazy 校验 user 层 workspace。"""
    session_row = await db_service.get_session(session_id, user_id=user.id)
    if session_row is None:
        raise HTTPException(404, "session not found")

    # G2 集成：lazy 校验 user 层 workspace
    app_id = int(session_row.agent_app_id)
    await ensure_user_workspace_up_to_date(
        db, user_id=str(user.id), app_id=app_id
    )

    return ApiResponse.success(session_row)
```

#### 12.2.3 `chatbot` 端点（若 G3 不废弃）

若 G3 §1.2 的"chatbot 整体废弃"决策**未来被推翻**，chatbot 端点在 chat 前必须先 lazy 校验：

```python
async def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: DBSession = Depends(get_db_session),
):
    # G2 集成：每次 chat 前 lazy 校验
    await ensure_user_workspace_up_to_date(
        db, user_id=str(user.id), app_id=int(payload.agent_app_id)
    )
    ...
```

### 12.3 G3 session JSON 文件路径（若采用 JSON 视图层）

按 G2 §1.1（v3）目录结构，session JSON 文件归属：

```
{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.json
```

**清理时机**：
- 删除 session 时同步清理 JSON 文件（DELETE /sessions/{id} 端点）
- 删除 AgentApp 时清理整个 agents/<app_id>/ 子树（含所有 session）
- 取消 user 关联时清理该 app 下该 user 的 sessions 目录

### 12.4 G3 集成验证清单

G3 实施时必须验证：

- [ ] `POST /sessions` 入口调用 `ensure_user_workspace_up_to_date`
- [ ] `GET /sessions/{session_id}` 入口调用 `ensure_user_workspace_up_to_date`
- [ ] session JSON 文件路径符合 `{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.json`
- [ ] 删除 session 时同步清理 JSON 文件
- [ ] 单元测试覆盖 lazy 校验触发逻辑（mock `ensure_user_workspace_up_to_date`）
- [ ] 集成测试覆盖 user 在不同 agent 下 session 隔离

### 12.5 不在 G3 集成范围

- G2 路径 helper（`_data_root` / `_agent_skill_dir` / `_user_skill_file` 等）由 G2 实现，G3 不直接调用
- G2 启动校验（`ensure_default_agent_workspace`）由 G2 在 lifespan 触发，G3 不重复
- G2 的 `UserAgentAppAssociation` 表由 G2 管理，G3 只读
