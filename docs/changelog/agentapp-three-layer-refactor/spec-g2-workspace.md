# G2 Spec：Workspace 三级文件系统（v3.3 修订版）

> **主题**：引入 Agent 层；publish 时 Global→Agent 复制；关联用户时 (Global + Agent) → User 复制；User 层**嵌套**在 AgentApp 下；lazy 校验兜底；User Chat 读 User，Agent/SubAgent 测试读 Global+Agent。
> **关联文档**：
> - `overview.md`（路线图）
> - `files-risks.md`（文件清单 + 风险）
> - `spec-g2-review.md`（v3 修订版审查与决策记录）
> - `spec-g3-session.md` §12（G3 集成接口预留）
> **目标读者**：后端实施者
> **风险等级**：中（DB schema + 目录结构 + 发布流程 + Runtime cache）
> **估算工时**：2.5 周（含 service 层拆分 + 迁移脚本）
> **修订时间**：2026-08-26（v3.3 实现期澄清决策固化）
> **版本**：v3.3 修订版
> - v3.1 原则性错误修复：P0-1 user_id 显式传入、P0-3 PATCH 清空关联表、H1-1 缓存大小限制、H1-3 参数语义明确
> - v3.2 上下文歧义修复：A1-1 DoD 按 Phase 分组、A1-2 G2/G3 接口签名统一、A1-3 MVP 限制表述统一、A1-5 G3 JSON Schema 补充
> - v3.3 实现期澄清决策固化：Session 类型锁定同步 SQLModel、lazy 校验动态期望指纹算法、
>   compile_agent_app 移除编译期 User 层填充、移除启动预热、D6/D23 落点修订（详见 §13.4）
>
> **全局约定（v3.3）**：本文档所有伪代码基于 **SQLModel 同步 `Session`**（与代码库现状一致）；
> 函数保持 `async def`（文件 IO 经 `asyncio.to_thread`），`session.get/exec/commit` 为同步调用。

---

## 目录

1. [目标与非目标](#1-目标与非目标)
2. [目录结构设计](#2-目录结构设计)
3. [alembic 迁移设计](#3-alembic-迁移设计)
4. [复制时机定义（核心逻辑）](#4-复制时机定义核心逻辑)
5. [fingerprint 锁定语义](#5-fingerprint-锁定语义)
6. [运行时读取路径调整](#6-运行时读取路径调整)
7. [DoD（v3 修订版）](#7-dodv3-修订版)
8. [验证（v3 测试矩阵）](#8-验证v3-测试矩阵)
9. [service 层重构](#9-service-层重构)
10. [迁移收尾](#10-迁移收尾)
11. [文档更新清单](#11-文档更新清单)
12. [G3 集成 TODO](#12-g3-集成-todo)
13. [关键决策（Q3/Q4 + v3 新增决策）](#13-关键决策q3q4--v3-新增决策)

---

## 1. 目标与非目标

### 1.1 目标

1. **三级目录结构**：Global（共享基础）/ Agent（Agent 专属）/ User（用户个性化，**嵌套**在 AgentApp 下）
2. **复制时机定义**：
   - publish AgentApp 时 Global → Agent 层复制（**Hash 比对优化**：仅不一致时写）
   - 用户**首次**关联 AgentApp 时 (Global + Agent) → User 层复制（合并去重，Agent 覆盖 Global）
   - **Lazy 校验**：每个 session 启动时 `ensure_user_workspace_up_to_date`（增量同步）
   - 启动期 `ensure_all_agent_workspaces`（重命名自 `ensure_default_agent_workspace`，补 Agent 层 + workspace_hash）
3. **fingerprint 锁定**：`workspace_hash` 计算 Agent 层内容指纹；**不纳入** `compute_fingerprint`（与 `_load_skill_hashes` 冗余）
4. **PATCH 状态机（解读 B）**：`published → draft`（PATCH 后 `workspace_hash` 清空，需重新 publish 才生效）
5. **service 层重构**：业务逻辑下沉至 `agent_apps_service.py` / `agents_service.py`；API 层仅做参数校验
6. **兼容策略**：保留旧 `SKILLS_ROOT` env 一个大版本；启动期自动补建不阻断

### 1.2 非目标（不在本 Phase 范围）

- Agent 层 Skill UI 编辑（仅后端 schema + 复制逻辑；UI Phase 4 再做）
- Skill 文件的多版本快照（保留现有 `version` 字段即可）
- 跨 AgentApp 共享 Agent 层 skill（本版本**禁止**；详见 `open-questions.md` Q4 + `spec-g2-review.md` §1.5）
- 前端 `AgentList.vue` "绑定到用户" 操作（frontend stub 状态，G3 决定，G2 范围外）
- `AGENTS_ROOT` 独立配置项（MVP 简化，路径直接拼接，Phase 5+ 引入）
- `test_runner` MVP 阶段维持 Global-only（不读 Agent/User 层）；`materialize_into_combined_directory` 函数已实现（§4.3），G3+ 阶段调用方按需启用

---

## 2. 目录结构设计

### 2.1 当前 vs v3 提议

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

v3（三层，嵌套 User）：
{DATA_ROOT}/
  global/
    skills/                  # 全局共享（DB 真相源）
      <skill_name>/
        SKILL.md
  agents/
    <app_id>/                 # AgentApp 私有空间
      skills/                 # Agent 层（publish 时复制；hash 比对）
        <skill_name>/
          SKILL.md
      users/                  # User 层嵌套（per-(app, user) 真正隔离）
        <user_id>/
          skills/             # (Global + Agent) 聚合 + lazy 校验
            <skill_name>/
              SKILL.md
          sessions/           # G3 预留
  users/                      # 跨 app 共享空间（顶层保留，MVP 空）
    <user_id>/
```

### 2.2 关键路径变量（`app/core/config.py` 改造）

```python
# 旧
SKILLS_ROOT: Path = Path("./data/skills")

# v3 新增
DATA_ROOT: Path = Path("./data")                    # 一级根（v3 新增）
GLOBAL_SKILLS_ROOT: Path = DATA_ROOT / "global" / "skills"
AGENT_DIR_TEMPLATE: str = "{data_root}/agents/{app_id}"        # MVP 不抽配置，直接拼接
USER_SKILLS_TEMPLATE: str = "{data_root}/agents/{app_id}/users/{user_id}/skills"
SESSION_DIR_TEMPLATE: str = "{data_root}/agents/{app_id}/users/{user_id}/sessions"  # G3 预留
```

> **MVP 简化决策**：`AGENTS_ROOT` / `USERS_ROOT` 配置项**不引入**（原 spec 提议），路径直接拼接模板字符串即可。Phase 5+ 如需跨盘 / NFS 拆分存储，再独立化配置。

### 2.3 兼容策略：旧 `SKILLS_ROOT` 双轨期

```python
# app/core/config.py 兼容逻辑
SKILLS_ROOT: Path = Path("./data/skills")  # 旧变量保留
DATA_ROOT: Path = Path("./data")           # 新变量默认指向父目录

# 检测旧路径是否存在，决定是否启用双轨
if (SKILLS_ROOT / "global").exists() and not (DATA_ROOT / "global").exists():
    logger.warning(
        "skills_root_legacy_detected",
        legacy_root=str(SKILLS_ROOT),
        action="auto_migrate_via_migration_script",
    )
    # 启动期不自动迁移；由 scripts/migrate_workspace.py 一次性处理
```

**保留期**：1 个大版本（v3 上线后 1 个 minor 版本周期内清理）

### 2.4 读取路径策略

| 调用方 | 读取层 | 路径模板 |
|---|---|---|
| **SubAgent 测试**（`run_subagent_once`） | Global | `tmp_skills_root/<name>/SKILL.md`（MVP：仅 Global，combined 函数预留） |
| **User Chat**（`runtime.ainvoke`） | User 层 | `{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/<name>/SKILL.md` |
| **AgentApp 编辑预览**（前端） | DB body | 直接读取 DB body（不经文件系统） |
| **Agent 层文件**（publish 后） | Agent 层 | `{DATA_ROOT}/agents/<app_id>/skills/<name>/SKILL.md` |
| **Global skill 文件** | Global | `{DATA_ROOT}/global/skills/<name>/SKILL.md` |

---

## 3. alembic 迁移设计

### 3.1 新增字段

```python
# alembic/versions/<rev>_agent_workspace.py
def upgrade():
    # --- SkillAsset.scope ---
    op.add_column(
        "skill_asset",
        sa.Column("scope", sa.String(16), nullable=False, server_default="global"),
    )
    op.create_index("ix_skill_asset_scope", "skill_asset", ["scope"])

    # --- AgentApp.agent_dir ---
    op.add_column(
        "agent_app",
        sa.Column("agent_dir", sa.String(255), nullable=True),
    )

    # --- AgentApp.workspace_hash ---
    op.add_column(
        "agent_app",
        sa.Column("workspace_hash", sa.String(64), nullable=True),
    )

    # --- AgentApp.agent_workspace_status ---
    op.add_column(
        "agent_app",
        sa.Column(
            "agent_workspace_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
    )

    # --- user_agent_app_association 新表（v3 关键基础设施） ---
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
        sa.UniqueConstraint(
            "user_id", "agent_app_id", name="uq_user_agent_app"
        ),
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


def downgrade():
    op.drop_table("user_agent_app_association")
    op.drop_column("agent_app", "agent_workspace_status")
    op.drop_column("agent_app", "workspace_hash")
    op.drop_column("agent_app", "agent_dir")
    op.drop_index("ix_skill_asset_scope", table_name="skill_asset")
    op.drop_column("skill_asset", "scope")
```

### 3.2 数据回填

```python
# alembic 迁移 upgrade() 末尾或独立脚本（推荐独立：scripts/migrate_workspace.py）
for app in session.exec(select(AgentApp)).all():
    if app.agent_dir is None:
        app.agent_dir = f"{settings.DATA_ROOT}/agents/{app.id}"
    app.agent_workspace_status = "pending"  # 启动期由 bootstrap 补建
session.commit()
```

### 3.3 新增 Model 字段

| 表 | 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|---|
| `skill_asset` | `scope` | `String(16)` | `'global'` | 默认 global；Phase 5+ 支持 `scope='agent'` |
| `agent_app` | `agent_dir` | `String(255)` | NULL | 物理路径模板基址 |
| `agent_app` | `workspace_hash` | `String(64)` | NULL | Agent 层内容指纹（sha256 hex） |
| `agent_app` | `agent_workspace_status` | `String(16)` | `'pending'` | `pending` / `active`（v3 简化） |

### 3.4 新增表 `user_agent_app_association`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `Integer PK` | 主键 |
| `user_id` | `Integer FK(user.id, ON DELETE CASCADE)` | 用户外键 |
| `agent_app_id` | `Integer FK(agent_app.id, ON DELETE CASCADE)` | AgentApp 外键 |
| `last_synced_workspace_hash` | `String(64)` | 上次同步时的 Agent workspace_hash（增量同步优化） |
| `associated_at` | `DateTime` | 关联时间 |
| 约束 | `UNIQUE(user_id, agent_app_id)` | 联合唯一 |

**CASCADE 策略**：
- 删 `user`：自动清理其所有关联
- 删 `agent_app`：自动清理其所有用户关联 + Agent 层 + User 层文件（由 service 层连锁）

---

## 4. 复制时机定义（核心逻辑）

### 4.1 publish 流程：Global → Agent 复制

```python
# app/services/agents/agent_apps_service.py

async def publish_agent_app(
    session: Session,
    *,
    app_cfg: AgentApp,
    current_user_id: int,
) -> AgentApp:
    """发布 AgentApp：双段校验 + Global → Agent 复制 + workspace_hash 计算。

    业务编排（API 层仅做参数校验）。
    """
    # 1. 既有逻辑（双段校验 + status='published'）保留
    await _validate_publish_prerequisites(session, app_cfg, current_user_id)

    # 2. v3 新增：Global → Agent 复制（Hash 比对）
    if app_cfg.skill_names:
        await materialize_for_agent(
            session,
            app_id=app_cfg.id,
            skill_names=list(app_cfg.skill_names),
        )

    # 3. v3 新增：计算 workspace_hash
    # 注意：传入 _agent_skill_dir（skills/ 子目录），不是 _agent_dir（会包含 users/）
    agent_skill_dir = _agent_skill_dir(app_cfg.id)
    app_cfg.workspace_hash = compute_workspace_hash(agent_skill_dir)
    app_cfg.agent_workspace_status = "active"

    # 5. 更新关联表的 last_synced_workspace_hash（无效化缓存）
    await _invalidate_user_layer_cache(session, app_cfg)

    session.commit()
    logger.info(
        "agent_app_published",
        app_id=app_cfg.id,
        workspace_hash=app_cfg.workspace_hash,
        skill_count=len(app_cfg.skill_names or []),
    )
    return app_cfg
```

### 4.2 关联用户：(Global + Agent) → User 层复制

```python
# app/services/agents/agent_apps_service.py

async def associate_user_with_app(
    session: Session,
    *,
    user_id: int,
    app_id: int,
    current_user_id: int,
) -> None:
    """首次关联：(Global + Agent) → User 层复制。

    业务编排（API 层仅做参数校验）。
    """
    # 1. 参数与状态校验
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        raise AgentAppNotFoundError(app_id=app_id)
    if app_cfg.status != "published":
        raise AgentAppNotPublishedError(app_id=app_id)
    user = db_service.get_user(session, user_id)
    if user is None:
        raise UserNotFoundError(user_id=user_id)

    # 2. 幂等关联（重复调用刷新 User 层）
    assoc = await _get_or_create_association(
        session, user_id=user_id, app_id=app_id
    )

    # 3. v3 新签名：聚合 (Global + Agent) → User 层（不再传 global_skill_names 与 agent_skill_names）
    subagent_cfgs = await agents_service.list_subagent_cfgs(
        session, app_id=app_id, skill_names=app_cfg.skill_names or []
    )
    await materialize_to_user_combined(
        session=session,
        app_cfg=app_cfg,
        user_id=user_id,  # v3 修复：显式传入 user_id
        subagent_cfgs=subagent_cfgs,
    )

    # 4. 更新关联表
    assoc.last_synced_workspace_hash = app_cfg.workspace_hash
    session.commit()

    logger.info(
        "user_app_associated",
        user_id=user_id,
        app_id=app_id,
        workspace_hash=app_cfg.workspace_hash,
    )
```

### 4.3 `skills_store.py` 新增函数

```python
# app/services/agents/skills_store.py

# ====== v3 路径 helpers ======

def _agent_dir(app_id: int) -> Path:
    """返回 AgentApp 私有空间根目录：{DATA_ROOT}/agents/<app_id>/"""
    return settings.DATA_ROOT / "agents" / str(app_id)


def _agent_skill_dir(app_id: int) -> Path:
    """返回 Agent 层 skill 目录：{DATA_ROOT}/agents/<app_id>/skills/"""
    return _agent_dir(app_id) / "skills"


def _agent_skill_file(app_id: int, name: str) -> Path:
    """返回 Agent 层 SKILL.md 路径。"""
    return _agent_skill_dir(app_id) / _validate_skill_name(name) / _SKILL_FILE_NAME


def _user_skill_dir(app_id: int, user_id: int) -> Path:
    """返回 User 层 skill 目录：{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/"""
    return _agent_dir(app_id) / "users" / str(user_id) / "skills"


def _user_skill_file(app_id: int, user_id: int, name: str) -> Path:
    """返回 User 层 SKILL.md 路径。"""
    return _user_skill_dir(app_id, user_id) / _validate_skill_name(name) / _SKILL_FILE_NAME


# ====== 复制函数（v3 全部带 hash 比对） ======

async def materialize_for_agent(
    session: Session, *, app_id: int, skill_names: Sequence[str]
) -> None:
    """把 Global skills 复制到 Agent 层；用于 publish 时。

    幂等：覆盖式写入（仅 hash 不一致时写）。
    """
    target_dir = _agent_skill_dir(app_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name in skill_names:
        _validate_skill_name(name)
        body = await read_global(session, name)
        target = target_dir / name / _SKILL_FILE_NAME
        if await _hash_compare_or_write(target, body):
            written += 1
    logger.info(
        "agent_skills_materialized",
        app_id=app_id,
        skill_count=len(list(skill_names)),
        files_written=written,
    )


async def materialize_to_user_combined(
    session: Session, *,
    app_cfg: AgentApp,
    user_id: int,  # v3 修复：显式传入 user_id（从 API 层 get_current_user 获取）
    subagent_cfgs: Sequence[SubAgentConfig],
) -> None:
    """聚合 (Global + Agent) → User 层（v3 新签名：直接传 app_cfg 与 subagent_cfgs）。

    合并去重（Agent 覆盖 Global）；hash 比对；prune 过期 skill。

    Args:
        session: 数据库会话
        app_cfg: AgentApp 配置
        user_id: 用户 ID（从 API 层 get_current_user 获取，确保权限校验）
        subagent_cfgs: SubAgent 配置列表
    """
    effective_skill_names = sorted(
        set(app_cfg.skill_names or [])
        | {n for cfg in subagent_cfgs for n in (cfg.skill_names or [])}
    )
    target_dir = _user_skill_dir(app_cfg.id, user_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for name in effective_skill_names:
        agent_path = _agent_skill_file(app_cfg.id, name)
        source = agent_path if agent_path.exists() else _global_skill_file(name)
        if source.exists():
            body = await asyncio.to_thread(source.read_text, "utf-8")
            target = target_dir / name / _SKILL_FILE_NAME
            if await _hash_compare_or_write(target, body):
                written += 1
        else:
            logger.warning(
                "user_materialize_source_missing",
                source=str(source),
                app_id=app_cfg.id,
                user_id=user_id,
            )

    # 清理 User 层不在 effective_skill_names 中的子目录
    await _prune_stale_user_skills(target_dir, set(effective_skill_names))

    logger.info(
        "user_workspace_materialized_combined",
        user_id=user_id,
        app_id=app_cfg.id,
        skill_count=len(effective_skill_names),
        files_written=written,
    )


async def materialize_into_combined_directory(
    session: Session,
    target_dir: Path,
    *,
    app_id: int,
    skill_names: Sequence[str],
) -> None:
    """供 test_runner 用：聚合 (Global + Agent) → 临时目录。

    MVP：standalone runner 仅调用此函数（Global only）；
    真正的 combined 调用由 G3+ 阶段启用。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in skill_names:
        agent_path = _agent_skill_file(app_id, name)
        source = agent_path if agent_path.exists() else _global_skill_file(name)
        if source.exists():
            body = await asyncio.to_thread(source.read_text, "utf-8")
            await asyncio.to_thread(
                _atomic_write, target_dir / name / _SKILL_FILE_NAME, body
            )


# ====== 哈希工具（v3 新增） ======

async def _hash_compare_or_write(target: Path, body: str) -> bool:
    """hash 比对：source_hash vs existing_hash，仅不一致时写。

    Returns: True 表示写入了文件；False 表示跳过。
    """
    new_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if target.exists():
        existing_hash = hashlib.sha256(
            await asyncio.to_thread(target.read_bytes)
        ).hexdigest()
        if existing_hash == new_hash:
            return False
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_atomic_write, target, body)
    return True


# ====== workspace_hash 计算（v3 新增） ======

def compute_workspace_hash(agent_skill_dir: Path) -> str:
    """计算 Agent 层 skill 目录的内容指纹。

    Args:
        agent_skill_dir: Agent 层 skill 目录，应传入 _agent_skill_dir(app_id)，
                        即 {DATA_ROOT}/agents/<app_id>/skills/
                        注意：不要传入 _agent_dir(app_id)（会包含 users/ 子目录）

    Returns:
        sha256 hex 指纹（所有 SKILL.md 的 sha256 拼接后再 sha256）
    """
    if not agent_skill_dir.is_dir():
        return hashlib.sha256(b"").hexdigest()
    file_hashes: list[str] = []
    # 仅搜索 agent_skill_dir 下的 SKILL.md，不递归到 users/ 子目录
    for path in sorted(agent_skill_dir.glob(_SKILL_FILE_NAME)):
        content = path.read_bytes()
        file_hashes.append(hashlib.sha256(content).hexdigest())
    return hashlib.sha256("\n".join(file_hashes).encode("utf-8")).hexdigest()


def _compute_user_workspace_hash(user_dir: Path) -> str:
    """计算 User 层内容指纹（lazy 校验用）。"""
    if not user_dir.is_dir():
        return hashlib.sha256(b"").hexdigest()
    file_hashes: list[str] = []
    for path in sorted(user_dir.rglob(_SKILL_FILE_NAME)):
        content = path.read_bytes()
        file_hashes.append(hashlib.sha256(content).hexdigest())
    return hashlib.sha256("\n".join(file_hashes).encode("utf-8")).hexdigest()


def _compute_effective_workspace_hash(
    app_id: int, effective_skill_names: Sequence[str]
) -> str:
    """计算期望指纹（v3.3 新增）：按 effective_skill_names 逐名解析期望源文件。

    源解析规则与 materialize_to_user_combined 完全一致（Agent 覆盖 Global）：
    - Agent 层文件存在 → 以 Agent 层为期望源
    - 否则 → 以 Global 层为期望源
    - 两者皆缺失 → 该 name 贡献空串占位（保持位置稳定，指纹可区分缺失）

    背景：User 层文件集 = effective_skill_names（App ∪ SubAgent），可能大于
    Agent 层快照（SubAgent 可拥有 App 之外的 skill），因此不能直接比对
    Agent 层 workspace_hash —— 否则永不相等、每次触发同步。
    """
    file_hashes: list[str] = []
    for name in sorted(effective_skill_names):
        agent_path = _agent_skill_file(app_id, name)
        source = agent_path if agent_path.exists() else _global_skill_file(name)
        if source.exists():
            file_hashes.append(hashlib.sha256(source.read_bytes()).hexdigest())
        else:
            file_hashes.append("")
    return hashlib.sha256("\n".join(file_hashes).encode("utf-8")).hexdigest()


# ====== Lazy 校验（v3.3 动态期望指纹算法） ======

async def ensure_user_workspace_up_to_date(
    session: Session, *,
    user_id: int,
    app_id: int,
) -> bool:
    """Lazy 校验：session 启动入口调用（v3.3 动态期望指纹算法）。

    比对算法：动态计算期望指纹（逐名解析 Agent 层优先、缺失回退 Global 层）
    vs User 层实际指纹。不直接比对 app_cfg.workspace_hash —— 因 SubAgent 可
    拥有 App 之外的 skill（User 层文件集 = effective_skill_names 并集，可能
    大于 Agent 层快照），直接比对会导致永不相等、每次触发同步。

    Returns: True 表示执行了重新复制；False 表示 hash 命中跳过 / 静默跳过
             （app 或 association 不存在时静默 False，不抛异常）。
    """
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        logger.warning("lazy_validate_app_not_found", app_id=app_id)
        return False

    assoc = await _get_association(session, user_id=user_id, app_id=app_id)
    if assoc is None:
        # 未关联用户：User 层尚不存在，跳过（首次复制由 associate-user 端点负责）
        logger.debug("lazy_validate_assoc_not_found", user_id=user_id, app_id=app_id)
        return False

    subagent_cfgs = await agents_service.list_subagent_cfgs(
        session, app_id=app_id, skill_names=app_cfg.skill_names or []
    )
    effective_skill_names = sorted(
        set(app_cfg.skill_names or [])
        | {n for cfg in subagent_cfgs for n in (cfg.skill_names or [])}
    )

    expected_hash = _compute_effective_workspace_hash(app_id, effective_skill_names)
    current_hash = _compute_user_workspace_hash(_user_skill_dir(app_id, user_id))

    if current_hash == expected_hash:
        return False  # 命中，跳过

    # 不一致 → 增量同步（幂等：hash 比对仅写差异，prune 过期子目录）
    await materialize_to_user_combined(
        session=session,
        app_cfg=app_cfg,
        user_id=user_id,
        subagent_cfgs=subagent_cfgs,
    )
    assoc.last_synced_workspace_hash = app_cfg.workspace_hash
    session.commit()
    logger.info(
        "user_workspace_lazy_synced",
        user_id=user_id,
        app_id=app_id,
        workspace_hash=app_cfg.workspace_hash,
    )
    return True
```

### 4.4 既有函数复用 / 修改

| 函数 | 现状 | v3 变更 |
|---|---|---|
| `materialize_for_user(session, user_id, skill_names)` | 仅 Global → User | **保留**：供独立 skill 复制（无 Agent 上下文） |
| `sync_user_skills(session, user_id, associated_names)` | 重置 User 目录 | **保留（历史兼容）**：G2 起 `compile_agent_app` 不再调用它（v3.3，§6.1.3）；User 层填充统一由 lazy 校验的 `materialize_to_user_combined` 负责 |
| `refresh_disk_from_db(session, name)` | Global 刷新 | **保留**：作用域限定为 Global |
| `read_global(session, name)` | Global 读取 | **保留** |
| `materialize_into_directory(session, target_dir, skill_names)` | 复制到任意目录 | **保留**：供 test_runner 等使用 |

### 4.5 `_read_agent_dir_skill_names` 否决（v3 关键删除）

**否决理由**：与 `app_cfg.skill_names` 冗余。
- 原 spec 设计：从 `agent_dir` 扫描目录获取 skill 列表
- v3 修订：直接使用 `app_cfg.skill_names`（DB 真相源），无需扫描目录
- 删除后：所有调用方改为 `app_cfg.skill_names` 直接读取

---

## 5. fingerprint 锁定语义

### 5.1 PATCH 状态机（解读 B · v3 新增）

```
发布流：
draft ──publish──> published ──PATCH(skill_names/system_prompt/...)──> draft
                          │                                              │
                          │                                              └─workspace_hash 清空
                          │                                              └─User 层进入过期状态
                          │                                                  (lazy 校验或下次 publish 修复)
                          └─workspace_hash 保留

启动期补建：
pending ──ensure_all_agent_workspaces──> active
```

**关键决策**（v3 修订版）：
- ✅ **PATCH 解读 B**：published → draft（需重新 publish 才生效）
- ✅ PATCH 后 `workspace_hash = NULL`
- ✅ PATCH 后 `agent_workspace_status = 'pending'`
- ✅ PATCH 后失效所有关联用户的 `last_synced_workspace_hash`（v3 修复：P0-3）
  - 调用 `_invalidate_user_layer_cache(session, app_cfg)` 清空关联表
  - v3.3 说明：drift 检测由动态期望指纹 vs User 层实际指纹承担（§4.3），
    `last_synced_workspace_hash` 仅作同步状态记录 / 观测用途
- ✅ 启动期 `ensure_all_agent_workspaces` **同时校验已 active 的 App**（防目录丢失）
- ✅ draft 状态保持 pending（不自动转 active）
- ✅ published 状态必要时 rematerialize（Agent 层目录丢失场景）
- ✅ 单 App 异常 try/except 隔离（不阻断其他 App）

### 5.2 变更检测矩阵

| 触发场景 | workspace_hash 重算？ | last_synced_workspace_hash | 备注 |
|---|---|---|---|
| Global skill body 更新（`PATCH /skills/<name>`） | 否 | 不变 | Agent 层在下次 publish 时刷新 |
| AgentApp publish 时 | ✅ | 清空所有关联用户的值 | 重算 Agent 层 + workspace_hash；触发 lazy 校验 |
| AgentApp PATCH（编辑 skill_names / system_prompt 等） | 清空（NULL） | 清空所有关联用户的值 | PATCH 后变 draft；下次 publish 重算 |
| User 层文件被外部修改 | 否 | 不变 | User 层是消费端，不影响 fingerprint |
| Lazy 校验发现 drift | 重新复制 User 层 | 更新为当前 workspace_hash | 不影响 workspace_hash |

### 5.3 `compute_fingerprint` 不纳入 workspace_hash（v3 否决项）

**否决理由**：两者目的不同，不纳入是合理的。

- `fingerprint`：用于 runtime cache key 校验，包含 AgentApp 配置（system_prompt、allowed_tools、model 等）
- `workspace_hash`：用于 User 层同步校验，仅包含 Agent 层 skill 文件内容
- 当 `workspace_hash` 变化时，`skill_names` 等配置字段通常也会变化，fingerprint 已随之变化
- 已有 `_load_skill_hashes` 监控 skill body 变更，无需重复纳入

```python
# app/services/agents/assembly.py
# 维持原签名（5 输入字段），不新增 workspace_hash

_APP_FIELDS = (
    "name", "system_prompt", "allowed_tools", "model",
    "skill_names", "subagent_names", "interrupt_on", "engine",
)

def compute_fingerprint(
    app_cfg: AgentApp,
    subagent_cfgs: Sequence[SubAgentConfig],
    skill_hashes: Mapping[str, str],
    mcp_fingerprint: str,
    model_fingerprint: str,
) -> str:
    """计算 AgentApp fingerprint（用于 cache key 校验）。

    v3.3：伪代码对齐现状实现（5 输入）。
    不纳入 workspace_hash —— 已有 _load_skill_hashes 监控 skill body 变更；
    workspace_hash 变化时 fingerprint 已变化（skill_names 等也会变）。
    """
    ...
```

### 5.4 启动期校验（`bootstrap.py` 重命名）

```python
# app/services/agents/bootstrap.py

async def ensure_all_agent_workspaces(session: Session) -> None:
    """启动期补建：遍历所有 AgentApp，确保 Agent 层就位 + workspace_hash 准确。

    v3 重命名（原 ensure_default_agent_workspace → ensure_all_agent_workspaces）。
    """
    apps = session.exec(select(AgentApp)).all()
    active_count = 0
    skipped_count = 0

    for app in apps:
        try:
            agent_dir = _agent_skill_dir(app.id)

            if app.status == "draft":
                # draft 状态保持 pending，仅创建空骨架
                agent_dir.mkdir(parents=True, exist_ok=True)
                skipped_count += 1
                continue

            # published 状态：确保 Agent 层有内容
            if not agent_dir.exists() or not any(agent_dir.iterdir()):
                if app.skill_names:
                    await materialize_for_agent(
                        session,
                        app_id=app.id,
                        skill_names=list(app.skill_names),
                    )

            # 已 active 状态仍校验（防目录丢失）
            if app.agent_workspace_status == "active":
                expected_hash = compute_workspace_hash(agent_dir)
                if app.workspace_hash != expected_hash:
                    logger.warning(
                        "agent_workspace_hash_drift",
                        app_id=app.id,
                        stored=app.workspace_hash,
                        expected=expected_hash,
                    )
                    app.workspace_hash = expected_hash

            # pending → active
            app.agent_workspace_status = "active"
            active_count += 1
        except Exception as exc:
            # 单 App 异常隔离
            logger.exception(
                "agent_workspace_bootstrap_failed",
                app_id=app.id,
                error=str(exc),
            )
            continue

    session.commit()
    logger.info(
        "agent_workspace_bootstrap_completed",
        total=len(apps),
        active=active_count,
        skipped=skipped_count,
    )
```

### 5.5 `_validate_user_workspace` 否决（v3 否决项）

**否决理由**：与 lazy 校验 + 启动期校验职责重叠。

- 原 spec 设计：chatbot 装配时校验 User 层与 Agent 层 drift
- v3 修订：双层覆盖已足够
  - 启动期 `ensure_all_agent_workspaces` 校验 Agent 层完整性
  - session 启动 `ensure_user_workspace_up_to_date` lazy 校验 User 层
- 删除后：chatbot 调用 `get_runtime` 时**仅**调用 lazy 校验，不做额外启动校验

---

## 6. 运行时读取路径调整

### 6.1 Chatbot runtime（`app/services/agents/runtime.py` v3 重构）

#### 6.1.1 删除 `_COMPILE_USER_ID = "system"` 临时简化

```python
# app/services/agents/runtime.py

# 旧（v3 删除）
# _COMPILE_USER_ID = "system"
# Phase-1 deviation: all assets are globally shared...
# Phase-2 upgrade path: once per-user asset ownership exists, thread the real
# requesting user id through ``get_runtime`` -> ``get_or_compile``

# v3：真实 user_id 透传 + H1-1 缓存优化
async def get_runtime(
    session: Session,
    app_id: int,
    *,
    user_id: int,  # v3 必传
) -> AgentAppRuntime:
    """获取 runtime（per-(app_id, user_id) 隔离）。"""
    # 1. Lazy 校验 User 层（v3 新增）
    await ensure_user_workspace_up_to_date(
        session, user_id=user_id, app_id=app_id
    )

    # 2. 计算 fingerprint（5 输入，与 assembly.compute_fingerprint 现状签名一致，§5.3）
    app_cfg = session.get(AgentApp, app_id)
    fingerprint = compute_fingerprint(
        app_cfg, subagent_cfgs, skill_hashes, mcp_fingerprint, model_fingerprint
    )

    # 3. 三元组 cache key
    cache_key = (app_id, user_id, fingerprint)
    cached = _runtime_cache.get(cache_key)
    if cached is not None:
        # 更新 last_accessed（LRU）
        cached.last_accessed = time.time()
        return cached.runtime

    # 4. 编译并缓存
    runtime_obj = await get_or_compile(
        session,
        app_cfg,
        user_id=user_id,
    )

    # 5. 淘汰过期和满容量条目（H1-1）
    _evict_stale_entries()
    _evict_oldest_if_full()

    # 6. 存入缓存
    now = time.time()
    _runtime_cache[cache_key] = _CacheEntry(
        runtime=runtime_obj,
        created_at=now,
        last_accessed=now,
    )

    # 7. 淘汰同 (app_id, user_id) 的旧缓存
    for stale_key in [
        key for key in _runtime_cache
        if key[0] == app_id and key[1] == user_id and key != cache_key
    ]:
        del _runtime_cache[stale_key]

    return runtime_obj
```

#### 6.1.2 `_runtime_cache` 三元组 key（v3 关键变更 + H1-1 修复）

```python
# app/services/agents/runtime.py

import time
from dataclasses import dataclass

# 缓存配置
_RUNTIME_CACHE_MAX_SIZE = 1000  # 最大缓存条目数
_RUNTIME_CACHE_TTL = 3600  # 缓存过期时间（秒）

@dataclass
class _CacheEntry:
    """缓存条目（包含 runtime 和时间戳）。"""
    runtime: AgentAppRuntime
    created_at: float  # time.time()
    last_accessed: float  # time.time()（LRU 用）

# 旧（v3 升级）
# _runtime_cache: dict[tuple[int, str], AgentAppRuntime] = {}

# v3：三元组 key + 大小限制 + TTL
_runtime_cache: dict[tuple[int, int, str], _CacheEntry] = {}
#                       app_id, user_id, fingerprint

def _evict_stale_entries() -> None:
    """淘汰过期条目（TTL）。"""
    now = time.time()
    stale_keys = [
        key for key, entry in _runtime_cache.items()
        if now - entry.created_at > _RUNTIME_CACHE_TTL
    ]
    for key in stale_keys:
        del _runtime_cache[key]

def _evict_oldest_if_full() -> None:
    """如果缓存满，淘汰最久未访问的条目（LRU）。"""
    if len(_runtime_cache) >= _RUNTIME_CACHE_MAX_SIZE:
        # 按 last_accessed 排序，淘汰最旧的
        oldest_key = min(_runtime_cache, key=lambda k: _runtime_cache[k].last_accessed)
        del _runtime_cache[oldest_key]

# 淘汰策略：按 (app_id, user_id) 维度，保留新 fingerprint
def evict_runtime_cache(app_id: int, user_id: int) -> None:
    """淘汰指定 (app_id, user_id) 的所有旧缓存。"""
    for stale_key in [
        key for key in _runtime_cache
        if key[0] == app_id and key[1] == user_id
    ]:
        del _runtime_cache[stale_key]
```

#### 6.1.3 `compile_agent_app` FilesystemBackend v3 路径

```python
# app/services/agents/assembly.py

async def compile_agent_app(
    session: Session,
    app_cfg: AgentApp,
    *,
    user_id: int,  # v3 新增必传
) -> AgentAppRuntime:
    """编译 AgentApp runtime（v3 nested 路径）。"""
    # ... 既有逻辑（v3.3：不再调用 sync_user_skills；
    #     User 层填充由 get_runtime 前置 lazy 校验负责）...

    # v3 关键变更：FilesystemBackend 路径改为 nested user 层
    user_skill_root = (
        Path(settings.DATA_ROOT) / "agents" / str(app_cfg.id) / "users" / str(user_id)
    )
    backend = FilesystemBackend(root_dir=str(user_skill_root))
    # ... 其余编译逻辑 ...
```

#### 6.1.4 `get_or_compile` 接受 `user_id` 透传

```python
# app/services/agents/assembly.py

async def get_or_compile(
    session: Session,
    app_cfg: AgentApp,
    *,
    user_id: int,  # v3 透传
) -> AgentAppRuntime:
    """获取或编译 runtime（v3 接受 user_id）。"""
    # 调用 compile_agent_app 时传入 user_id
    return await compile_agent_app(session, app_cfg, user_id=user_id)
```

#### 6.1.5 启动预热策略（v3.3 决策）

`main.py` `_warm_agent_apps` 原在启动期调用 `get_runtime` 预编译图。v3 起 `get_runtime`
的 `user_id` 必填且 cache 按 `(app_id, user_id)` 隔离，启动期无真实用户上下文，
原预热语义不再成立。

**v3.3 决策**：
- ✅ **移除** `_warm_agent_apps` 中的 `get_runtime` 预热调用（连同该函数一并删除）
- ✅ lifespan 仅调用 `bootstrap.ensure_all_agent_workspaces`（Agent 层补建 + workspace_hash 校验，§5.4）
- ✅ 首个用户请求现场编译并缓存（冷启动代价由首个请求承担，通常数秒）
- ❌ 不引入按关联表遍历 (app, user) 对预热（启动时间随关联数线性增长，否决）
- ❌ 不允许 `user_id=None` 跳过校验的兼容模式（违背 v3「user_id 必真实」决策，否决）

### 6.2 Test runner（`app/services/agents/test_runner.py`）

> **v3.3 标注**：下方样例为 G3+ 未来形态；G2 阶段 `run_subagent_once` **不改签名**，
> 仅 docstring 追加 MVP 限制说明（D24），维持 `materialize_into_directory` Global-only 读取。

```python
# app/services/agents/test_runner.py

async def run_subagent_once(
    session: Session,
    subagent_cfg: SubAgentConfig,
    *,
    user_id: int,
    app_id: int,  # v3 新增
) -> SubAgentTestTrace:
    """运行 SubAgent 测试（MVP：仅读 Global 层）。"""
    # MVP 简化：standalone runner 仅读 Global，不强制 combined
    # 真正的 combined 调用由 G3+ 阶段启用
    await materialize_into_directory(
        session, tmp_skills_root, list(subagent_cfg.skill_names or [])
    )
    # ... 既有逻辑 ...
```

> **MVP 限制说明**：G2 阶段 test_runner 维持 Global-only；`materialize_into_combined_directory` 函数已实现（§4.3），调用方后续按需启用。

### 6.3 Chatbot 调用方调整（v3.3 落点重定向）

> **v3.3 事实修正**：`app/api/v1/chatbot.py` 已于 G1 阶段退役（空 router，无业务端点），
> 本节原「chat 端点传 user_id」的落点不存在。

**真实调用链（v3.3）**：
1. 启动期：`main.py` lifespan 仅调用 `ensure_all_agent_workspaces`（Agent 层补建；
   移除原 `_warm_agent_apps` 预热，§6.1.5）
2. 请求热路径：G3 session API（`spec-g3-session.md` §12）在 session 创建 / 启动入口
   调用 `ensure_user_workspace_up_to_date`，随后调用 `get_runtime`
3. 首个用户请求现场编译并缓存三元组 cache（冷启动代价由首个请求承担）

```python
# G3 session API（未来形态示意；G2 阶段无 API 改动）
runtime = await get_runtime(
    session,
    app_id=app_id,
    user_id=current_user.id,  # v3 必传真实 user_id
)
```

### 6.4 影响下游模块清单

| 模块 | 变更类型 | 备注 |
|---|---|---|
| `assembly.compile_agent_app` | 路径改造 | nested user 路径 |
| `assembly.get_or_compile` | 签名扩展 | 接受 `user_id` |
| `runtime._runtime_cache` | key 类型扩展 | tuple → 三元组 |
| `runtime.get_runtime` | 签名扩展 | 接受 `user_id` |
| `runtime._COMPILE_USER_ID` | 删除 | 真实 user_id 替代 |
| `main.py` lifespan | 新增 `ensure_all_agent_workspaces` | 启动期补建 |
| `test_runner.run_subagent_once` | docstring 更新 | MVP 限制说明 |
| `chatbot.ainvoke` | 调用方传入 user_id | per-(app, user) 隔离 |

---

## 7. DoD（v3.2 修订版）

> 详见 `spec-g2-review.md` §6.2 详细否决/修订理由。
> **A1-1 修复**：按 Phase 分组，明确依赖关系和实施顺序。

### 7.1 接受项（26 项 · 按 Phase 分组）

**Phase 0: 数据库基础设施**（阻塞后续所有 Phase）
- [x] **D1**: alembic 迁移：`skill_asset.scope`、`agent_app.agent_dir`、`agent_app.workspace_hash`、`agent_app.agent_workspace_status`（含 `user_agent_app_association` 新表）
- [x] **D2**: 数据回填：所有现有 AgentApp 的 `agent_dir` 填充、`agent_workspace_status='pending'`

**Phase 1: skills_store 扩展**（依赖 Phase 0，不阻塞 API 层）
- [x] **D3**: `skills_store.py` 新增 v3 路径 helpers（`_agent_dir` / `_agent_skill_dir` / `_user_skill_dir`）
- [x] **D4**: `skills_store.py` 新增复制函数（`materialize_for_agent` / `materialize_to_user_combined` v3 新签名 / `materialize_into_combined_directory`）
- [x] **D5**: `skills_store.py` 新增 hash 工具（`_hash_compare_or_write`）+ workspace_hash 计算（`compute_workspace_hash` / `_compute_user_workspace_hash` / `_compute_effective_workspace_hash` v3.3）
- [x] **D7**: `skills_store.py` 新增 prune（`_prune_stale_user_skills`）

**Phase 2: service 层重构**（依赖 Phase 1）
- [x] **D6**: lazy 校验 `ensure_user_workspace_up_to_date` 实现于 `agent_apps_service.py`（v3.3 归属修订：自 Phase 1 skills_store 移入；算法见 §4.3 动态期望指纹，hash 工具由 skills_store 提供）
- [x] **D10**: `agent_apps_service.py` 新建（业务编排：`publish_agent_app` / `associate_user_with_app` / `delete_agent_app` / `patch_agent_app`）
- [x] **D11**: `agents_service.py` 新建（`list_subagent_cfgs` / `validate_subagent_skill_visibility`）
- [x] **D12**: `db_service.py` 新增 association CRUD（`_get_or_create_association` / `_get_association` / `_invalidate_user_layer_cache`）

**Phase 3: API 层调整**（依赖 Phase 2）
- [x] **D8**: `apps.py` publish 流程新增 Global → Agent 复制步骤；`workspace_hash` 计算
- [x] **D9**: `apps.py` 新增 `POST /apps/{id}/associate-user/{uid}` 端点（参数校验）
- [x] **D23**: 落点重定向（v3.3）：`chatbot.py` 已于 G1 退役（空 router）；user_id 透传由 G3 session API 承接（§6.3）；G2 无代码改动，仅本文档记录
- [x] **D24**: `test_runner.py` docstring MVP 限制说明

**Phase 4: runtime 层改造**（依赖 Phase 2）
- [x] **D15**: `assembly.py` `compile_agent_app` FilesystemBackend v3 路径（实施注记：采用「模式 A」root=user workspace 根 + `/skills/<name>` 虚拟挂载，与 §2.1 模板自洽；`compile_standalone_subagent` 维持模式 B）
- [x] **D16**: `assembly.py` `compile_agent_app` 接受 `user_id` 参数
- [x] **D17**: `assembly.py` `get_or_compile` 接受 `user_id` 透传
- [x] **D18**: `assembly.py` `compute_fingerprint` 维持原签名（5 输入字段，**不纳入** workspace_hash）
- [x] **D19**: `runtime.py` 删除 `_COMPILE_USER_ID = "system"`
- [x] **D20**: `runtime.py` `_runtime_cache` 升级为三元组 `(app_id, user_id, fingerprint)` + 大小限制 + TTL
- [x] **D21**: `runtime.py` `get_runtime` 接受 `user_id`，调用 `ensure_user_workspace_up_to_date`；`main.py` 移除 `_warm_agent_apps` 预热（v3.3，§6.1.5；实施注记：G1 契约的 default-app + MCP 工具预热保留）
- [x] **D22**: `runtime.py` cache 淘汰按 `(app_id, user_id)` 维度

**Phase 5: 启动期 + 迁移脚本**（依赖 Phase 1）
- [x] **D13**: `bootstrap.py` 重命名 `ensure_default_agent_workspace` → `ensure_all_agent_workspaces`；单 App 异常隔离
- [x] **D14**: `main.py` lifespan 调用 `ensure_all_agent_workspaces`
- [x] **D25**: `scripts/migrate_workspace.py` 一次性迁移脚本

**Phase 6: 文档更新**（依赖所有 Phase）
- [x] **D26**: 文档更新（详见 §11）

### 7.1.1 Phase 依赖关系图

```
Phase 0 (DB) ──→ Phase 1 (skills_store) ──→ Phase 2 (service)
                    │                           │
                    │                           ├──→ Phase 3 (API)
                    │                           │
                    │                           └──→ Phase 4 (runtime)
                    │
                    └──→ Phase 5 (bootstrap + 迁移)
                            │
                            └──→ Phase 6 (文档)
```

### 7.2 否决项（4 项 · v3 关键变更）

- [x] **N1**: ❌ 不实现 `_read_agent_dir_skill_names`（与 `app_cfg.skill_names` 冗余）
- [x] **N2**: ❌ `compute_fingerprint` 不纳入 `workspace_hash`（与 `_load_skill_hashes` 冗余）
- [x] **N3**: ❌ 不新增 `_validate_user_workspace` 启动期校验（与 lazy 校验 + 启动期校验职责重叠）
- [x] **N4**: ❌ 不引入 `AGENTS_ROOT` 配置项（MVP 简化，路径直接拼接）
- [x] **N5**: ❌ 前端 `AgentList.vue` "绑定到用户" MVP 暂缓（frontend stub 状态，G2 范围外）
- [x] **N6**: ❌ `test_runner` MVP 不强制 combined（standalone runner 维持 Global-only）

### 7.3 修订项（3 项 · v3 关键变更）

- [x] **R1**: ✅ `bootstrap.ensure_default_agent_workspace` 重命名为 `ensure_all_agent_workspaces`（覆盖所有 AgentApp，不仅 default）
- [x] **R2**: ✅ `materialize_to_user_combined` 签名升级：`(app_cfg, user_id, subagent_cfgs)` 替代旧签名（消除参数冗余，user_id 显式传入）
- [x] **R3**: ✅ PATCH 状态机解读 B：`published → draft`（需重新 publish 才生效）

---

## 8. 验证（v3 测试矩阵）

> 详见 `spec-g2-review.md` §6.3 详细测试设计。

### 8.1 单元测试（~37 测试）

#### 8.1.1 `tests/unit/agents/test_skills_store.py`（+10 测试）

| 测试名 | 覆盖点 |
|---|---|
| `test_materialize_for_agent_creates_files` | 复制 + 写入 |
| `test_materialize_for_agent_hash_skip` | hash 命中跳过（性能） |
| `test_materialize_to_user_combined_aggregates_global_and_agent` | 合并去重 |
| `test_agent_skill_overrides_global_in_combined` | Agent 覆盖 Global |
| `test_prune_stale_user_skills` | 过期清理 |
| `test_compute_workspace_hash_stable` | 指纹稳定性 |
| `test_compute_workspace_hash_different_for_diff_content` | 指纹区分度 |
| `test_ensure_user_workspace_up_to_date_skips_when_matched` | lazy 命中 |
| `test_ensure_user_workspace_up_to_date_resyncs_when_drifted` | lazy 不命中 |
| `test_hash_compare_or_write_no_op_when_match` | 写优化 |

#### 8.1.2 `tests/unit/agents/test_runtime.py`（+4 测试）

| 测试名 | 覆盖点 |
|---|---|
| `test_runtime_cache_three_tuple_key` | 三元组 key |
| `test_runtime_cache_eviction_per_user` | 按 (app, user) 淘汰 |
| `test_get_runtime_invokes_lazy_validation` | lazy 触发 |
| `test_remove_compile_user_id_constant` | 验证删除 |

#### 8.1.3 `tests/unit/agents/test_assembly.py`（+3 测试）

| 测试名 | 覆盖点 |
|---|---|
| `test_compile_agent_app_user_skill_root_nested` | nested 路径 |
| `test_compile_agent_app_passes_user_id_to_backend` | user_id 透传 |
| `test_compute_fingerprint_no_workspace_hash` | 不纳入 workspace_hash |

#### 8.1.4 `tests/unit/agents/test_runner.py`（+2 测试）

| 测试名 | 覆盖点 |
|---|---|
| `test_run_subagent_once_docstring_mvp_note` | docstring MVP 说明 |
| `test_materialize_into_combined_directory_function_exists` | 函数存在性 |

#### 8.1.5 `tests/unit/api/test_agent_apps_api.py`（+5 测试）

| 测试名 | 覆盖点 |
|---|---|
| `test_publish_endpoint_calls_service` | API → service 边界 |
| `test_associate_user_endpoint_validates_params` | 参数校验 |
| `test_associate_user_endpoint_calls_service` | API → service 边界 |
| `test_patch_endpoint_handles_status_transition` | 状态机 |
| `test_delete_endpoint_cascades_workspace` | 连锁清理 |

#### 8.1.6 `tests/unit/services/test_*.py`（新文件 +7 测试）

| 文件 | 测试名 | 覆盖点 |
|---|---|---|
| `test_agent_apps_service.py` | `test_publish_calls_materialize_for_agent` | service 编排 |
| `test_agent_apps_service.py` | `test_associate_user_calls_materialize_to_user_combined` | service 编排 |
| `test_agent_apps_service.py` | `test_delete_cascades_user_layer` | service 连锁清理 |
| `test_agent_apps_service.py` | `test_patch_transitions_to_draft` | PATCH 解读 B |
| `test_agents_service.py` | `test_list_subagent_cfgs_returns_effective_set` | 业务逻辑 |
| `test_agents_service.py` | `test_validate_subagent_skill_visibility` | 校验 |
| `test_db_service.py` | `test_get_or_create_association_idempotent` | 关联幂等 |

### 8.2 集成测试（~7 测试 · 新文件）

#### 8.2.1 `tests/integration/agents/test_agent_workspace.py`（+4 测试）

| 测试名 | 覆盖点 |
|---|---|
| `test_full_publish_flow_creates_three_layers` | 端到端三层复制 |
| `test_patch_published_app_reverts_to_draft` | PATCH 解读 B |
| `test_lazy_validation_resyncs_user_layer` | lazy 校验 |
| `test_cross_user_isolation` | per-(app, user) 隔离 |

#### 8.2.2 `tests/integration/agents/test_runtime_cache.py`（+3 测试）

| 测试名 | 覆盖点 |
|---|---|
| `test_cache_separated_by_user` | 三元组 cache |
| `test_cache_evicted_on_workspace_change` | 缓存失效 |
| `test_get_runtime_concurrent_safety` | 并发安全 |

### 8.3 手工冒烟（6 场景）

参考 `docs/agentapp-manual-testing.md` §6.6（待新增）。

| 场景 | 步骤 |
|---|---|
| M1: 三层复制 | 创建 Skill → 创建 AgentApp（含 skill）→ publish → 验证三层文件就位 |
| M2: 关联用户 | associate-user → 验证 User 层 = (Global + Agent) 聚合 |
| M3: PATCH 解读 B | PATCH skill_names → 验证状态变 draft，workspace_hash 清空 |
| M4: 跨用户隔离 | user1 关联 → user2 关联 → 验证 User 层互不影响 |
| M5: Lazy 校验 | publish → 关联 → 修改 Global skill → 重新 publish → 触发 session → 验证 User 层更新 |
| M6: 启动期补建 | 手动删除 Agent 层 → 重启服务 → 验证 `ensure_all_agent_workspaces` 补建 |

### 8.4 覆盖率要求

| 维度 | 覆盖率 | 来源 |
|---|---|---|
| 后端单元测试 | >= 80% | `AGENTS.md` + `docs/development/*` |
| 前端单元测试（vitest） | >= 70% | 同上 |
| 集成测试 | 关键路径 100% | 同上 |
| E2E 手工冒烟 | 每次 Phase 上线前完整跑一遍 | `docs/agentapp-manual-testing.md` |

---

## 9. service 层重构

### 9.1 设计原则

> **用户约束**："api层不要有业务代码，只要做参数校验，业务代码在service层这是项目代码规范"

- **API 层**：仅参数校验 + 调用 service + 返回响应
- **service 层**：业务编排（多表操作、状态机、文件 IO）
- **db 层**：单表 CRUD（无业务逻辑）

### 9.2 文件结构

```
app/services/agents/
├── agent_apps_service.py      # 【新增】AgentApp 业务编排
├── agents_service.py          # 【新增】SubAgent + Agent 关联业务
├── skills_store.py            # 路径 + 复制函数（已有，扩展）
├── bootstrap.py               # 启动期（重命名 + 扩展）
├── runtime.py                 # Runtime cache（扩展）
├── assembly.py                # 编译 + fingerprint（扩展）
└── test_runner.py             # 测试运行（扩展）

app/services/
└── db_service.py              # 新增 association CRUD
```

### 9.3 `agent_apps_service.py` 接口

```python
# app/services/agents/agent_apps_service.py

async def publish_agent_app(
    session: Session, *, app_cfg: AgentApp, current_user_id: int
) -> AgentApp: ...

async def associate_user_with_app(
    session: Session, *, user_id: int, app_id: int, current_user_id: int
) -> None: ...

async def disassociate_user_from_app(
    session: Session, *, user_id: int, app_id: int, current_user_id: int
) -> None: ...

async def patch_agent_app(
    session: Session, *, app_cfg: AgentApp, patch_data: AgentAppUpdate, current_user_id: int
) -> AgentApp:
    """PATCH 解读 B：published → draft，workspace_hash 清空。

    v3.3：patch_data 使用现状 Pydantic schema（AgentAppUpdate），非裸 dict。
    """
    ...

async def delete_agent_app(
    session: Session, *, app_id: int, current_user_id: int
) -> None:
    """删除 AgentApp：清理 Agent 层 + User 层 + 关联表（CASCADE 自动）。"""
    ...

async def ensure_user_workspace_up_to_date(
    session: Session, *, user_id: int, app_id: int
) -> bool: ...
```

### 9.4 `agents_service.py` 接口

```python
# app/services/agents/agents_service.py

async def list_subagent_cfgs(
    session: Session, *, app_id: int, skill_names: Sequence[str]
) -> list[SubAgentConfig]: ...

async def validate_subagent_skill_visibility(
    session: Session, *, app_cfg: AgentApp, subagent_cfgs: Sequence[SubAgentConfig]
) -> None: ...
```

### 9.5 `db_service.py` 新增

```python
# app/services/db_service.py

async def _get_or_create_association(
    session: Session, *, user_id: int, app_id: int
) -> UserAgentAppAssociation: ...

async def _get_association(
    session: Session, *, user_id: int, app_id: int
) -> UserAgentAppAssociation | None: ...

async def _invalidate_user_layer_cache(
    session: Session, *, app_cfg: AgentApp
) -> None:
    """publish / PATCH 后失效关联表的 last_synced_workspace_hash。"""
    ...
```

---

## 10. 迁移收尾

### 10.1 `scripts/migrate_workspace.py`（一次性脚本）

```python
# scripts/migrate_workspace.py

"""
一次性迁移脚本：将旧两层结构迁移至 v3 三层结构。

步骤：
1. 检测旧 SKILLS_ROOT 路径
2. 备份旧结构（archive/ 子目录）
3. 创建新 DATA_ROOT 三层结构
4. 数据回填（agent_dir / workspace_hash / agent_workspace_status）
5. 复制 Global 层 → 新路径
6. 复制 User 层 → 新路径（per-(app_id, user_id)）
7. 验证完整性

执行：python scripts/migrate_workspace.py [--dry-run]
"""
```

**关键设计**：
- `--dry-run` 默认开启（必须显式 `--apply` 才真正迁移）
- 备份 `archive/` 子目录保留 7 天后清理
- 不依赖 alembic（避免长事务）

### 10.2 `app/core/config.py` 兼容

```python
# app/core/config.py

# 保留旧变量 1 个大版本
SKILLS_ROOT: Path = Path("./data/skills")  # DEPRECATED: v3 改用 DATA_ROOT

# 新变量
DATA_ROOT: Path = Path("./data")

# 兼容检测
def _check_legacy_skills_root() -> None:
    if (SKILLS_ROOT / "global").exists() and not (DATA_ROOT / "global").exists():
        logger.warning(
            "skills_root_legacy_detected",
            legacy_root=str(SKILLS_ROOT),
            new_root=str(DATA_ROOT),
            action="run_scripts/migrate_workspace.py",
        )
```

### 10.3 迁移顺序

1. **备份**：备份旧 `data/skills/` 整个目录
2. **迁移脚本**：执行 `scripts/migrate_workspace.py --apply`
3. **alembic 迁移**：`alembic upgrade head`（含 user_agent_app_association 新表）
4. **服务重启**：启动期 `ensure_all_agent_workspaces` 自动补建
5. **冒烟验证**：跑 §8.3 6 个手工冒烟场景

---

## 11. 文档更新清单

### 11.1 `docs/agentapp-manual-testing.md`（新增 §6.6）

| 节 | 标题 | 内容 |
|---|---|---|
| §6.6 | **三级 Workspace 同步** | 6 个手工冒烟场景（M1-M6，详见 §8.3） |

### 11.2 `docs/authentication.md`（新增 §7）

| 节 | 标题 | 内容 |
|---|---|---|
| §7 | **Workspace 隔离** | per-(app_id, user_id) User 层隔离；权限边界；admin 可视化接口 |

### 11.3 `docs/architecture.md`（更新 Workspace 章节）

| 节 | 标题 | 内容 |
|---|---|---|
| Workspace 三层架构 | v3 嵌套路径图解 | 见 §2.1 |
| 复制时机 | v3 原版 + lazy 校验 | 见 §4 |
| fingerprint 锁定 | workspace_hash 语义 | 见 §5 |

### 11.4 `spec-g2-workspace.md`（本文档）

- 重写：v3 修订版（即当前文档）
- 旧 spec 保留为 `spec-g2-workspace-legacy.md`（仅参考）

---

## 12. G3 集成 TODO

> 详见 `spec-g3-session.md` §12。

### 12.1 G2 提供的集成接口

**签名一致性说明**：此接口实现于 `agent_apps_service.py`（v3.3 归属，§9.3）；算法详见 §4.3。G3 调用方无需关心内部实现位置。

```python
# app/services/agents/agent_apps_service.py（统一入口）

async def ensure_user_workspace_up_to_date(
    session: Session, *, user_id: int, app_id: int
) -> bool:
    """G3 在 session 创建 / 启动入口调用。

    Args:
        session: 数据库会话（SQLModel 同步 Session，v3.3 全局约定）
        user_id: 用户 ID（从 API 层 get_current_user 获取）
        app_id: AgentApp ID

    Returns:
        bool: True 表示执行了重新复制；False 表示 hash 命中跳过。

    Raises:
        AgentAppNotFoundError: app_id 不存在
    """
```

### 12.2 G3 调用点

| 端点 | 调用时机 |
|---|---|
| `POST /sessions` | 创建新会话前调用（确保 User 层就位） |
| `GET /sessions/{id}` | 获取会话详情前调用（lazy 校验） |
| `GET /sessions/{id}/messages` | 读消息历史前调用（确保 User 层 + FilesystemBackend 一致） |

### 12.3 G3 路径预留

**路径模板**：
```
{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.json
```

**存储策略**（详见 `spec-g3-session.md` §4）：
- **主存储**：PG `Session` 表 + LangGraph `AsyncPostgresSaver`（不变）
- **JSON 视图层**：导出 / 调试用途（非主存储）
- 此路径用于存放 JSON 导出文件，**不替代** PG 主存储

**JSON Schema**（待 G3 细化）：
```json
{
  "session_id": "string",
  "user_id": "int",
  "agent_app_id": "int",
  "created_at": "ISO8601",
  "updated_at": "ISO8601",
  "messages": [
    {
      "role": "user|assistant|system",
      "content": "string",
      "timestamp": "ISO8601"
    }
  ],
  "metadata": {
    "model": "string",
    "fingerprint": "string",
    "workspace_hash": "string"
  }
}
```

**注意事项**：
- JSON 文件仅用于调试和离线分析，**不作为生产数据源**
- 用户可通过 `GET /sessions/{id}/export` 端点导出
- 导出时需进行 session 归属校验（确保 user_id 匹配）

---

## 13. 关键决策（Q3/Q4 + v3 新增决策）

### 13.1 Q3：Agent 层 Skill 物理路径命名

| 选项 | 路径 | 决策 |
|---|---|---|
| **`<app_id>`**（推荐） | `{DATA_ROOT}/agents/1/skills/csv-report/SKILL.md` | ✅ **采纳** |
| `<app_name>` | `{DATA_ROOT}/agents/demo-assistant/skills/csv-report/SKILL.md` | ❌ |
| `<app_id>_<app_name>` | `{DATA_ROOT}/agents/1_demo-assistant/skills/csv-report/SKILL.md` | ❌ |

**理由**：主键稳定性（name 未来若放开可改，id 永不）；跨环境（dev/staging/prod）name 可能冲突，id 不会；运维侧有 `app_id` → `app_name` 的查询接口补偿。

### 13.2 Q4：Agent 层与 Global 冲突时优先级

| 选项 | 描述 | 决策 |
|---|---|---|
| **Agent 覆盖 Global**（推荐） | Agent 是发布时的快照 | ✅ **采纳** |
| Global 永远优先 | Global 是共享基线 | ❌ |
| 按引用顺序 | 实现简单但语义模糊 | ❌ |

**理由**：Agent 层是 publish 时的快照，本质是 Agent 拥有者对 Global 的"私有化定制"；符合用户对"Agent 专属"的预期。

### 13.3 v3 新增决策（62 项关键决策详见 `spec-g2-review.md` §7.0）

| # | 决策 | v3.2 选择 |
|---|---|---|
| D-V3-01 | User 层是否嵌套在 AgentApp 下 | ✅ 嵌套（`agents/<app_id>/users/<user_id>/`） |
| D-V3-02 | `materialize_to_user_combined` 签名 | `(app_cfg, user_id, subagent_cfgs)`（消除参数冗余，user_id 显式传入） |
| D-V3-03 | `_read_agent_dir_skill_names` | ❌ 否决（与 `app_cfg.skill_names` 冗余） |
| D-V3-04 | `compute_fingerprint` 是否纳入 workspace_hash | ❌ 否决（与 `_load_skill_hashes` 冗余） |
| D-V3-05 | `_validate_user_workspace` 是否新增 | ❌ 否决（职责重叠） |
| D-V3-06 | PATCH 状态机解读 | ✅ 解读 B（published → draft） |
| D-V3-07 | `_runtime_cache` key 维度 | ✅ 三元组 `(app_id, user_id, fingerprint)` |
| D-V3-08 | `_COMPILE_USER_ID` | ❌ 删除（真实 user_id 替代） |
| D-V3-09 | 启动期补建函数 | ✅ 重命名 `ensure_all_agent_workspaces` |
| D-V3-10 | Lazy 校验位置 | ✅ `ensure_user_workspace_up_to_date`（session 启动入口） |
| D-V3-11 | `AGENTS_ROOT` 配置项 | ❌ MVP 不引入 |
| D-V3-12 | 前端 "绑定到用户" | ❌ MVP 暂缓 |
| D-V3-13 | `test_runner` MVP | ✅ 维持 Global-only |
| D-V3-14 | service 层拆分 | ✅ `agent_apps_service.py` + `agents_service.py` |
| D-V3-15 | 兼容 `SKILLS_ROOT` | ✅ 保留 1 个大版本 |

### 13.4 v3.3 实现期澄清决策（实现前固化，编码只认本节）

> 产生背景：v3.2 伪代码与代码库现状 / 评审文档存在冲突，实现前经用户逐项澄清确认。

#### A. 用户澄清的 4 项主决策

| # | 议题 | v3.3 决策 | 影响 |
|---|---|---|---|
| C-1 | Session 类型 | ✅ SQLModel 同步 `Session`（全文伪代码已同步）；原 `AsyncSession` 为示意笔误 | 不迁移 asyncpg；G2 范围内零引擎变更 |
| C-2 | 启动预热策略 | ✅ 移除 `main.py` `_warm_agent_apps` 的 get_runtime 预热；lifespan 仅调 `ensure_all_agent_workspaces`（§6.1.5） | 首个请求承担编译冷启动代价 |
| C-3 | lazy 比对算法 | ✅ 动态期望指纹（`_compute_effective_workspace_hash` 聚合 Agent+Global 期望源 vs User 层实际指纹，§4.3）；bool 返回 + 静默 False 语义保留 | 修复「SubAgent 外部 skill 导致永不相等」缺陷；支持目录丢失自愈 |
| C-4 | User 层填充时机 | ✅ `compile_agent_app` 删除 `sync_user_skills` 调用（§6.1.3）；填充统一由 get_runtime 前置 lazy 校验负责 | 编译函数纯化；`sync_user_skills` 保留为历史兼容 |

#### B. 次级声明（spec 内部冲突取舍）

| # | 议题 | v3.3 取舍 |
|---|---|---|
| S-1 | `ensure_user_workspace_up_to_date` 实现位置 | `agent_apps_service.py`（§9.3 权威；D6 归属同步修订，避免 skills_store ↔ agents_service 循环依赖） |
| S-2 | `get_runtime` 签名 | `(session, app_id: int, *, user_id: int)`（§6.1）；删除 `_resolve_agent_app` 的 `"system-default"` 占位符解析 |
| S-3 | `run_subagent_once` | G2 不改签名，仅 docstring 补 MVP 限制（D24；§6.2 样例为 G3+ 未来形态） |
| S-4 | `patch_agent_app` patch_data 类型 | 现状 Pydantic schema（`AgentAppUpdate`），非裸 dict |
| S-5 | `db_service.py` 函数命名 | 按 §9.5 下划线名（`_get_or_create_association` 等） |
| S-6 | `agent_dir` / association 表字段 | `String(255)`；按 §3.4（`associated_at`） |
| S-7 | `compute_fingerprint` 签名 | 维持现状 5 输入（§5.3 伪代码已修正对齐） |

---

## 附录 A：变更影响总览

### A.1 文件改动量

| 文件 | v2 变更 | v3 变更 | v3 改动量 |
|---|---|---|---|
| `app/services/agents/skills_store.py` | 7 个新函数 | 11 个新函数 + 5 路径 helpers + hash 工具 | 大（> 300 行） |
| `app/services/agents/agent_apps_service.py` | （未创建） | 新建，6 个编排函数 | 大 |
| `app/services/agents/agents_service.py` | （未创建） | 新建，2 个函数 | 中 |
| `app/services/db_service.py` | （未涉及） | 新增 3 个 association 函数 | 小 |
| `app/services/agents/bootstrap.py` | 1 个新函数 | 重命名 + 增强 | 中 |
| `app/services/agents/runtime.py` | chatbot 装配新增 | 三元组 cache + lazy 校验 | 中 |
| `app/services/agents/assembly.py` | 1 个字段扩展 | nested 路径 + user_id 透传 | 中 |
| `app/services/agents/test_runner.py` | 1 处改用 | docstring MVP 说明 | 小 |
| `app/api/v1/apps.py` | publish 扩展 + 1 端点 | 同 v2（调用 service） | 中 |
| `app/api/v1/chatbot.py` | （未涉及） | 调用 `get_runtime(..., user_id=...)` | 小 |
| `app/models/agent_assets.py` | 4 字段 | 4 字段 + 1 表 | 中 |
| `app/core/config.py` | 1 配置项 | DATA_ROOT + 兼容检测 | 小 |
| `app/main.py` lifespan | 1 调用 | 同 v2 | 小 |
| `scripts/migrate_workspace.py` | （未涉及） | 新建 | 中 |
| `alembic/versions/<rev>_*.py` | 4 字段 | 4 字段 + 1 新表 | 大 |

### A.2 v3 vs v2 关键差异

| 维度 | v2（原 spec） | v3.2 修订版 |
|---|---|---|
| User 层路径 | `users/<user_id>/`（顶层） | `agents/<app_id>/users/<user_id>/`（嵌套） |
| `materialize_to_user_combined` 参数 | `(user_id, app_id, global_skill_names, agent_skill_names)` | `(app_cfg, user_id, subagent_cfgs)`（消除冗余，user_id 显式传入） |
| `_read_agent_dir_skill_names` | 实现 | ❌ 否决 |
| `compute_fingerprint` workspace_hash | 纳入 | ❌ 不纳入 |
| `_validate_user_workspace` | 实现 | ❌ 否决（lazy 校验替代） |
| PATCH 状态机 | 未明确 | ✅ 解读 B（published → draft） |
| 启动期补建 | `ensure_default_agent_workspace` | `ensure_all_agent_workspaces`（重命名 + 覆盖所有） |
| `_runtime_cache` key | `(app_id, fingerprint)` 二元组 | `(app_id, user_id, fingerprint)` 三元组 |
| `_COMPILE_USER_ID` | 临时简化 | ❌ 删除 |
| `AGENTS_ROOT` 配置 | 新增 | ❌ MVP 不引入 |
| 前端绑定用户 | 实现 | ❌ MVP 暂缓 |
| service 层拆分 | 未涉及 | ✅ `agent_apps_service.py` + `agents_service.py` |
| 迁移脚本 | 未涉及 | ✅ `scripts/migrate_workspace.py` |

### A.3 否决项回顾

| # | spec 项 | 否决理由 | 替代方案 |
|---|---|---|---|
| 1 | `_read_agent_dir_skill_names` | 与 `app_cfg.skill_names` 冗余 | 直接从 DB 读取 |
| 2 | `compute_fingerprint` 纳入 `workspace_hash` | 与 `_load_skill_hashes` 冗余 | 维持 5 输入字段 |
| 3 | `_validate_user_workspace` 启动期校验 | 与 lazy + 启动期补建职责重叠 | 双层覆盖已足够 |
| 4 | `AGENTS_ROOT` 配置项 | MVP 简化 | 路径直接拼接 |
| 5 | `_build_filesystem_backend` 单独函数 | 单点调用 | 内联在 `compile_agent_app` |
| 6 | 前端 "绑定到用户" | frontend stub 状态，G2 范围外 | G3 决定 |
| 7 | `test_runner` 强制 combined | standalone runner MVP 限制 | 函数实现，调用方后续启用 |

---

## 附录 B：术语表

| 术语 | 定义 |
|---|---|
| **Global 层** | `{DATA_ROOT}/global/skills/<name>/SKILL.md`，全 AgentApp 共享 |
| **Agent 层** | `{DATA_ROOT}/agents/<app_id>/skills/<name>/SKILL.md`，AgentApp 私有，publish 时从 Global 复制 |
| **User 层** | `{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/<name>/SKILL.md`，用户针对特定 AgentApp 的个性化副本 |
| **workspace_hash** | Agent 层内容指纹（所有 SKILL.md 的 sha256 拼接后再 sha256） |
| **effective_skill_names** | Agent + SubAgent skill_names 并集 |
| **Lazy 校验** | session 启动入口调用 `ensure_user_workspace_up_to_date`，hash 命中跳过 |
| **PATCH 解读 B** | published → draft（PATCH 后 workspace_hash 清空，需重新 publish 才生效） |
| **v3 嵌套 User 层** | User 层嵌套在 AgentApp 下（per-(app, user) 真正隔离），区别于 v2 顶层 `users/<user_id>/` |
| **Hash 比对优化** | source_hash vs existing_hash，仅不一致时写（避免冗余 IO） |

---

## 附录 C：参考

- **总览**：`docs/changelog/agentapp-three-layer-refactor/overview.md`
- **风险清单**：`docs/changelog/agentapp-three-layer-refactor/files-risks.md`
- **开放问题**：`docs/changelog/agentapp-three-layer-refactor/open-questions.md`（Q3/Q4）
- **审查记录**：`docs/changelog/agentapp-three-layer-refactor/spec-g2-review.md`（62 项关键决策 + 4 项否决）
- **G1 认证**：`docs/changelog/agentapp-three-layer-refactor/spec-g1-auth.md`
- **G3 Session**：`docs/changelog/agentapp-three-layer-refactor/spec-g3-session.md`
- **手工测试**：`docs/agentapp-manual-testing.md` §6.6（待新增）
- **认证文档**：`docs/authentication.md` §7（待新增）
- **架构文档**：`docs/architecture.md` Workspace 章节（待更新）

---

**文档版本**：v3.3 修订版（2026-08-26）
**审查方法**：分阶段逐项（6 项）；每项落盘后进入下一项
**审查记录**：`spec-g2-review.md`（2967 行；62 项关键决策 + 4 项否决）
**下一步**：按 §7 DoD 顺序实施（Phase 0-7），详见 `spec-g2-review.md` §7.2 交付清单