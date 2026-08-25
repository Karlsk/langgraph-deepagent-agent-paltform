# G2 Spec：Workspace 三级文件系统

> **主题**：引入 Agent 层；publish 时 Global→Agent 复制；关联用户时 Agent→User 复制；Agent/SubAgent 测试读 Global+Agent，User Chat 读 User。
> **关联文档**：`overview.md`（路线图）、`files-risks.md`（文件清单 + 风险）
> **目标读者**：后端实施者
> **风险等级**：中（DB schema + 目录结构 + 发布流程）
> **估算工时**：2 周

---

## 1. 目标与非目标

### 1.1 目标

1. **三级目录结构**：Global（共享基础）/ Agent（Agent 专属）/ User（用户个性化）
2. **复制时机定义**：
   - publish AgentApp 时 Global → Agent 复制
   - 用户首次关联 AgentApp 时 Agent → User 复制（叠加 Global 共享）
   - Agent 层内容变更后重新 publish 时 User 层增量同步
3. **fingerprint 锁定**：Agent 层内容变更后 `published_hash` 自动重算；Chat 端运行时校验 fingerprint 一致性
4. **兼容策略**：已发布 AgentApp 启动时自动补建 Agent 层目录（不阻断）

### 1.2 非目标（不在本 Phase 范围）

- Agent 层 Skill UI 编辑（仅后端 schema + 复制逻辑；UI Phase 4 再做）
- Skill 文件的多版本快照（保留现有 `version` 字段即可）
- 跨 AgentApp 共享 Agent 层 skill（本版本**禁止**；详见 `open-questions.md` Q4）

---

## 2. 目录结构设计

### 2.1 当前 vs 提议

```text
当前（两层）：
{SKILLS_ROOT}/
  global/
    <skill_name>/
      SKILL.md
  users/
    <user_id>/
      <skill_name>/
        SKILL.md

提议（三层）：
{SKILLS_ROOT}/
  global/                        # 全局共享（不变）
    <skill_name>/
      SKILL.md
  agents/                        # 新增：Agent 专属
    <app_id>/                    # 路径命名详见 open-questions.md Q3
      skills/
        <skill_name>/
          SKILL.md
  users/                        # 用户个性化
    <user_id>/
      <skill_name>/
        SKILL.md
```

### 2.2 读取路径策略

| 调用方 | 读取层 | 路径 |
|---|---|---|
| **SubAgent 测试**（`run_subagent_once`） | Global + Agent | `tmp_skills_root/<name>/SKILL.md`（运行时 materialise） |
| **User Chat**（`runtime.ainvoke`） | User（聚合 Global + Agent → User 后） | `{SKILLS_ROOT}/users/<user_id>/<name>/SKILL.md` |
| **AgentApp 编辑预览**（前端） | Global + Agent | 通过 API 直接读取 DB body |

### 2.3 关键路径变量（`app/core/config.py` 新增）

```python
SKILLS_ROOT: Path = Path("./data/skills")              # 已有
AGENTS_ROOT: Path = Path("./data/skills/agents")       # 新增
USERS_ROOT: Path = Path("./data/skills/users")         # 显式化（已存在）
```

> 默认 `AGENTS_ROOT = SKILLS_ROOT / "agents"`；可独立配置以支持跨盘 / NFS。

---

## 3. alembic 迁移设计

### 3.1 新增字段

```python
# alembic/versions/<rev>_agent_workspace.py

def upgrade():
    # SkillAsset.scope: 'global' | 'agent'
    op.add_column(
        "skill_asset",
        sa.Column("scope", sa.String(16), nullable=False, server_default="global"),
    )
    op.create_index("ix_skill_asset_scope", "skill_asset", ["scope"])

    # AgentApp.agent_dir: 物理路径（冗余存以便查询）
    op.add_column(
        "agent_app",
        sa.Column("agent_dir", sa.String(255), nullable=True),
    )

    # AgentApp.workspace_hash: Agent 层内容指纹（独立于 published_hash）
    op.add_column(
        "agent_app",
        sa.Column("workspace_hash", sa.String(64), nullable=True),
    )

    # AgentApp.agent_workspace_status: 'pending' | 'migrated' | 'active'
    op.add_column(
        "agent_app",
        sa.Column("agent_workspace_status", sa.String(16), nullable=False, server_default="pending"),
    )


def downgrade():
    op.drop_column("agent_app", "agent_workspace_status")
    op.drop_column("agent_app", "workspace_hash")
    op.drop_column("agent_app", "agent_dir")
    op.drop_index("ix_skill_asset_scope", table_name="skill_asset")
    op.drop_column("skill_asset", "scope")
```

### 3.2 数据回填

迁移后，对所有现有 AgentApp 行：

```python
# data migration 在 upgrade() 末尾或独立脚本
for app in session.exec(select(AgentApp)).all():
    if app.agent_dir is None:
        app.agent_dir = f"{settings.AGENTS_ROOT}/{app.id}/skills"
    app.agent_workspace_status = "pending"  # 启动时由 bootstrap 补建
session.commit()
```

---

## 4. 复制时机定义（核心逻辑）

### 4.1 publish 流程新增：Global → Agent 复制

```python
# app/api/v1/apps.py, 在 publish_agent_app 内

# 1. 原有逻辑（双段校验 + fingerprint + status='published'）保留
...

# 2. 新增：Global → Agent 复制
if app_cfg.skill_names:
    await materialize_for_agent(
        session,
        app_id=app_cfg.id,
        skill_names=list(app_cfg.skill_names),
    )
    logger.info("agent_workspace_materialized", app_id=app_cfg.id, skill_count=len(app_cfg.skill_names))

# 3. 新增：workspace_hash 计算
app_cfg.workspace_hash = compute_workspace_hash(app_cfg.agent_dir)
app_cfg.agent_workspace_status = "active"
session.commit()
```

### 4.2 新增端点：`/apps/{id}/associate-user/{uid}`

```python
# app/api/v1/apps.py

@router.post("/apps/{app_id}/associate-user/{user_id}", response_model=ApiResponse[None])
async def associate_user_with_app(
    app_id: int,
    user_id: int,
    db: DBSession = Depends(get_db_session),
    current_session: ChatSession = Depends(get_current_session),  # Phase 1 后改 get_current_user_with_session_id
) -> ApiResponse[None]:
    """把 AgentApp 关联到用户：触发 Agent + Global → User 复制。

    语义：用户首次绑定此 AgentApp 时调用；幂等（重复调用刷新 User 层内容）。
    """
    try:
        app_cfg = db.get(AgentApp, app_id)
        if app_cfg is None:
            raise HTTPException(404, "Agent app not found")
        if app_cfg.status != "published":
            raise HTTPException(422, "Agent app is not published")
        user = await db_service.get_user(user_id)
        if user is None:
            raise HTTPException(404, "User not found")

        # 聚合 Global（共享）+ Agent（专属）→ User 层
        await materialize_to_user_combined(
            session=db,
            user_id=str(user.id),
            app_id=app_id,
            global_skill_names=list(app_cfg.skill_names),  # User 层也保留 Global 副本（避免运行时读不到）
            agent_skill_names=_read_agent_dir_skill_names(app_cfg.agent_dir),
        )
        logger.info("agent_app_user_associated", app_id=app_id, user_id=user.id)
        return ApiResponse.success(None)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("agent_app_associate_user_failed", app_id=app_id, user_id=user_id)
        raise HTTPException(500, str(exc)) from exc
```

### 4.3 `skills_store.py` 新增函数

```python
# app/services/agents/skills_store.py

def _agent_skill_dir(app_id: int) -> Path:
    """返回 Agent 层 skill 目录：{AGENTS_ROOT}/<app_id>/skills/"""
    return settings.AGENTS_ROOT / str(app_id) / "skills"


def _agent_skill_file(app_id: int, name: str) -> Path:
    """返回 Agent 层 SKILL.md 路径。"""
    return _agent_skill_dir(app_id) / _validate_skill_name(name) / _SKILL_FILE_NAME


async def materialize_for_agent(
    session: Session, *, app_id: int, skill_names: Sequence[str]
) -> None:
    """把全局 skills 复制到 Agent 层；用于 publish 时。

    幂等：覆盖式写入（hash 不匹配时刷新）。
    """
    for name in skill_names:
        _validate_skill_name(name)
        body = await read_global(session, name)
        await asyncio.to_thread(_atomic_write, _agent_skill_file(app_id, name), body)
    logger.info("agent_skills_materialized", app_id=app_id, skill_count=len(list(skill_names)))


async def materialize_to_user_combined(
    session: Session, *, user_id: str, app_id: int, global_skill_names: Sequence[str], agent_skill_names: Sequence[str]
) -> None:
    """聚合 Global（共享）+ Agent（专属）→ User 层。

    合并去重后复制；Agent 层优先于 Global。
    """
    uid = _validate_user_id(user_id)
    # 合并去重（Agent 优先）
    merged: dict[str, Path] = {}
    for name in global_skill_names:
        merged[name] = _global_skill_file(name)
    for name in agent_skill_names:
        merged[name] = _agent_skill_file(app_id, name)
    for name, source_path in merged.items():
        if source_path.exists():
            body = await asyncio.to_thread(source_path.read_text, "utf-8")
            await asyncio.to_thread(_atomic_write, _user_skill_file(uid, name), body)
        else:
            logger.warning("user_materialize_source_missing", source=str(source_path))
    # 清理 User 层不再使用的 skill
    await asyncio.to_thread(_prune_stale_user_skills, uid, set(merged.keys()))
    logger.info("user_workspace_materialized_combined", user_id=uid, app_id=app_id, skill_count=len(merged))


def _read_agent_dir_skill_names(app_dir: Path) -> list[str]:
    """扫描 Agent 层目录，返回现有 skill 名字列表。"""
    if not app_dir.is_dir():
        return []
    return sorted(entry.name for entry in app_dir.iterdir() if entry.is_dir())


def compute_workspace_hash(agent_dir: Path) -> str:
    """计算 Agent 层内容指纹：所有 SKILL.md 的 sha256 拼接后再 sha256。"""
    if not agent_dir.is_dir():
        return hashlib.sha256(b"").hexdigest()
    file_hashes = []
    for path in sorted(agent_dir.rglob("SKILL.md")):
        content = path.read_bytes()
        file_hashes.append(hashlib.sha256(content).hexdigest())
    return hashlib.sha256("\n".join(file_hashes).encode("utf-8")).hexdigest()
```

### 4.4 既有函数复用 / 修改

| 函数 | 现状 | 变更 |
|---|---|---|
| `materialize_for_user(session, user_id, skill_names)` | 仅 Global → User | **保留**：供独立 skill 复制（无 Agent 上下文） |
| `sync_user_skills(session, user_id, associated_names)` | 重置 User 目录 | **保留**：与新函数并存，调用方决定使用 |
| `refresh_disk_from_db(session, name)` | Global 刷新 | **保留**：作用域限定为 Global |
| `read_global(session, name)` | Global 读取 | **保留** |
| `materialize_into_directory(session, target_dir, skill_names)` | 复制到任意目录 | **保留**：供 test_runner 等使用 |

---

## 5. fingerprint 锁定语义

### 5.1 Agent 层变更检测

| 触发场景 | fingerprint 重算？ |
|---|---|
| Global skill body 更新（`PATCH /skills/<name>` body） | 否（Agent 层在下次 publish 时刷新） |
| AgentApp publish 时 | ✅ 重算 Agent 层 + workspace_hash |
| AgentApp PATCH（编辑 system_prompt / allowed_tools / skill_names） | ✅ PATCH 后自动 publish 回退 draft（既有逻辑） |
| User 层文件被外部修改 | 否（User 层是消费端，不影响 fingerprint） |

### 5.2 启动校验（`bootstrap.py`）

```python
# app/services/agents/bootstrap.py

async def ensure_default_agent_workspace(session: Session) -> None:
    """为 default AgentApp 创建 Agent 层骨架（启动时调用）。

    所有已有 AgentApp 若 agent_workspace_status != 'active'：
    - 若 status='published' 且 agent_dir 为空 → 立即补建（同步）
    - 若 status='draft' → 仅创建空骨架目录
    """
    for app in session.exec(select(AgentApp)).all():
        if app.agent_workspace_status == "active":
            continue
        agent_dir = _agent_skill_dir(app.id)
        if app.status == "published" and not agent_dir.exists():
            await materialize_for_agent(
                session,
                app_id=app.id,
                skill_names=list(app.skill_names),
            )
            app.workspace_hash = compute_workspace_hash(agent_dir)
        agent_dir.mkdir(parents=True, exist_ok=True)  # 确保骨架存在
        app.agent_workspace_status = "active"
    session.commit()
    logger.info("agent_workspace_bootstrap_completed", total=...)
```

---

## 6. 运行时读取路径调整

### 6.1 Chatbot runtime（`app/services/agents/runtime.py`）

```python
# DeepAgentsAppRuntime._build_filesystem_backend 内（Phase 2 适配点）

# 旧：直接读 {SKILLS_ROOT}/users/<user_id>/<name>/SKILL.md
# 新：校验 User 层与 Agent 层 workspace_hash 一致后再使用

# 启动时一次性校验（启动期校验，不在请求热路径）
async def _validate_user_workspace(user_id: str, app_id: int) -> None:
    """启动校验：User 层 skill 与 (Global + Agent) 集合一致。

    不一致时记录 user_workspace_drift 警告日志，不阻断启动
    （请求热路径上有 lazy re-materialize 兜底）。
    """
    user_skills = _read_user_skill_names(user_id)
    expected = set(_read_agent_dir_skill_names(_agent_skill_dir(app_id)))
    if user_skills != expected:
        logger.warning("user_workspace_drift", user_id=user_id, app_id=app_id,
                      user_set=user_skills, expected_set=expected)
```

### 6.2 Test runner（`app/services/agents/test_runner.py`）

```python
# run_subagent_once 内

# 旧：materialize_into_directory(session, tmp_skills_root, skills)
# 新：materialize_into_directory 保持，但来源改为 (Global + Agent 层合并)
async def materialize_into_combined_directory(
    session: Session, target_dir: Path, *, app_id: int, skill_names: Sequence[str]
) -> None:
    """供 test_runner 用：聚合 Global + Agent 层 → 临时目录。"""
    for name in skill_names:
        agent_path = _agent_skill_file(app_id, name)
        source = agent_path if agent_path.exists() else _global_skill_file(name)
        if source.exists():
            body = await asyncio.to_thread(source.read_text, "utf-8")
            await asyncio.to_thread(_atomic_write, target_dir / name / _SKILL_FILE_NAME, body)
```

---

## 7. DoD

- [ ] alembic 迁移：`skill_asset.scope`、`agent_app.agent_dir`、`agent_app.workspace_hash`、`agent_app.agent_workspace_status`
- [ ] 数据回填：所有现有 AgentApp 的 `agent_dir` 填充、`agent_workspace_status='pending'`
- [ ] `skills_store.py` 新增 `_agent_skill_dir` / `_agent_skill_file` / `materialize_for_agent` / `materialize_to_user_combined` / `_read_agent_dir_skill_names` / `compute_workspace_hash` / `materialize_into_combined_directory`
- [ ] `apps.py` publish 流程新增 Global → Agent 复制步骤；`workspace_hash` 计算
- [ ] `apps.py` 新增 `POST /apps/{id}/associate-user/{uid}` 端点
- [ ] `bootstrap.py` 新增 `ensure_default_agent_workspace`，启动时自动补建 Agent 层
- [ ] `assembly.py` `compute_fingerprint` 新增 `workspace_hash` 字段
- [ ] `runtime.py` chatbot 装配时校验 User 层与 Agent 层 drift（启动期）
- [ ] `test_runner.py` 改用 `materialize_into_combined_directory`（Global + Agent）
- [ ] `app/core/config.py` 新增 `AGENTS_ROOT` 配置项（默认 `SKILLS_ROOT / "agents"`）
- [ ] 前端 `AgentList.vue`（已在 stub）扩展为支持"绑定到用户"操作（按钮 + confirm 对话框）
- [ ] 文档：
  - `docs/agentapp-manual-testing.md` 新增第 6.6 节"三级 Workspace 同步"
  - `docs/authentication.md` 第 7 节新增"Workspace 隔离"小节
  - 更新 `docs/architecture.md` 关于 Workspace 的章节

---

## 8. 验证

### 8.1 单元测试

- `tests/unit/services/test_skills_store.py`：
  - `test_materialize_for_agent_creates_files`
  - `test_materialize_to_user_combined_aggregates_global_and_agent`
  - `test_compute_workspace_hash_stable`
  - `test_agent_skill_overrides_global_in_combined`
- `tests/unit/api/test_apps.py`：
  - `test_publish_creates_agent_workspace`
  - `test_associate_user_copies_combined_skills`
  - `test_associate_user_idempotent`
- `tests/unit/services/test_bootstrap.py`：
  - `test_ensure_default_agent_workspace_migrates_pending`

### 8.2 集成测试

- `tests/integration/api/test_agent_workspace.py`：
  - 创建 Skill → 创建 AgentApp（含 skill）→ publish → 验证 Agent 层文件就位
  - associate-user → 验证 User 层 = Global + Agent 聚合
  - PATCH skill body → 重新 publish → 验证 Agent 层更新
  - 跨用户：user1 关联后 user2 关联 → User 层互不影响

### 8.3 手工冒烟

参考 `docs/agentapp-manual-testing.md` 第 6.6 节（待新增）。

---

## 9. 关键决策（详见 `open-questions.md` Q3/Q4）

- **Q3**：Agent 层路径用 `<app_id>` 还是 `<app_name>`？推荐 `<app_id>`（主键稳定，name 可改）。
- **Q4**：Agent 层与 Global 同名 skill 冲突时优先级？推荐 Agent 覆盖 Global（Agent 是发布时的快照）。
