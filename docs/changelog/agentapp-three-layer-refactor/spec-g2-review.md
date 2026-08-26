# G2 三级 Workspace 审查报告

> **关联文档**：`spec-g2-workspace.md`（待修订）、`overview.md`、`files-risks.md`
> **审查方式**：分阶段逐项审查；每项完成后落盘，进入下一项
> **审查日期**：2026-08-25
> **审查范围**：spec §2（目录结构）、§3（alembic 迁移）、§4（复制逻辑）、§5（fingerprint + 启动校验）、§6（运行时）、§7（DoD）

---

## 审查进度

| # | 审查主题 | spec 章节 | 状态 |
|---|---|---|---|
| 1 | 目录结构设计 | §2 | ✅ **已落盘（v3 最终版）** |
| 2 | alembic 数据模型迁移 | §3 | ✅ **已落盘（v3 修订版）** |
| 3 | 复制逻辑核心 | §4 | ✅ **已落盘（v3 原版 + 实现优化）** |
| 4 | fingerprint 锁定 + 启动校验 | §5 | ✅ **已落盘（v3 修订版）** |
| 5 | 运行时读取路径调整 | §6 | ✅ **已落盘（v3 修订版）** |
| 6 | DoD  + 兼容迁移收尾 | §7 + §8 | ✅ **已落盘（v3 修订版）** |
| — | 总览总结 + 交付清单 | — | ✅ **已落盘（v3 最终版）** |

---

## 第 1 项：目录结构设计（已落盘 · v3 最终版）

### 1.0 关键决策回顾

| 决策 | 结论 | 依据 |
|---|---|---|
| 顶层目录 | `SKILLS_ROOT` → `DATA_ROOT` | 与 `LOG_DIR` / `MCP_STDIO_ROOT` 命名一致；预留 notes/memory/sessions 等其他 workspace 资源 |
| 子目录 | 每层下加 `skills/` 子目录 | 隔离 skill 文件与其他未来资源 |
| AGENTS_ROOT / USERS_ROOT | **不引入** | MVP 简化；路径直接拼接 |
| **User 层路径（重大变更）** | `agents/<app_id>/users/<user_id>/skills/`（**嵌套在 AgentApp 下**） | per-(app, user) 真正隔离；session 文件归属清晰；删 AgentApp 连锁清理可行 |
| 顶层 `users/<user_id>/` | **保留**（MVP 不创建，仅 soul.md 等预留） | 跨 app 共享资源空间 |
| SubAgent workspace | **不需要**（与 Agent 共享 user 层 backend） | deepagents SubAgent 无 backend 字段；共享父 agent 的 FilesystemMiddleware |
| SubAgent.skill_names | **保持独立配置**（whitelist） | 与 deepagents 设计一致；多 Agent 共享 SubAgent 时配置共享 + 数据隔离 |
| effective_skill_names | Agent ∪ SubAgent（并集） | user 层 materialize 范围 |
| User 层复制时机 | **Hybrid**：首次 associate-user 全量 + session 启动 lazy 校验 | 按需同步；不阻塞 publish |
| Lazy 校验触发时机 | G3 决定（G2 仅实现函数 + `lazy_workspace_sync` 参数） | 解耦 G2 / G3 |
| standalone 测试差异 | MVP 接受（测试用 Global 模拟；运行时用 user 层） | standalone 测试只验证功能可用性 |

### 1.1 目录结构（v3 最终版）

```text
{DATA_ROOT}/
  global/
    skills/
      <skill_name>/
        SKILL.md                       # 全局共享 skill（真相源在 DB）
  agents/
    <app_id>/
      skills/                          # Agent 层 skill 快照
        <skill_name>/
          SKILL.md
      users/                           # 关联到该 AgentApp 的 user 目录
        <user_id>/
          skills/                      # User 层 skill 快照
            <skill_name>/
              SKILL.md
          sessions/                    # G3 预留：session JSON 文件
            <session_id>.json
  users/
    <user_id>/                         # 跨 app 共享资源（现阶段为空，soul.md 预留）
```

### 1.2 `app/core/config.py` 改造

```python
# 替换原 SKILLS_ROOT
self.DATA_ROOT = os.getenv("DATA_ROOT", "./data")

# 兼容旧 SKILLS_ROOT env（一次性迁移期，标记 deprecated）
legacy_skills_root = os.getenv("SKILLS_ROOT", "")
if legacy_skills_root and not os.getenv("DATA_ROOT"):
    self.DATA_ROOT = legacy_skills_root
    logger.warning(
        "skills_root_env_deprecated",
        hint="SKILLS_ROOT is deprecated; use DATA_ROOT instead",
    )

# 不引入 AGENTS_ROOT / USERS_ROOT（路径直接拼接，MVP 简化）
```

### 1.3 `app/services/agents/skills_store.py` 路径 helper（v3 修订版）

```python
def _data_root() -> Path:
    return Path(settings.DATA_ROOT)

def _global_skill_dir(name: str) -> Path:
    return _data_root() / "global" / "skills" / _validate_skill_name(name)

def _global_skill_file(name: str) -> Path:
    return _global_skill_dir(name) / _SKILL_FILE_NAME

def _agent_dir(app_id: int) -> Path:
    """AgentApp 私有空间根目录：``{DATA_ROOT}/agents/<app_id>``

    是 AgentApp 的"工作空间根"，所有 AgentApp 专属资源的父目录：
    - skills/    # Agent 层 skill 快照
    - users/     # 关联到该 AgentApp 的 user 目录（含 per-user skills/ 和 sessions/）
    - notes/     # Phase 5+ 预留
    - memory/    # Phase 5+ 预留
    """
    return _data_root() / "agents" / str(app_id)

def _agent_skill_dir(app_id: int) -> Path:
    """Agent 层 skill 目录：``{DATA_ROOT}/agents/<app_id>/skills``"""
    return _agent_dir(app_id) / "skills"

def _agent_skill_file(app_id: int, name: str) -> Path:
    return _agent_skill_dir(app_id) / _validate_skill_name(name) / _SKILL_FILE_NAME

def _user_dir(app_id: int, user_id: str) -> Path:
    """per-(app, user) user 私有空间根目录：``{DATA_ROOT}/agents/<app_id>/users/<user_id>``

    是 per-(app, user) 工作空间根，所有该 user 在该 app 下资源的父目录：
    - skills/     # User 层 skill 快照
    - sessions/   # G3 预留
    """
    return _agent_dir(app_id) / "users" / _validate_user_id(user_id)

def _user_skill_dir(app_id: int, user_id: str) -> Path:
    """User 层 skill 目录：``{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills``"""
    return _user_dir(app_id, user_id) / "skills"

def _user_skill_file(app_id: int, user_id: str, name: str) -> Path:
    return _user_skill_dir(app_id, user_id) / _validate_skill_name(name) / _SKILL_FILE_NAME

def _user_session_dir(app_id: int, user_id: str) -> Path:
    """User 层 session 目录（G3 预留）：``{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions``"""
    return _user_dir(app_id, user_id) / "sessions"

def _user_global_dir(user_id: str) -> Path:
    """User顶层目录（跨 app 共享资源预留）。"""
    return _data_root() / "users" / _validate_user_id(user_id)
```

### 1.4 读取路径策略

| 调用方 | 读取层 | 路径 |
|---|---|---|
| **Agent App Chat**（`runtime.ainvoke`） | User 层 | `{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/<name>/SKILL.md` |
| **Agent App SubAgent**（在 Agent 中运行） | User 层（共享 Agent backend） | 同上 |
| **SubAgent Standalone 测试**（`run_subagent_once`） | 模拟 User 层 | `tmp_skills_root/<name>/SKILL.md`（从 Global 复制填充） |
| **AgentApp 编辑预览**（前端） | DB body | API 直接读 `SkillAsset.body` |

### 1.5 FilesystemBackend 路径改造

```python
# app/services/agents/assembly.py:478
# 旧：
user_skill_root = Path(settings.SKILLS_ROOT) / "users" / str(user_id)

# 新：
user_skill_root = (
    Path(settings.DATA_ROOT)
    / "agents"
    / str(app_cfg.id)
    / "users"
    / str(user_id)
)
```

### 1.6 目录创建时机

| 触发点 | 创建路径 |
|---|---|
| 系统启动（lifespan） | `{DATA_ROOT}/`, `{DATA_ROOT}/global/skills/`, `{DATA_ROOT}/agents/` |
| AgentApp 创建（`POST /apps`） | `{DATA_ROOT}/agents/<new_app_id>/skills/` |
| User 注册（`POST /auth/register`） | （MVP 不创建顶层 user 目录；按需创建） |
| 首次 associate-user（`POST /apps/{id}/associate-user/{uid}`） | `{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/` |
| Lazy 校验（`ensure_user_workspace_up_to_date`） | 检查目录存在，不存在则 mkdir |
| Bootstrap 补建 | 遍历存量 (AgentApp, User) 关联补建缺失目录 |

### 1.7 删除连锁清理

| 删除事件 | 清理范围 |
|---|---|
| **删 AgentApp** | `shutil.rmtree(_data_root() / "agents" / <app_id>)`（含所有 user 在该 app 下的 skills + sessions） |
| **取消 user 关联** | `shutil.rmtree(_user_skill_dir(<app_id>, <user_id>))`（仅该 app 下该 user 的数据；其他 app 不受影响） |
| **删 Global skill** | `_remove_skill_dirs` 增加清理 Agent 层副本（每个 agent 目录下） |

### 1.8 迁移脚本（`scripts/migrate_workspace.py`）

```python
# 一次性迁移脚本：SKILLS_ROOT → DATA_ROOT + skills/ 子目录 + user 层嵌套
# 前置条件：user_agent_app_association 表已建立 + 存量关联回填完成
```

- 旧 `{SKILLS_ROOT}/global/<name>/SKILL.md` → 新 `{DATA_ROOT}/global/skills/<name>/SKILL.md`
- 旧 `{SKILLS_ROOT}/users/<uid>/<name>/SKILL.md` → 根据 user_agent_app_association 映射到 `{DATA_ROOT}/agents/<app_id>/users/<uid>/skills/<name>/SKILL.md`
- 迁移期：保留旧路径符号链接（双轨1 个版本）；下个大版本清理

### 1.9 影响下游模块

| 受影响模块 | 变更内容 | 优先级 |
|---|---|---|
| `skills_store.py` | 路径 helper 全部重写（接受 app_id） | G2 必改 |
| `assembly.py:478` | FilesystemBackend 路径改造 | G2 必改 |
| `runtime.py` | `get_runtime` 接受 user_id（移除 `_COMPILE_USER_ID="system"`） | G2 必改 |
| `apps.py` | 新增 `associate-user` 端点 + delete 连锁清理 | G2 必改 |
| `models/agent_assets.py` | 新增 `UserAgentAppAssociation` 表 | G2 必改 |
| alembic 迁移 | 新增 user_agent_app_association 表 + agent_app 字段 | G2 必改 |
| `G3 spec-g3-session.md` | session JSON 路径 `agents/<app_id>/users/<user_id>/sessions/<session_id>.json` | G3 集成点 |
| 存量迁移脚本 | `SKILLS_ROOT` → `DATA_ROOT` + 重映射 user 层路径 | G2 上线前 |

### 1.10 G3 集成 TODO（已同步到 `spec-g3-session.md` §12）

详见 `spec-g3-session.md` §12 "G2 集成接口预留"。

---

## 第 2 项：alembic 数据模型迁移（已落盘 · v3 修订版）

### 2.0 关键决策回顾

| 决策 | 结论 | 备注 |
|---|---|---|
| `SkillAsset.scope` 字段 | ✅ **保留**（默认 `global`，加索引） | 为未来 Phase 5+ `scope='agent'` 扩展 |
| `AgentApp.agent_dir` 字段 | ✅ **保留**（语义修订：`{DATA_ROOT}/agents/<app_id>`，不含 `/skills`） | AgentApp 私有空间根目录 |
| `AgentApp.workspace_hash` 字段 | ✅ 保留 | sha256 hex，nullable |
| `AgentApp.agent_workspace_status` 字段 | ✅ 简化两态（`pending` / `active`，加索引） | 三态 → 两态 |
| `UserAgentAppAssociation` 新表 | ✅ **新增**（v3 关键基础设施） | (user_id, agent_app_id) 联合唯一 |
| `last_synced_workspace_hash` 字段 | ✅ 包含 | 增量同步优化 |
| FK ondelete 策略 | ✅ `CASCADE` | 删 AgentApp/User 自动清理 |
| alembic 数据回填 | ✅ `conn.execute(sa.text(...))` | 不引入 env 依赖 |

### 2.1 现状核对

| 现状项 | 文件 / 行号 | 与 spec 的一致性 |
|---|---|---|
| `SkillAsset` 模型（无 scope） | `app/models/agent_assets.py:57-79` | ⚠️ 需加 scope 字段 |
| `AgentApp` 模型（无 agent_dir/workspace_hash/agent_workspace_status） | `app/models/agent_assets.py:82-115` | ⚠️ 需加三个字段 |
| alembic 迁移规范 | `alembic/versions/g1a2b3c4d5e6_refresh_token.py`（最新） | ✅ 标准模式 |
| `UserAgentAppAssociation` 表 | **不存在** | ⚠️ v3 设计新增必要表 |
| alembic env.py | `alembic/env.py:40-47` | ✅ `EXCLUDE_TABLES` 配置 |
| 当前 down_revision 链 | `g1a2b3c4d5e6`（最新） | G2 迁移应基于此 |

### 2.2 字段定义清单

#### 2.2.1 `SkillAsset.scope`

```python
op.add_column(
    "skill_asset",
    sa.Column("scope", sa.String(16), nullable=False, server_default="global"),
)
op.create_index(
    op.f("ix_skill_asset_scope"),
    "skill_asset",
    ["scope"],
)
```

**保留理由**：未来 Phase 5+ 可扩展 `scope='agent'`（Agent 专属 skill，不共享到 Global）。

#### 2.2.2 `AgentApp.agent_dir`

```python
op.add_column("agent_app", sa.Column("agent_dir", sa.String(512), nullable=True))
```

**语义**：`{DATA_ROOT}/agents/<app_id>`，**不含** `/skills`。AgentApp 私有空间根目录。

**填充逻辑**（bootstrap）：

```python
async def _ensure_agent_dir(app_cfg: AgentApp) -> None:
    agent_dir = _agent_dir(app_cfg.id)
    agent_dir.mkdir(parents=True, exist_ok=True)
    if app_cfg.agent_dir != str(agent_dir):
        app_cfg.agent_dir = str(agent_dir)
```

#### 2.2.3 `AgentApp.workspace_hash`

```python
op.add_column("agent_app", sa.Column("workspace_hash", sa.String(64), nullable=True))
```

- 64 字符（sha256 hex）
- `nullable=True`（pending 状态下未计算）
- 不加索引（不作为查询条件）
- 不加 unique（不同 app 可能有相同 hash）

**用法**：
- publish 时由 `compute_workspace_hash(_agent_skill_dir(app_cfg.id))` 计算
- lazy 校验时与 user 层实际 hash 比对

#### 2.2.4 `AgentApp.agent_workspace_status`

```python
op.add_column(
    "agent_app",
    sa.Column("agent_workspace_status", sa.String(16), nullable=False, server_default="pending"),
)
op.create_index(
    op.f("ix_agent_app_agent_workspace_status"),
    "agent_app",
    ["agent_workspace_status"],
)
```

**状态机简化**：三态（pending/migrated/active）→ **两态**（pending/active）

| 状态 | 含义 | 转换 |
|---|---|---|
| `pending` | Agent 层目录未补全 | 默认；bootstrap 后 → `active` |
| `active` | Agent 层目录已就位 | bootstrap / publish 后设置 |

### 2.3 `UserAgentAppAssociation` 新表（v3 关键新增）

**引入原因**：v3 设计下 user 层路径依赖 `(app_id, user_id)`，必须持久化关联关系。

```python
op.create_table(
    "user_agent_app_association",
    sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
    sa.Column("user_id", sa.Integer(), nullable=False),
    sa.Column("agent_app_id", sa.Integer(), nullable=False),
    sa.Column(
        "last_synced_workspace_hash",
        sqlmodel.sql.sqltypes.AutoString(length=64),
        nullable=True,
    ),
    sa.ForeignKeyConstraint(["agent_app_id"], ["agent_app.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("user_id", "agent_app_id", name="uq_user_agent_app_association"),
)
op.create_index(
    op.f("ix_user_agent_app_association_user_id"),
    "user_agent_app_association",
    ["user_id"],
)
op.create_index(
    op.f("ix_user_agent_app_association_agent_app_id"),
    "user_agent_app_association",
    ["agent_app_id"],
)
```

**关键决策**：

| 决策 | 结论 | 理由 |
|---|---|---|
| `last_synced_workspace_hash` 字段 | ✅ 包含 | lazy 校验时快速判断"是否需要同步"，无需每次全量遍历 |
| `last_synced_at` 字段 | ⚠️ 可选 | 调试用，MVP 不必须 |
| FK ondelete 策略 | ✅ `CASCADE` | 删 AgentApp/User 时自动清理关联 |
| 唯一约束 | `(user_id, agent_app_id)` 联合唯一 | 同一 user 不能重复关联同一 app |
| 自增 PK | ✅ 需要 | 便于引用 |

### 2.4 完整 alembic 迁移脚本（v3 修订版）

```python
"""Add Agent workspace fields + UserAgentAppAssociation table (Phase 2 G2).

Revision ID: <rev>
Revises: g1a2b3c4d5e6
Create Date: 2026-08-XX

字段变更：
- skill_asset.scope: skill 作用域（global/agent，MVP 仅 global）
- agent_app.agent_dir: AgentApp 私有空间根目录（{DATA_ROOT}/agents/<app_id>）
- agent_app.workspace_hash: Agent 层内容指纹（sha256 hex）
- agent_app.agent_workspace_status: pending | active

新增表：
- user_agent_app_association: (user_id, agent_app_id) 关联关系 + 同步状态

数据回填：
- skill_asset.scope = 'global'（默认）
- agent_app.agent_workspace_status = 'pending'（默认）
- agent_dir / workspace_hash 由 bootstrap 启动时补全
"""
from typing import Sequence, Union
import sqlalchemy as sa
import sqlmodel  # noqa: F401
from alembic import op

revision: str = "<rev>"
down_revision: Union[str, Sequence[str], None] = "g1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. SkillAsset.scope 字段（保留为未来扩展）
    op.add_column(
        "skill_asset",
        sa.Column("scope", sa.String(16), nullable=False, server_default="global"),
    )
    op.create_index(
        op.f("ix_skill_asset_scope"),
        "skill_asset",
        ["scope"],
    )

    # 2. AgentApp 新字段
    op.add_column("agent_app", sa.Column("agent_dir", sa.String(512), nullable=True))
    op.add_column("agent_app", sa.Column("workspace_hash", sa.String(64), nullable=True))
    op.add_column(
        "agent_app",
        sa.Column(
            "agent_workspace_status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
    )
    op.create_index(
        op.f("ix_agent_app_agent_workspace_status"),
        "agent_app",
        ["agent_workspace_status"],
    )

    # 3. UserAgentAppAssociation 新表
    op.create_table(
        "user_agent_app_association",
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_app_id", sa.Integer(), nullable=False),
        sa.Column(
            "last_synced_workspace_hash",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["agent_app_id"], ["agent_app.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "agent_app_id", name="uq_user_agent_app_association"),
    )
    op.create_index(
        op.f("ix_user_agent_app_association_user_id"),
        "user_agent_app_association",
        ["user_id"],
    )
    op.create_index(
        op.f("ix_user_agent_app_association_agent_app_id"),
        "user_agent_app_association",
        ["agent_app_id"],
    )

    # 4. 数据回填（确保 status 和 scope 显式填充）
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE skill_asset SET scope = 'global' WHERE scope IS NULL"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE agent_app SET agent_workspace_status = 'pending' "
            "WHERE agent_workspace_status IS NULL"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 1. 删除 UserAgentAppAssociation
    op.drop_index(
        op.f("ix_user_agent_app_association_agent_app_id"),
        table_name="user_agent_app_association",
    )
    op.drop_index(
        op.f("ix_user_agent_app_association_user_id"),
        table_name="user_agent_app_association",
    )
    op.drop_table("user_agent_app_association")

    # 2. 删除 AgentApp 新字段
    op.drop_index(
        op.f("ix_agent_app_agent_workspace_status"),
        table_name="agent_app",
    )
    op.drop_column("agent_app", "agent_workspace_status")
    op.drop_column("agent_app", "workspace_hash")
    op.drop_column("agent_app", "agent_dir")

    # 3. 删除 SkillAsset.scope
    op.drop_index(op.f("ix_skill_asset_scope"), table_name="skill_asset")
    op.drop_column("skill_asset", "scope")
```

### 2.5 模型层同步（`app/models/agent_assets.py`）

```python
# SkillAsset 新增字段
class SkillAsset(BaseModel, table=True):
    ...
    scope: str = Field(default="global", max_length=16, index=True)  # 保留字段


# AgentApp 新增字段
class AgentApp(BaseModel, table=True):
    ...
    agent_dir: Optional[str] = Field(default=None, max_length=512)
    workspace_hash: Optional[str] = Field(default=None, max_length=64)
    agent_workspace_status: str = Field(default="pending", max_length=16, index=True)


# 新增 UserAgentAppAssociation 模型
class UserAgentAppAssociation(BaseModel, table=True):
    """User-AgentApp 关联表（v3 引入）：记录 user 关联了哪些 AgentApp。

    Attributes:
        user_id: 外键引用 user.id（CASCADE 删除）
        agent_app_id: 外键引用 agent_app.id（CASCADE 删除）
        last_synced_workspace_hash: 上次 lazy 校验时的 workspace_hash
            （用于快速判断是否需要增量同步，避免每次全量遍历 user 层）
        created_at: 关联创建时间
    """
    __tablename__ = "user_agent_app_association"  # pyright: ignore[reportAssignmentType]

    user_id: int = Field(foreign_key="user.id", index=True)
    agent_app_id: int = Field(foreign_key="agent_app.id", index=True)
    last_synced_workspace_hash: Optional[str] = Field(default=None, max_length=64)
    # BaseModel 已有 created_at / updated_at 字段


# alembic/env.py 新增 import
from app.models.agent_assets import (
    AgentApp, McpServerConfig, SkillAsset, SubAgentConfig,
    UserAgentAppAssociation,  # 新增
)
```

### 2.6 影响下游模块

| 受影响模块 | 变更内容 | 优先级 |
|---|---|---|
| `app/models/agent_assets.py` | 新增 scope/agent_dir/workspace_hash/agent_workspace_status 字段 + UserAgentAppAssociation 模型 | G2 必改 |
| `alembic/versions/<rev>_*.py` | 新增迁移脚本 | G2 必改 |
| `alembic/env.py` | 新增 UserAgentAppAssociation import | G2 必改 |
| `app/services/agents/skills_store.py` | scope 字段过滤逻辑（创建/查询时） | G2 必改 |
| `app/services/agents/bootstrap.py` | 补建 Agent 层目录 + 设置 agent_dir/workspace_hash | G2 必改 |

---

## 第 3 项：复制逻辑核心（已落盘 · v3 原版 + 实现优化）

### 3.0 关键决策回顾

| 决策 | 结论 | 备注 |
|---|---|---|
| **整体架构** | ✅ **v3 原版**（保留 Agent 层 skills/ 目录） | 适合后续 Phase 5+ 扩展增强 |
| `materialize_to_user_combined` 签名 | ✅ **优化为 `(app_cfg, subagent_cfgs)`** | 消除 effective_skill_names / agent_skill_names 参数冗余 |
| 删除 `_read_agent_dir_skill_names` 函数 | ✅ 删除 | 磁盘扫描逻辑冗余（= AgentApp.skill_names） |
| `materialize_for_agent` 函数 | ✅ 保留 | publish 时 Global → Agent 层（v3 核心） |
| `compute_workspace_hash` 函数 | ✅ 保留 | publish 时 Agent 层指纹 |
| `_compute_user_workspace_hash` 函数 | ✅ 保留 | lazy 校验时 User 层实际指纹 |
| `agent_app.workspace_hash` 字段 | ✅ 保留 | publish 时缓存（用于快速判定） |
| Agent 层 `skills/` 目录 | ✅ 保留 | v3 架构核心，Phase 5+ 扩展点 |
| **hash 比对优化** | ✅ 新增 | 所有复制函数加入 source_hash vs existing_hash |
| **API 层业务代码下沉到 service 层** | ✅ 重构 | 项目代码规范（反馈 2） |
| 新增 `app/services/agents/agent_apps_service.py` | ✅ 新建 | 业务编排层 |
| 新增 `app/services/agents/agents_service.py` | ✅ 新建 | SubAgent / skill 校验业务 |
| `ensure_user_workspace_up_to_date` 函数 | ✅ 新增 | lazy 校验核心（已在 G3 §12 同步） |

### 3.1 现状核对

| 现状项 | 文件 / 行号 | 与 v3 修订版的一致性 |
|---|---|---|
| `materialize_for_user` / `sync_user_skills` | `app/services/agents/skills_store.py:380-450` | ✅ 既有，需升级 `sync_user_skills` 接受 `app_id` |
| `refresh_disk_from_db` / `read_global` | `app/services/agents/skills_store.py:190-280` | ✅ 既有，保留 |
| `_atomic_write`（tempfile + os.replace） | `app/services/agents/skills_store.py:120-150` | ✅ 既有，保留 |
| `materialize_into_directory` | `app/services/agents/skills_store.py:560-600` | ✅ 既有，保留（test_runner 使用） |
| `materialize_for_agent` / `materialize_to_user_combined` / `compute_workspace_hash` | **不存在** | ⚠️ 第 3 项新增（v3 修订版） |
| `_read_agent_dir_skill_names` | **不存在** | ⚠️ v3 修订版**删除** |
| `ensure_user_workspace_up_to_date` | **不存在** | ⚠️ v3 修订版新增 |
| `agent_apps_service.py` / `agents_service.py` | **不存在** | ⚠️ v3 修订版新增（service 层） |

### 3.2 子项 §4.1 publish 流程审查（v3 原版）

#### 3.2.1 spec §4.1 原文（v3 原版保留）

```python
# spec §4.1（v3 原版保留）
if app_cfg.skill_names:
    await materialize_for_agent(
        session,
        app_id=app_cfg.id,
        skill_names=list(app_cfg.skill_names),
    )

app_cfg.workspace_hash = compute_workspace_hash(app_cfg.agent_dir)
app_cfg.agent_workspace_status = "active"
```

#### 3.2.2 v3 修订版（service 层）

```python
# app/services/agents/agent_apps_service.py（v3 修订版 · service 层）

async def publish_agent_app(
    session: Session,
    *,
    app_id: int,
    published_by_user_id: int,
) -> AgentApp:
    """发布 AgentApp：Global → Agent 层复制 + 计算 workspace_hash。

    Service 层业务：编排 skills_store / agents_service / db_service。
    """
    # 1. 既有双段校验（保留）
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        raise HTTPException(404, "Agent app not found")
    if app_cfg.status != "draft":
        raise HTTPException(
            422, f"agent app must be in draft, current: {app_cfg.status}"
        )

    subagent_cfgs = await agents_service.list_subagent_cfgs(
        session, app_id=app_id
    )

    # 2. v3 保留：SubAgent skill 可见性校验
    agents_service.validate_subagent_skill_visibility(
        app_cfg=app_cfg, subagent_cfgs=subagent_cfgs
    )

    # 3. v3 保留：确保 AgentApp 私有空间骨架目录存在
    agent_dir = skills_store._agent_dir(app_cfg.id)
    agent_dir.mkdir(parents=True, exist_ok=True)
    if app_cfg.agent_dir != str(agent_dir):
        app_cfg.agent_dir = str(agent_dir)

    # 4. v3 核心：Global → Agent 层复制
    if app_cfg.skill_names:
        await skills_store.materialize_for_agent(
            session,
            app_id=app_cfg.id,
            skill_names=list(app_cfg.skill_names),
        )
        logger.info(
            "agent_workspace_materialized",
            app_id=app_cfg.id,
            skill_count=len(app_cfg.skill_names),
        )

    # 5. v3 核心：计算 workspace_hash
    app_cfg.workspace_hash = skills_store.compute_workspace_hash(
        skills_store._agent_skill_dir(app_cfg.id)
    )
    app_cfg.agent_workspace_status = "active"

    # 6. 状态更新
    app_cfg.status = "published"
    app_cfg.published_at = datetime.utcnow()
    app_cfg.published_by_user_id = published_by_user_id

    session.commit()
    logger.info(
        "agent_app_published",
        app_id=app_id,
        effective_skill_count=len(set(app_cfg.skill_names) | {
            n for cfg in subagent_cfgs for n in (cfg.skill_names or [])
        }),
    )
    return app_cfg
```

#### 3.2.3 API 层重构（仅参数校验）

```python
# app/api/v1/apps.py（v3 修订版 · 仅参数校验）

@router.post("/apps/{app_id}/publish", response_model=ApiResponse[AgentAppRead])
async def publish_agent_app(
    app_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ApiResponse[AgentAppRead]:
    """API 层：仅参数校验 + 权限检查 + 调用 service。"""
    await rbac_require_admin(current_user)  # 仅 admin 可 publish

    app_cfg = await agent_apps_service.publish_agent_app(
        session=session,
        app_id=app_id,
        published_by_user_id=current_user.id,
    )
    return ApiResponse.success(app_cfg)
```

### 3.3 子项 §4.2 associate-user 端点审查（v3 修订）

#### 3.3.1 service 层（v3 修订版核心）

```python
# app/services/agents/agent_apps_service.py（v3 修订版）

async def associate_user_with_app(
    session: Session,
    *,
    app_id: int,
    user_id: int,
) -> None:
    """业务编排：参数二次校验 + workspace 复制 + association upsert。

    v3 修订：
    - 调用 materialize_to_user_combined(app_cfg, subagent_cfgs)（消除参数冗余）
    - upsert UserAgentAppAssociation 记录 + 同步 last_synced_workspace_hash
    """
    # 1. 二次校验（业务规则）
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        raise HTTPException(404, "Agent app not found")
    if app_cfg.status != "published":
        raise HTTPException(422, "Agent app is not published")
    user = await db_service.get_user(user_id)
    if user is None:
        raise HTTPException(404, "User not found")

    # 2. 获取 SubAgent 配置
    subagent_cfgs = await agents_service.list_subagent_cfgs(
        session, app_id=app_id
    )

    # 3. v3 修订：调 store 层（传 app_cfg + subagent_cfgs，不再传 effective_skill_names）
    await skills_store.materialize_to_user_combined(
        session=session,
        user_id=str(user.id),
        app_id=app_id,
        app_cfg=app_cfg,
        subagent_cfgs=subagent_cfgs,
    )

    # 4. upsert association（带 hash 同步）
    actual_hash = skills_store._compute_user_workspace_hash(
        skills_store._user_skill_dir(app_id, str(user.id))
    )
    await db_service.upsert_user_agent_app_association(
        user_id=user.id,
        agent_app_id=app_id,
        last_synced_workspace_hash=actual_hash,
    )

    logger.info(
        "agent_app_user_associated",
        app_id=app_id,
        user_id=user.id,
    )


async def disassociate_user_from_app(
    session: Session,
    *,
    app_id: int,
    user_id: int,
) -> None:
    """取消 user 关联：删除 association + 物理清理 user 私有空间。"""
    # 1. 校验
    assoc = await db_service.get_user_agent_app_association(
        user_id=user_id, agent_app_id=app_id
    )
    if assoc is None:
        raise HTTPException(404, "association not found")

    # 2. 删 association 记录（CASCADE 自动）
    await session.delete(assoc)
    await session.commit()

    # 3. 物理清理 user 私有空间
    user_dir = skills_store._user_dir(app_id, str(user_id))
    if user_dir.exists():
        shutil.rmtree(user_dir)

    logger.info(
        "agent_app_user_disassociated",
        app_id=app_id,
        user_id=user_id,
    )
```

#### 3.3.2 API 层（仅参数校验）

```python
# app/api/v1/apps.py

@router.post(
    "/apps/{app_id}/associate-user/{user_id}",
    response_model=ApiResponse[None],
)
async def associate_user_with_app(
    app_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ApiResponse[None]:
    """API 层：仅参数校验 + 调用 service。"""
    await rbac_require_admin(current_user)
    await agent_apps_service.associate_user_with_app(
        session=session, app_id=app_id, user_id=user_id,
    )
    return ApiResponse.success(None)


@router.delete(
    "/apps/{app_id}/associate-user/{user_id}",
    response_model=ApiResponse[None],
)
async def disassociate_user_from_app(
    app_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ApiResponse[None]:
    """API 层：仅参数校验 + 调用 service。"""
    await rbac_require_admin(current_user)
    await agent_apps_service.disassociate_user_from_app(
        session=session, app_id=app_id, user_id=user_id,
    )
    return ApiResponse.success(None)
```

### 3.4 子项 §4.3 skills_store.py 函数清单（v3 修订版）

#### 3.4.1 路径 helper（与第 1 项 v3 修订版一致）

```python
# app/services/agents/skills_store.py（路径 helper 不变，参见第 1 项 §1.3）
def _data_root() -> Path: ...
def _global_skill_dir(name) -> Path: ...
def _global_skill_file(name) -> Path: ...
def _agent_dir(app_id) -> Path: ...
def _agent_skill_dir(app_id) -> Path: ...
def _agent_skill_file(app_id, name) -> Path: ...
def _user_dir(app_id, user_id) -> Path: ...
def _user_skill_dir(app_id, user_id) -> Path: ...
def _user_skill_file(app_id, user_id, name) -> Path: ...
def _user_session_dir(app_id, user_id) -> Path: ...
def _user_global_dir(user_id) -> Path: ...
```

#### 3.4.2 `materialize_for_agent`（v3 保留 + hash 比对）

```python
async def materialize_for_agent(
    session: Session,
    *,
    app_id: int,
    skill_names: Sequence[str],
) -> None:
    """把 Global skill 复制到 Agent 层（per-AgentApp 隔离）。

    v3 保留：publish 时调用。
    路径：{DATA_ROOT}/agents/<app_id>/skills/<name>/SKILL.md
    hash 比对优化：source_hash != existing_hash 时才写文件。
    """
    agent_skill_root = _agent_skill_dir(app_id)
    agent_skill_root.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    for name in skill_names:
        _validate_skill_name(name)
        target = _agent_skill_file(app_id, name)
        target.parent.mkdir(parents=True, exist_ok=True)

        # hash 比对优化
        global_body = await read_global(session, name)
        global_hash = hashlib.sha256(global_body.encode("utf-8")).hexdigest()

        existing_hash = None
        if target.exists():
            existing_body = await asyncio.to_thread(target.read_text, "utf-8")
            existing_hash = hashlib.sha256(existing_body.encode("utf-8")).hexdigest()

        if existing_hash == global_hash:
            skipped += 1
            continue

        await asyncio.to_thread(_atomic_write, target, global_body)
        written += 1

    logger.info(
        "agent_skills_materialized",
        app_id=app_id,
        requested_count=len(list(skill_names)),
        written=written,
        skipped=skipped,
    )
```

#### 3.4.3 `materialize_to_user_combined`（v3 修订版 · 消除参数冗余）

```python
async def materialize_to_user_combined(
    session: Session,
    *,
    user_id: str,
    app_id: int,
    app_cfg: AgentApp,                       # ✅ 单一配置源
    subagent_cfgs: Sequence[SubAgentConfig], # ✅ 单一配置源
) -> None:
    """聚合 (Global 共享 + Agent 专属) → User 层（per-(app, user) 隔离）。

    v3 修订：
    - 接受 app_cfg + subagent_cfgs（消除 effective_skill_names / agent_skill_names 参数冗余）
    - 内部统一计算 effective_skill_names（Agent ∪ SubAgent）
    - 复制源判断：name ∈ app_cfg.skill_names → Agent 层；否则 → Global 层
    - 路径：{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/<name>/SKILL.md
    - hash 比对优化：仅在 source_hash != existing_hash 时才写文件
    """
    uid = _validate_user_id(user_id)
    user_skill_root = _user_skill_dir(app_id, uid)
    user_skill_root.mkdir(parents=True, exist_ok=True)

    # 1. 计算 effective_skill_names（配置层面）
    effective_skill_names = sorted(
        set(app_cfg.skill_names or [])
        | {n for cfg in subagent_cfgs for n in (cfg.skill_names or [])}
    )

    # 2. Agent 层包含的 skill 集合（= AgentApp.skill_names）
    agent_published_names = set(app_cfg.skill_names or [])

    written = 0
    skipped = 0
    missing = 0
    pruned = 0

    for name in effective_skill_names:
        target = _user_skill_file(app_id, uid, name)
        target.parent.mkdir(parents=True, exist_ok=True)

        # 3. 决定 source path
        if name in agent_published_names:
            source_path = _agent_skill_file(app_id, name)
        else:
            source_path = _global_skill_file(name)

        if not source_path.exists():
            missing += 1
            logger.warning(
                "user_materialize_source_missing",
                source=str(source_path),
                name=name,
                user_id=uid,
                app_id=app_id,
            )
            continue

        # 4. hash 比对优化
        source_body = await asyncio.to_thread(source_path.read_text, "utf-8")
        source_hash = hashlib.sha256(source_body.encode("utf-8")).hexdigest()

        existing_hash = None
        if target.exists():
            existing_body = await asyncio.to_thread(target.read_text, "utf-8")
            existing_hash = hashlib.sha256(existing_body.encode("utf-8")).hexdigest()

        if existing_hash == source_hash:
            skipped += 1
            continue

        # 5. 写入
        await asyncio.to_thread(_atomic_write, target, source_body)
        written += 1

    # 6. prune User 层过期 skill
    expected = set(effective_skill_names)
    pruned = await asyncio.to_thread(
        _prune_stale_user_skills_counted, user_skill_root, expected
    )

    logger.info(
        "user_workspace_materialized_combined",
        user_id=uid,
        app_id=app_id,
        effective_skill_count=len(effective_skill_names),
        written=written,
        skipped=skipped,
        missing=missing,
        pruned=pruned,
    )


def _prune_stale_user_skills_counted(
    user_skill_root: Path, expected: set[str]
) -> int:
    """删除 user_skill_root 下不在 expected 集合中的子目录，返回清理数量。"""
    if not user_skill_root.exists():
        return 0
    pruned = 0
    for entry in user_skill_root.iterdir():
        if entry.is_dir() and entry.name not in expected:
            shutil.rmtree(entry)
            pruned += 1
    return pruned
```

#### 3.4.4 `compute_workspace_hash`（v3 保留）

```python
def compute_workspace_hash(skill_dir: Path) -> str:
    """计算 Agent 层内容指纹：所有 SKILL.md 的 sha256 拼接后再 sha256。

    v3 保留：函数签名不变；调用方必须传 _agent_skill_dir(app.id)。
    """
    if not skill_dir.is_dir():
        return hashlib.sha256(b"").hexdigest()
    file_hashes = []
    for path in sorted(skill_dir.rglob("SKILL.md")):
        content = path.read_bytes()
        file_hashes.append(hashlib.sha256(content).hexdigest())
    return hashlib.sha256(
        "\n".join(file_hashes).encode("utf-8")
    ).hexdigest()
```

#### 3.4.5 `_compute_user_workspace_hash`（v3 新增）

```python
def _compute_user_workspace_hash(user_skill_root: Path) -> str:
    """计算 user 层实际内容的指纹（lazy 校验用）。"""
    if not user_skill_root.exists():
        return hashlib.sha256(b"").hexdigest()
    file_hashes = []
    for path in sorted(user_skill_root.rglob("SKILL.md")):
        content = path.read_bytes()
        file_hashes.append(hashlib.sha256(content).hexdigest())
    return hashlib.sha256(
        "\n".join(file_hashes).encode("utf-8")
    ).hexdigest()
```

#### 3.4.6 `ensure_user_workspace_up_to_date`（v3 新增 · service 层）

```python
# app/services/agents/agent_apps_service.py

async def ensure_user_workspace_up_to_date(
    session: Session,
    *,
    user_id: int,
    app_id: int,
) -> None:
    """Lazy 校验：User 层与 (Global + Agent) 集合是否一致，不一致则同步。

    触发时机（由 G3 决定）：
    - POST /sessions（创建 session）入口
    - GET /sessions/{session_id}（加载 session）入口

    内部行为：
    - 计算 user 层实际 hash
    - 计算 expected hash（基于 effective_skill_names 的实际文件内容 hash）
    - 不一致 → 调用 materialize_to_user_combined 同步
    - 一致 → 跳过
    """
    # 1. 校验
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        raise ValueError(f"agent app {app_id} not found")
    assoc = await db_service.get_user_agent_app_association(
        user_id=user_id, agent_app_id=app_id
    )
    if assoc is None:
        raise HTTPException(
            404, f"user {user_id} not associated with app {app_id}"
        )

    # 2. 计算 user 层实际 hash
    user_skill_root = skills_store._user_skill_dir(app_id, str(user_id))
    actual_hash = skills_store._compute_user_workspace_hash(user_skill_root)

    # 3. 计算 expected hash
    subagent_cfgs = await agents_service.list_subagent_cfgs(
        session, app_id=app_id
    )
    expected_hash = await _compute_effective_workspace_hash(
        session=session,
        app_cfg=app_cfg,
        subagent_cfgs=subagent_cfgs,
    )

    # 4. 比对 hash
    if actual_hash == expected_hash and user_skill_root.exists():
        logger.debug(
            "user_workspace_up_to_date",
            user_id=user_id,
            app_id=app_id,
        )
        return

    # 5. 不一致：触发同步
    logger.info(
        "user_workspace_drift_detected",
        user_id=user_id,
        app_id=app_id,
        actual_hash=actual_hash,
        expected_hash=expected_hash,
    )
    await skills_store.materialize_to_user_combined(
        session=session,
        user_id=str(user_id),
        app_id=app_id,
        app_cfg=app_cfg,
        subagent_cfgs=subagent_cfgs,
    )

    # 6. 更新 association 同步状态
    new_actual_hash = skills_store._compute_user_workspace_hash(
        user_skill_root
    )
    await db_service.update_association_synced_hash(
        user_id=user_id,
        agent_app_id=app_id,
        last_synced_workspace_hash=new_actual_hash,
    )


async def _compute_effective_workspace_hash(
    session: Session,
    *,
    app_cfg: AgentApp,
    subagent_cfgs: Sequence[SubAgentConfig],
) -> str:
    """计算 effective_skill_names 对应的全局指纹（基于实际文件内容）。

    策略：对每个 effective skill，按"是否在 Agent 层"决定 source path，
    然后取 source 内容的 sha256 拼接再 sha256。
    这与 materialize_to_user_combined 的 source 判断逻辑保持一致。
    """
    agent_published_names = set(app_cfg.skill_names or [])
    file_hashes: list[str] = []

    for name in sorted(
        set(app_cfg.skill_names or [])
        | {n for cfg in subagent_cfgs for n in (cfg.skill_names or [])}
    ):
        if name in agent_published_names:
            source_path = skills_store._agent_skill_file(app_cfg.id, name)
        else:
            source_path = skills_store._global_skill_file(name)

        if not source_path.exists():
            logger.warning(
                "effective_hash_source_missing",
                name=name,
                source=str(source_path),
            )
            continue

        content = await asyncio.to_thread(source_path.read_bytes)
        file_hashes.append(hashlib.sha256(content).hexdigest())

    return hashlib.sha256(
        "\n".join(file_hashes).encode("utf-8")
    ).hexdigest()
```

#### 3.4.7 **删除 `_read_agent_dir_skill_names` 函数**

```python
# v3 修订版：删除该函数
# 原因：函数语义已被 app_cfg.skill_names 完全覆盖（Agent 层 = publish 时 AgentApp.skill_names 的全量副本）
# 调用方全部改为：从 app_cfg.skill_names 直接派生
```

### 3.5 子项 §4.4 既有函数复用（v3 修订）

| 函数 | 现状 | v3 修订版处理 | 备注 |
|---|---|---|---|
| `materialize_for_user(session, user_id, skill_names)` | 仅 Global → User | ✅ **保留** | 供独立 skill 复制（无 Agent 上下文） |
| `sync_user_skills(session, user_id, associated_names)` | 重置 User 目录 | ✅ **保留 + 签名升级**：接受 `app_id` | v3 下 User 层嵌套在 agent 下，需要 `app_id` |
| `refresh_disk_from_db(session, name)` | Global 刷新 | ✅ **保留** | 作用域限定为 Global |
| `read_global(session, name)` | Global 读取 | ✅ **保留** | |
| `materialize_into_directory(session, target_dir, skill_names)` | 复制到任意目录 | ✅ **保留** | 供 test_runner 使用 |

#### 3.5.1 `sync_user_skills` 签名升级（v3 修订）

```python
# app/services/agents/skills_store.py（v3 修订版）

async def sync_user_skills(
    session: Session,
    *,
    user_id: str,
    app_id: int,                          # ✅ v3 新增（必填）
    associated_names: Sequence[str],
) -> None:
    """重置 user 层 skill 目录为 associated_names 集合（v3 嵌套路径）。

    v3 修订：路径改为 {DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/。
    """
    uid = _validate_user_id(user_id)
    user_skill_root = _user_skill_dir(app_id, uid)
    user_skill_root.mkdir(parents=True, exist_ok=True)

    existing_names = set(
        entry.name for entry in user_skill_root.iterdir() if entry.is_dir()
    )
    target_names = set(associated_names)

    # 清理多余
    for name in existing_names - target_names:
        target = user_skill_root / name
        shutil.rmtree(target)

    # 写入缺失（hash 比对优化）
    for name in target_names - existing_names:
        body = await read_global(session, name)
        target = _user_skill_file(app_id, uid, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_atomic_write, target, body)

    logger.info(
        "user_skills_synced",
        user_id=uid,
        app_id=app_id,
        skill_count=len(target_names),
    )
```

### 3.6 子项 §4.5 边界场景（v3 新增）

#### 3.6.1 场景 A：User 取消关联后再次关联

| 触发 | 行为 |
|---|---|
| 取消关联 → 删 `agents/<app_id>/users/<user_id>/` | 物理清理（service `disassociate_user_from_app`） |
| 再次关联 → recreate 目录 + 全量复制 | 幂等恢复（service `associate_user_with_app`） |

#### 3.6.2 场景 B：User 关联多个 AgentApp

| 触发 | 行为 |
|---|---|
| User A 关联 agent1 和 agent2 | 各 agent 下独立 user 目录 |
| 路径：`agents/1/users/100/` 和 `agents/2/users/100/` | 自然隔离 |

✅ v3 设计下自然隔离。

#### 3.6.3 场景 C：AgentApp publish 后已关联用户

| 触发 | 行为 |
|---|---|
| publish 修改了 agent 层 + 重新计算 workspace_hash | 不主动同步 user 层 |
| 下次 chat session 启动 → lazy 校验触发同步 | ✅ 第 1 项 G 方案 |

#### 3.6.4 场景 D：删除 Global skill 后 Agent 层残留

| 触发 | 行为 |
|---|---|
| Skill X 被 hard delete | skill_asset 表删除 + Global 物理清理 |
| Agent 层如有 X 副本 | 需 `prune_skill_from_all_layers(name)` 清理 |

```python
# app/services/agents/skills_store.py（v3 新增）

async def prune_skill_from_all_layers(
    session: Session,
    *,
    name: str,
) -> int:
    """从 Global / 所有 Agent 层 / 所有 User 层删除 skill。返回清理总数。

    注意：必须在 SkillAsset 硬删除前调用。
    """
    _validate_skill_name(name)
    total = 0

    # 1. Global 层
    global_dir = _global_skill_dir(name)
    if global_dir.exists():
        shutil.rmtree(global_dir)
        total += 1

    # 2. 所有 Agent 层
    agent_apps = session.exec(select(AgentApp)).all()
    for app_cfg in agent_apps:
        agent_skill_dir = _agent_skill_dir(app_cfg.id) / name
        if agent_skill_dir.exists():
            shutil.rmtree(agent_skill_dir)
            total += 1

    # 3. 所有 User 层（遍历所有关联）
    from app.models.agent_assets import UserAgentAppAssociation
    assocs = session.exec(select(UserAgentAppAssociation)).all()
    for assoc in assocs:
        user_skill_dir = (
            _user_skill_dir(assoc.agent_app_id, str(assoc.user_id)) / name
        )
        if user_skill_dir.exists():
            shutil.rmtree(user_skill_dir)
            total += 1

    logger.info(
        "skill_pruned_from_all_layers",
        name=name,
        total_dirs_cleaned=total,
    )
    return total
```

#### 3.6.5 场景 E：磁盘丢失 / DB 元数据幸存

| 触发 | 行为 |
|---|---|
| User 层目录丢失（docker-compose 未挂载） | DB association 表仍在 |
| lazy 校验 → `_compute_user_workspace_hash` → 空目录 hash | 与 expected hash 不匹配 → 触发同步 ✅ |

**v3 自愈能力**：依赖 `ensure_user_workspace_up_to_date` 自动恢复。

#### 3.6.6 场景 F：删除 AgentApp 时的连锁清理

```python
# app/services/agents/agent_apps_service.py（v3 新增）

async def delete_agent_app(
    session: Session,
    *,
    app_id: int,
) -> None:
    """删除 AgentApp：DB CASCADE + 物理清理整个 agents/<id>/ 子树。"""
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        raise HTTPException(404, "Agent app not found")

    # 1. DB 删除（CASCADE 清理 association 表）
    session.delete(app_cfg)
    session.commit()

    # 2. 物理清理 AgentApp 整个子树（含所有 user 在该 app 下的数据）
    agent_root = skills_store._agent_dir(app_id)
    if agent_root.exists():
        shutil.rmtree(agent_root)

    logger.info("agent_app_deleted_with_workspace", app_id=app_id)
```

### 3.7 影响下游模块

| 受影响模块 | 变更内容 | 优先级 |
|---|---|---|
| `app/services/agents/skills_store.py` | 新增 / 删除 / 重构多个函数（v3 修订版签名） | G2 必改 |
| `app/services/agents/agent_apps_service.py`（新） | 业务编排层（publish / associate / lazy 校验 / delete） | G2 必改 |
| `app/services/agents/agents_service.py`（新） | SubAgent / skill 校验业务 | G2 必改 |
| `app/api/v1/apps.py` | 所有端点重构为仅参数校验 + service 调用 | G2 必改 |
| `app/services/agents/bootstrap.py` | `_ensure_agent_dir` 函数（v3 修订） | G2 必改 |
| `app/services/db_service.py` | 新增 `upsert_user_agent_app_association` / `update_association_synced_hash` | G2 必改 |
| `app/models/agent_assets.py` | `sync_user_skills` 签名升级（v3 修订） | G2 必改 |

### 3.8 已确认事项

| # | 议题 | 用户确认 |
|---|---|---|
| 1 | v3 原版架构保留（不采纳 v4 简化版） | ✅ |
| 2 | `materialize_to_user_combined` 签名优化为 `(app_cfg, subagent_cfgs)` | ✅ |
| 3 | 删除 `_read_agent_dir_skill_names` 函数 | ✅ |
| 4 | 保留 `agent_app.workspace_hash` 字段 | ✅ |
| 5 | hash 比对优化 | ✅ |
| 6 | API 层只做参数校验 + service 层业务 | ✅ |
| 7 | `sync_user_skills` 签名升级（接受 `app_id`） | ✅ |

---

## 第 4 项：fingerprint 锁定 + 启动校验（已落盘 · v3 修订版）

### 4.0 关键决策回顾

| 决策 | 结论 | 备注 |
|---|---|---|
| 启动校验函数名 | ✅ **重命名为 `ensure_all_agent_workspaces`** | 去除 "default" 误导 |
| 已 `active` 状态处理 | ✅ **仍重新校验**（防止目录丢失） | docker-compose 重建场景 |
| `draft` 状态处理 | ✅ **仅创建骨架目录**，status 保持 `pending` | 状态机正确性 |
| `published` 状态处理 | ✅ **校验目录 + 内容**，必要时重新 materialize | 自愈能力 |
| 启动失败容错 | ✅ **单 App 异常隔离**（try/except） | 不影响整体启动 |
| **PATCH 状态机语义澄清** | ✅ **解读 B：published → draft** | spec 原文"自动 publish 回退 draft"歧义 |
| PATCH 后 workspace_hash 处理 | ✅ **清空**（标记需要重新 publish） | |
| Global skill body 更新 → Agent 层 | ✅ **不主动同步**（spec 保持） | 下次 publish 触发 |
| User 层外部修改 → fingerprint | ✅ **不影响**（User 层是消费端） | spec 保持 |

### 4.1 现状核对

| 现状项 | 文件 / 行号 | 与 v3 修订版的一致性 |
|---|---|---|
| `bootstrap.py` 既有 `ensure_default_agent_app` | `app/services/agents/bootstrap.py:50-100` | ✅ 既有，保留 |
| `ensure_default_agent_workspace` | **不存在** | ⚠️ v3 修订版重命名为 `ensure_all_agent_workspaces` |
| `app/main.py` lifespan 调用 | 待确认 | G2 必改 |
| `AgentApp.workspace_hash` 字段 | **不存在** | ⚠️ 第 2 项新增 |
| `AgentApp.agent_workspace_status` 字段 | **不存在** | ⚠️ 第 2 项新增 |
| `AgentApp.patch_agent_app` 函数 | `app/api/v1/apps.py` | ⚠️ v3 修订版新增状态机处理 |

### 4.2 §5.1 Agent 层变更检测审查

#### 4.2.1 v3 修订版变更检测矩阵

| 触发场景 | fingerprint 重算？ | Agent 层刷新？ | v3 修订 |
|---|---|---|---|
| Global skill body 更新（`PATCH /skills/<name>` body） | ❌ 否 | ❌ 否（下次 publish 刷新） | ✅ 保持 spec |
| Global skill 创建 / 删除 | ❌ 否 | ❌ 否（下次 publish 刷新） | ✅ 保持 spec（spec 隐含） |
| **AgentApp publish 时** | ✅ 重算 Agent 层 + workspace_hash | ✅ 复制 | ✅ 保持 spec |
| **AgentApp PATCH**（编辑字段） | ⚠️ workspace_hash **清空** | ❌ 否（保留旧值） | ⚠️ 状态机修订 |
| AgentApp PATCH 后再次 publish | ✅ 重算 | ✅ 复制 | ✅ 保持 spec |
| User 层文件外部修改 | ❌ 否 | ❌ 否 | ✅ 保持 spec |
| **AgentApp 删除** | 不适用 | ✅ 物理清理（agents/<id>/ 整个子树） | ✅ 第 3 项场景 F |
| **Skill 硬删除** | ❌ 否 | ❌ 否（需 `prune_skill_from_all_layers`） | ✅ 第 3 项场景 D |

#### 4.2.2 PATCH 状态机语义澄清

**spec 原文（§5.1）**：

> AgentApp PATCH（编辑 system_prompt / allowed_tools / skill_names）| ✅ PATCH 后自动 publish 回退 draft（既有逻辑）

**歧义点**：spec 表述"自动 publish 回退 draft"含义不清。v3 修订版采用**解读 B**：

| 解读 | 含义 | v3 决策 |
|---|---|---|
| 解读 A | PATCH 后自动 publish | ❌ 否决（每次 PATCH 都触发完整 publish 不合理） |
| **解读 B**（推荐） | PATCH 后状态从 `published` 退回 `draft`，需重新 publish 才生效 | ✅ 接受 |

**v3 修订版 `patch_agent_app`**（service 层）：

```python
# app/services/agents/agent_apps_service.py（v3 修订版）

async def patch_agent_app(
    session: Session,
    *,
    app_id: int,
    patch_data: AgentAppPatch,
    patched_by_user_id: int,
) -> AgentApp:
    """编辑 AgentApp（system_prompt / allowed_tools / skill_names）。

    v3 行为：
    - 编辑字段时应用 patch
    - 如果之前 status='published'，则回退到 'draft'
    - workspace_hash 清空（标记需要重新 publish）
    """
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        raise HTTPException(404, "Agent app not found")

    # 1. 应用 patch
    if patch_data.system_prompt is not None:
        app_cfg.system_prompt = patch_data.system_prompt
    if patch_data.allowed_tools is not None:
        app_cfg.allowed_tools = patch_data.allowed_tools
    if patch_data.skill_names is not None:
        # 校验所有 skill 存在
        await skills_store.validate_skill_names_exist(
            session, skill_names=patch_data.skill_names
        )
        app_cfg.skill_names = patch_data.skill_names

    # 2. v3 关键：状态机处理
    if app_cfg.status == "published":
        app_cfg.status = "draft"
        app_cfg.workspace_hash = None
        logger.info(
            "agent_app_patched_revert_to_draft",
            app_id=app_id,
            patched_by=patched_by_user_id,
        )

    session.commit()
    return app_cfg
```

### 4.3 §5.2 启动校验审查（v3 修订版）

#### 4.3.1 问题诊断

| # | 问题 | 严重度 | 影响 |
|---|---|---|---|
| 1 | 函数名 `ensure_default_agent_workspace` 误导 | 中 | 代码可读性 |
| 2 | 已 `active` 状态直接跳过，不校验目录是否丢失 | **高** | docker-compose 重建场景不自愈 |
| 3 | `draft` 状态也设 `active` | 高 | 状态机错误 |
| 4 | 单次 commit，失败时部分进度丢失 | 中 | 启动容错 |
| 5 | logging `total` 字段没具体值 | 低 | 可观测性 |
| 6 | 缺少单 App 异常隔离 | 中 | 启动容错 |

#### 4.3.2 v3 修订版 `ensure_all_agent_workspaces`

```python
# app/services/agents/bootstrap.py（v3 修订版）

async def ensure_all_agent_workspaces(session: Session) -> None:
    """启动时为所有 AgentApp 补建 workspace（v3 修订版）。

    v3 修订要点：
    - 已 active 状态仍校验（防止目录丢失）
    - draft 状态保持 pending（仅骨架目录）
    - 单 App 异常隔离（try/except）
    - 完整 stats 日志
    """
    apps = session.exec(select(AgentApp)).all()

    stats = {
        "processed": 0,
        "rematerialized": 0,
        "skipped": 0,
        "failed": 0,
    }

    for app in apps:
        stats["processed"] += 1
        try:
            agent_dir = skills_store._agent_dir(app.id)
            agent_skill_dir = skills_store._agent_skill_dir(app.id)

            # 1. 确保骨架目录存在
            agent_dir.mkdir(parents=True, exist_ok=True)
            if app.agent_dir != str(agent_dir):
                app.agent_dir = str(agent_dir)

            # 2. 根据状态决定处理
            if app.status == "published":
                needs_rematerialize = (
                    app.agent_workspace_status != "active"
                    or not agent_skill_dir.exists()
                    or not any(agent_skill_dir.iterdir())
                )

                if needs_rematerialize:
                    logger.info(
                        "agent_workspace_rematerialize_start",
                        app_id=app.id,
                        reason=(
                            "status_pending"
                            if app.agent_workspace_status != "active"
                            else "dir_missing_or_empty"
                        ),
                    )
                    if app.skill_names:
                        await skills_store.materialize_for_agent(
                            session,
                            app_id=app.id,
                            skill_names=list(app.skill_names),
                        )
                    app.workspace_hash = skills_store.compute_workspace_hash(
                        agent_skill_dir
                    )
                    app.agent_workspace_status = "active"
                    stats["rematerialized"] += 1
                    logger.info(
                        "agent_workspace_rematerialize_done",
                        app_id=app.id,
                        skill_count=len(app.skill_names or []),
                        workspace_hash_prefix=(
                            app.workspace_hash[:16] + "..."
                            if app.workspace_hash else None
                        ),
                    )
                else:
                    stats["skipped"] += 1
                    logger.debug(
                        "agent_workspace_already_active",
                        app_id=app.id,
                    )
            else:
                # Draft / 其他状态：仅确保骨架目录
                if app.agent_workspace_status != "pending":
                    app.agent_workspace_status = "pending"
                stats["skipped"] += 1

        except Exception as exc:
            stats["failed"] += 1
            logger.exception(
                "agent_workspace_bootstrap_failed",
                app_id=app.id,
                error=str(exc),
            )

    session.commit()
    logger.info(
        "agent_workspace_bootstrap_completed",
        **stats,
    )
```

#### 4.3.3 修订对比

| 维度 | spec 原版 | v3 修订版 |
|---|---|---|
| 函数名 | `ensure_default_agent_workspace` | `ensure_all_agent_workspaces` |
| 已 active 状态处理 | 跳过 | 重新校验（防目录丢失） |
| draft 状态处理 | 设 active | 保持 pending |
| published 状态处理 | 仅当目录不存在时补建 | 校验目录 + 内容，必要时 rematerialize |
| workspace_hash 更新 | 仅在补建时计算 | 每次 rematerialize 时重算 |
| 异常隔离 | 单次 commit 中途失败全丢 | 单 App try/except 隔离 |
| logging | `total=...`（无具体值） | 完整 stats |

### 4.4 §5.1 新增场景（v3 完善）

#### 4.4.1 场景 G：Global skill body 更新后 Agent 层 stale

| 触发 | 行为 |
|---|---|
| Admin `PATCH /skills/<name>` body 更新 | Global 层刷新 + DB 更新 |
| Agent 层（如果有）**不刷新** | 按 spec 设计 |
| 下次该 AgentApp publish 时 | Agent 层刷新（`materialize_for_agent` 重新复制） |

**验证**：publish 是 Agent 层唯一的同步触发点。

#### 4.4.2 场景 H：AgentApp PATCH 后 Agent 层与 workspace_hash 不一致

| 触发 | 行为 |
|---|---|
| PATCH 编辑字段 | Agent 层文件不变 + workspace_hash 清空 |
| 下次 publish 时 | 重新复制 + 重新计算 workspace_hash |

**正确性**：DB workspace_hash=None 表示"未 publish"，与 Agent 层 stale 状态不冲突。

#### 4.4.3 场景 I：启动时 active 状态的 AgentApp 目录丢失

| 触发 | 行为 |
|---|---|
| 容器重建 → agent_dir 被删除 | DB status='active' 但 agent_dir 不存在 |
| 启动校验 → `needs_rematerialize=True`（目录不存在） | **自动重新 materialize** ✅ |

**v3 修订版自愈能力**：依赖 `needs_rematerialize` 三条件判断。

### 4.5 影响下游模块

| 受影响模块 | 变更内容 | 优先级 |
|---|---|---|
| `app/services/agents/bootstrap.py` | 重命名函数 + v3 修订逻辑 | G2 必改 |
| `app/services/agents/agent_apps_service.py` | `patch_agent_app` 函数新增状态机处理 | G2 必改 |
| `app/main.py` lifespan | 调用 `ensure_all_agent_workspaces` | G2 必改 |
| `app/services/agents/skills_store.py` | 新增 `validate_skill_names_exist` 函数 | G2 必改 |

### 4.6 已确认事项

| # | 议题 | 用户确认 |
|---|---|---|
| 1 | 启动校验函数重命名（去除 "default"） | ✅ |
| 2 | 已 active 状态仍重新校验（防目录丢失） | ✅ |
| 3 | draft 状态保持 pending（不自动 active） | ✅ |
| 4 | 单 App 异常隔离（try/except） | ✅ |
| 5 | PATCH 状态机解读 B（published → draft） | ✅ |
| 6 | PATCH 后 workspace_hash 清空 | ✅ |
| 7 | Global skill body 更新 → Agent 层不主动同步 | ✅ |

---

## 第 5 项：运行时读取路径调整（已落盘 · v3 修订版）

### 5.0 关键决策回顾

| 决策 | 结论 | 备注 |
|---|---|---|
| `_COMPILE_USER_ID = "system"` 临时简化 | ✅ **正式删除** | v3 阶段必须穿真实 user_id |
| `_runtime_cache` cache key | ✅ **升级为 `(app_id, user_id, fingerprint)`** | 添加 user_id 维度 |
| Cache 淘汰逻辑 | ✅ **按 `(app_id, user_id)` 维度清理** | 用户隔离失效时正确淘汰 |
| `compile_agent_app` FilesystemBackend 路径 | ✅ **更新为 `{DATA_ROOT}/agents/<app_id>/users/<user_id>/`** | v3 嵌套路径 |
| `sync_user_skills` 调用 | ✅ **保持现有签名**（接受 app_id） | 第 3 项已升级 |
| `_validate_user_workspace` 启动校验 | ✅ **替换为 `ensure_all_agent_workspaces`**（第 4 项）+ `ensure_user_workspace_up_to_date` lazy | 职责拆分 |
| `_build_filesystem_backend` 单独函数 | ✅ **不抽取** | 单点调用，`compile_agent_app` 内联即可 |
| `assembly.compute_fingerprint` 是否纳入 `workspace_hash` | ✅ **不纳入**（`skill_hashes` 已覆盖 DB 真相） | 避免双重指纹；spec §7 该项**否决** |
| SubAgent standalone 测试读取层 | ✅ **保持 Global-only**（当前实现）+ 文档说明 MVP 限制 | standalone 无 agent 上下文 |
| `materialize_into_combined_directory` 函数 | ✅ **接受 `app_id` 参数** | 由调用方传 parent agent 上下文 |
| Chatbot 启动期 User 层校验 | ✅ **不在 runtime 内做**，由 `bootstrap.ensure_all_agent_workspaces`（启动期） + `ensure_user_workspace_up_to_date`（lazy）覆盖 | 启动期 + 请求热路径双层兜底 |

### 5.1 现状核对

| 现状项 | 文件 / 行号 | 与 v3 修订版的一致性 |
|---|---|---|
| `_COMPILE_USER_ID = "system"` | `app/services/agents/runtime.py:86` | ⚠️ v3 必删（Phase 1 临时） |
| `_runtime_cache: dict[tuple[int, str], AgentAppRuntime]` | `app/services/agents/runtime.py:562` | ⚠️ 缺少 user_id 维度 |
| `_runtime_cache.get((app_cfg.id, fingerprint))` | `app/services/agents/runtime.py:769` | ⚠️ cache key 升级 |
| `user_id=_COMPILE_USER_ID` 传入 `assembly.get_or_compile` | `app/services/agents/runtime.py:791` | ⚠️ 传真实 user_id |
| `for stale_key in [key for key in _runtime_cache if key[0] == app_cfg.id]` | `app/services/agents/runtime.py:814-816` | ⚠️ 按 `(app_id, user_id)` 清理 |
| `_runtime_cache[(app_cfg.id, fingerprint)] = runtime_obj` | `app/services/agents/runtime.py:816` | ⚠️ cache key 升级 |
| `user_skill_root = Path(settings.SKILLS_ROOT) / "users" / str(user_id)` | `app/services/agents/assembly.py:478` | ⚠️ v3 路径需重写 |
| `backend = FilesystemBackend(root_dir=str(user_skill_root))` | `app/services/agents/assembly.py:479` | ⚠️ v3 路径需重写 |
| `await sync_user_skills(session, user_id, effective_skill_names)` | `app/services/agents/assembly.py:446` | ⚠️ 调用旧签名（缺 app_id）；第 3 项已升 |
| `get_runtime(session, agent_app_id)` 接口 | `app/services/agents/runtime.py:738` | ⚠️ v3 需传 user_id |
| `_APP_FIELDS`（fingerprint 输入） | `app/services/agents/assembly.py:574-583` | ✅ 不含 `workspace_hash`（skill_hashes 已覆盖） |
| `run_subagent_once` 中 `materialize_into_directory(session, tmp_skills_root, skills)` | `app/services/agents/test_runner.py:175` | ⚠️ spec §6.2 要求 combined，但 MVP 限制 |
| `_build_filesystem_backend` 函数 | **不存在** | ⚠️ 实际为内联（`assembly.py:479`） |
| `_validate_user_workspace` 函数 | **不存在**（spec 提及未实现） | ✅ v3 用 `ensure_all_agent_workspaces` + lazy 覆盖 |
| `main.py` lifespan 中 `_warm_agent_apps` | `app/main.py:55-84` | ✅ 既有；启动期预编译 |
| `bootstrap.ensure_default_agent_app` | `app/services/agents/bootstrap.py:50-100` | ✅ 既有；保留 |

### 5.2 子项 §6.1 Chatbot runtime 审查（v3 修订版）

#### 5.2.1 spec §6.1 原文（待修订）

```python
# spec §6.1（原文）
async def _validate_user_workspace(user_id: str, app_id: int) -> None:
    """启动校验：User 层 skill 与 (Global + Agent) 集合一致。"""
    user_skills = _read_user_skill_names(user_id)
    expected = set(_read_agent_dir_skill_names(_agent_skill_dir(app_id)))
    if user_skills != expected:
        logger.warning("user_workspace_drift", ...)
```

**spec §6.1 关键问题**：

| # | 问题 | 严重度 | 影响 |
|---|---|---|---|
| 1 | `_validate_user_workspace` 与 `ensure_user_workspace_up_to_date` 职责重叠 | 高 | 重复校验 + 概念混淆 |
| 2 | `_read_agent_dir_skill_names` 函数已被否决（第 3 项） | 高 | 与磁盘耦合 |
| 3 | 函数挂在 `_build_filesystem_backend` 内（spec 表述）但实际 `compile_agent_app` 内联 | 中 | 与代码结构不符 |
| 4 | `_COMPILE_USER_ID` 临时简化未清理 | **高** | v3 阶段 user_id 维度必须真实 |
| 5 | `_runtime_cache` key 缺 user_id 维度（同一 app 多 user 时串台） | **高** | 编译缓存污染 |
| 6 | `compute_fingerprint` 需新增 `workspace_hash` 字段（spec §7 DoD） | 中 | 与 `_load_skill_hashes` 冗余 |

#### 5.2.2 v3 修订版总览

```
启动期：  bootstrap.ensure_all_agent_workspaces(...)
           └─ 遍历所有 AgentApp，补全 Agent 层 + workspace_hash
              （第 4 项已落盘）

请求热路径：chatbot runtime.ainvoke(...)
            └─ get_runtime(session, agent_app_id, user_id)   ← 新增 user_id
               ├─ ensure_user_workspace_up_to_date(...)       ← 第 3 项已落盘
               └─ _runtime_cache.get((app_id, user_id, fingerprint))
                  └─ 命中：直接返回
                  └─ 未命中：
                     ├─ evict (app_id, user_id, *) stale keys
                     ├─ assembly.get_or_compile(..., user_id=user_id, app_id=app_id)
                     └─ _runtime_cache[(app_id, user_id, fingerprint)] = runtime
```

#### 5.2.3 v3 修订版 `runtime.py` 关键改造点

##### (1) 删除 `_COMPILE_USER_ID`

```python
# app/services/agents/runtime.py（v3 修订版）

# 旧：
_COMPILE_USER_ID = "system"

# 新：直接删除该常量（user_id 由 get_runtime 调用方传入）
```

**理由**：
- v3 阶段 user 层路径依赖 `(app_id, user_id)`
- 继续用 `"system"` 会把所有 user 的 skills 都物化到同一目录，破坏隔离
- 调用方（chatbot session 启动）已有真实 user_id 可传

##### (2) `_runtime_cache` key 升级为三元组

```python
# app/services/agents/runtime.py（v3 修订版）

# 旧：
_runtime_cache: dict[tuple[int, str], AgentAppRuntime] = {}

# 新：
_runtime_cache: dict[tuple[int, int, str], AgentAppRuntime] = {}
#                                                  ↑↑↑↑↑↑↑
#                                  (AgentApp.id, user_id, fingerprint)
```

**理由**：
- 同一 `(app_id, fingerprint)` 下，不同 user 的 skills backend 路径不同（user 层隔离）
- 编译 cache 必须按 `(app_id, user_id)` 隔离，否则 user A 的 runtime 被 user B 复用
- Phase 2 实现真正 per-user 隔离的关键

##### (3) `get_runtime` 接受 user_id

```python
# app/services/agents/runtime.py（v3 修订版）

async def get_runtime(
    session: Session,
    agent_app_id: Optional[str],
    *,
    user_id: int,                                # ✅ v3 必填
) -> AgentAppRuntime:
    """加载、编译（缓存）并返回 AgentApp 的运行时。

    v3 修订：
    - 新增 user_id 必填参数（per-user skills 隔离）
    - cache key 升级为 (app_id, user_id, fingerprint)
    - 调 ensure_user_workspace_up_to_date（lazy 校验）
    """
    app_cfg = await _resolve_agent_app(session, agent_app_id)
    if app_cfg.status != "published":
        raise ValueError(
            f"agent app {app_cfg.name!r} is not published (status={app_cfg.status})"
        )

    # ✅ v3 关键：lazy 校验 User 层
    await agent_apps_service.ensure_user_workspace_up_to_date(
        session=session,
        user_id=user_id,
        app_id=app_cfg.id,
    )

    subagent_cfgs = _load_subagents(session, app_cfg.subagent_names)
    skill_hashes = await _load_skill_hashes(
        session, app_cfg.skill_names, subagent_cfgs
    )
    mcp_fingerprint = await _load_mcp_fingerprint(session)
    model_fingerprint, resolved_model_name = await _load_model_fingerprint(
        session, app_cfg, subagent_cfgs
    )
    fingerprint = assembly.compute_fingerprint(
        app_cfg, subagent_cfgs, skill_hashes, mcp_fingerprint, model_fingerprint
    )

    # ✅ v3 修订：cache key 增加 user_id
    cached = _runtime_cache.get((app_cfg.id, user_id, fingerprint))
    if cached is not None:
        logger.debug(
            "agent_app_runtime_cache_hit",
            app_name=app_cfg.name,
            app_id=app_cfg.id,
            user_id=user_id,
        )
        return cached

    checkpointer = await _build_checkpointer()

    degraded = False
    if app_cfg.engine == "deepagents":
        compile_cfg = app_cfg
        if checkpointer is None and app_cfg.interrupt_on:
            logger.warning(
                "hil_disabled_no_checkpointer",
                app_name=app_cfg.name,
                app_id=app_cfg.id,
                user_id=user_id,
            )
            compile_cfg = app_cfg.model_copy(update={"interrupt_on": {}})
            degraded = True
        graph = await assembly.get_or_compile(
            compile_cfg,
            subagent_cfgs=subagent_cfgs,
            skill_hashes=skill_hashes,
            mcp_fingerprint=mcp_fingerprint,
            model_fingerprint=model_fingerprint,
            user_id=str(user_id),                  # ✅ v3 传真实 user_id
            app_id=app_cfg.id,                     # ✅ v3 必传
            session=session,
            checkpointer=checkpointer,
        )
        runtime_obj: AgentAppRuntime = DeepAgentsAppRuntime(
            app_cfg=compile_cfg,
            graph=graph,
            checkpointer=checkpointer,
            resolved_model_name=resolved_model_name,
        )
    elif app_cfg.engine == "workflow":
        runtime_obj = WorkflowAppRuntime(
            app_cfg=app_cfg, resolved_model_name=resolved_model_name
        )
    else:
        raise ValueError(
            f"unknown engine {app_cfg.engine!r} for agent app {app_cfg.name!r}"
        )

    if degraded:
        logger.debug(
            "agent_app_runtime_not_cached_degraded",
            app_name=app_cfg.name,
            app_id=app_cfg.id,
            user_id=user_id,
        )
        return runtime_obj

    # ✅ v3 修订：evict 按 (app_id, user_id) 维度
    for stale_key in [
        key for key in _runtime_cache
        if key[0] == app_cfg.id and key[1] == user_id
    ]:
        del _runtime_cache[stale_key]
    _runtime_cache[(app_cfg.id, user_id, fingerprint)] = runtime_obj
    logger.info(
        "agent_app_runtime_ready",
        app_name=app_cfg.name,
        app_id=app_cfg.id,
        user_id=user_id,
        engine=app_cfg.engine,
    )
    return runtime_obj
```

##### (4) `assembly.compile_agent_app` FilesystemBackend 路径改造

```python
# app/services/agents/assembly.py（v3 修订版 · 第 5 项关键改造）

async def compile_agent_app(
    app_cfg: AgentApp,
    *,
    subagent_cfgs: Sequence[SubAgentConfig],
    user_id: str,
    app_id: int,                                # ✅ v3 新增（必填）
    session: Session,
    checkpointer: Checkpointer | None = None,
) -> CompiledStateGraph:
    """装配并编译 AgentApp 为可执行的 deepagents 图（v3 修订版）。

    v3 修订：
    - 新增 app_id 必填参数
    - sync_user_skills 接受 app_id（嵌套路径）
    - FilesystemBackend 指向 agents/<app_id>/users/<user_id>/skills/
    """
    effective_skill_names = sorted(
        set(app_cfg.skill_names) | {
            n for cfg in subagent_cfgs for n in (cfg.skill_names or [])
        }
    )
    # ✅ v3 修订：sync_user_skills 必须传 app_id
    await sync_user_skills(
        session,
        user_id=user_id,
        app_id=app_id,
        associated_names=effective_skill_names,
    )

    catalog = await build_tool_catalog(session)
    mcp_tools = await get_mcp_tools(session)
    tool_index: dict[str, BaseTool] = {
        tool.name: tool for tool in [*builtin_tools, *mcp_tools]
    }
    logger.debug(
        "agent_app_tool_index_built",
        app_name=app_cfg.name,
        catalog_entries=len(catalog),
        tool_count=len(tool_index),
    )

    tools = resolve_tools(app_cfg.allowed_tools, tool_index)
    model = build_chat_model(*load_model_config(session, app_cfg.model))

    def resolve_model(reference: str) -> BaseChatModel:
        return build_chat_model(*load_model_config(session, reference))

    parent_skills: list[str] = [f"/{name}" for name in effective_skill_names]
    subagents = [
        build_subagent_spec(
            cfg,
            parent_tools=tools,
            parent_model=model,
            tool_index=tool_index,
            resolve_model=resolve_model,
            parent_skills=parent_skills,
        )
        for cfg in subagent_cfgs
    ]

    # ✅ v3 修订：FilesystemBackend 指向嵌套路径
    user_skill_root = (
        Path(settings.DATA_ROOT)
        / "agents"
        / str(app_id)
        / "users"
        / str(user_id)
    )
    backend = FilesystemBackend(root_dir=str(user_skill_root))
    interrupt_on = cast(Optional[dict[str, Any]], app_cfg.interrupt_on or None)

    graph = create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=app_cfg.system_prompt,
        middleware=[MemoryMiddleware()],
        subagents=subagents or None,
        skills=parent_skills or None,
        backend=backend,
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
        name=app_cfg.name,
    )

    logger.info(
        "agent_app_compiled",
        app_name=app_cfg.name,
        app_id=app_id,
        user_id=user_id,
        tool_count=len(tools),
        subagent_count=len(subagents),
        skill_count=len(effective_skill_names),
        skill_root=str(user_skill_root),
    )
    return graph
```

##### (5) `assembly.get_or_compile` 签名升级

```python
# app/services/agents/assembly.py（v3 修订版 · 第 5 项关键改造）

async def get_or_compile(
    app_cfg: AgentApp,
    *,
    subagent_cfgs: Sequence[SubAgentConfig],
    skill_hashes: Mapping[str, str],
    mcp_fingerprint: str,
    model_fingerprint: str,
    user_id: str,
    app_id: int,                              # ✅ v3 新增
    session: Session,
    checkpointer: Checkpointer | None = None,
) -> CompiledStateGraph:
    """返回缓存的或新编译的图（v3 修订版）。

    v3 修订：app_id 必填（透传给 compile_agent_app）。
    """
    fingerprint = compute_fingerprint(
        app_cfg, subagent_cfgs, skill_hashes, mcp_fingerprint, model_fingerprint
    )

    cached = _compile_cache.get(fingerprint)
    if cached is not None:
        _compile_cache.move_to_end(fingerprint)
        agent_graph_cache_hits_total.labels(result="hit").inc()
        logger.debug(
            "agent_graph_compile_cache_hit",
            fingerprint=fingerprint,
            app_name=app_cfg.name,
        )
        return cached

    agent_graph_cache_hits_total.labels(result="miss").inc()
    started = time.perf_counter()
    # ✅ v3 修订：透传 app_id
    graph = await compile_agent_app(
        app_cfg,
        subagent_cfgs=subagent_cfgs,
        user_id=user_id,
        app_id=app_id,
        session=session,
        checkpointer=checkpointer,
    )
    agent_graph_compile_duration_seconds.observe(time.perf_counter() - started)
    ...
```

##### (6) 启动期校验职责拆分（spec §6.1 重写）

| spec §6.1 表述 | v3 修订版对应函数 | 触发时机 | 职责 |
|---|---|---|---|
| `_validate_user_workspace`（启动期一次性校验） | `bootstrap.ensure_all_agent_workspaces`（第 4 项） | 进程启动时 | 校验 Agent 层 + 补建目录 |
| （spec 缺失）lazy 校验 | `agent_apps_service.ensure_user_workspace_up_to_date`（第 3 项） | 每次 session 启动 | 校验 User 层 + 增量同步 |
| 启动期 chatbot 装配校验（spec §7 DoD 第 8 项） | 不新增；由上述两层兜底 | — | 职责不重复 |

**关键决策**：spec §6.1 的 `_validate_user_workspace` 与第 3 项 `ensure_user_workspace_up_to_date` 职责重叠。v3 修订版**统一由 lazy 校验承担**，启动期只补 Agent 层。这样：
- 启动期无需遍历所有 `(app, user)` 对（O(app × user) 复杂度）
- 请求热路径统一兜底（首次访问时校验，后续 hash 命中即跳过）
- 测试可单独覆盖 lazy 校验路径（无状态污染）

#### 5.2.4 关于 spec §7 "compute_fingerprint 新增 workspace_hash 字段"

**spec §7 DoD 表述**：
> `assembly.py` `compute_fingerprint` 新增 `workspace_hash` 字段

**v3 修订版否决**：

| 项 | 现状 | v3 修订版决策 | 理由 |
|---|---|---|---|
| `compute_fingerprint` 输入 | `app_cfg`、`subagents`、`skill_hashes`、`mcp_fingerprint`、`model_fingerprint` | ✅ **不加 `workspace_hash`** | `skill_hashes` 已通过 `content_hash`（DB 真相源）覆盖 skill 内容；`workspace_hash` 是磁盘派生值，再纳入是冗余 |
| `workspace_hash` 字段 | `AgentApp.workspace_hash` | ✅ **保留字段**（publish 时缓存） | 用途：启动期 + lazy 校验时快速判定"是否需要重新物化"，不参与 fingerprint |
| fingerprint 覆盖度 | 已包含所有图结构输入 | ✅ **充分** | 重编译触发条件：app_cfg 字段变化 / subagent 字段变化 / skill 内容变化 / MCP 配置变化 / model 配置变化 |

**结论**：spec §7 该项**否决**（与 `_load_skill_hashes` 冗余）；`compute_fingerprint` 维持原签名。

### 5.3 子项 §6.2 Test runner 审查（v3 修订版）

#### 5.3.1 spec §6.2 原文

```python
# spec §6.2（原文）
async def materialize_into_combined_directory(
    session: Session, target_dir: Path, *, app_id: int, skill_names: Sequence[str]
) -> None:
    """供 test_runner 用：聚合 Global + Agent 层 → 临时目录。"""
    for name in skill_names:
        agent_path = _agent_skill_file(app_id, name)
        source = agent_path if agent_path.exists() else _global_skill_file(name)
        if source.exists():
            body = await asyncio.to_thread(source.read_text, "utf-8")
            await asyncio.to_thread(
                _atomic_write, target_dir / name / _SKILL_FILE_NAME, body
            )
```

#### 5.3.2 v3 修订版决策

| 决策 | 结论 | 备注 |
|---|---|---|
| `materialize_into_combined_directory` 函数 | ✅ **新增**（与 spec 一致） | 接受 `(session, target_dir, *, app_id, skill_names)` |
| 调用方 `run_subagent_once` | ⚠️ **保持当前实现**（仅 Global） + MVP 限制说明 | standalone 测试无 agent 上下文 |
| `SubAgentConfig` 加 `agent_app_id` 字段？ | ❌ **不推荐**（破坏单一所有权） | 单一 SubAgent 可被多 AgentApp 复用 |
| Test runner 是否需要支持 combined？ | **MVP 不支持**，文档说明 | SubAgent 单独运行测试只验证 skill 功能可用性；不验证 AgentApp 内的隔离语义 |

#### 5.3.3 v3 修订版实现

```python
# app/services/agents/skills_store.py（v3 修订版 · 第 5 项新增）

async def materialize_into_combined_directory(
    session: Session,
    target_dir: Path,
    *,
    app_id: int,
    skill_names: Sequence[str],
) -> None:
    """供 test_runner 等使用：聚合 (Agent 层 + Global) → 临时目录。

    v3 修订：
    - 接受 app_id（决定 Agent 层 source path）
    - 源判定：name ∈ agent_published_names → Agent 层；否则 → Global 层
    - 路径解析统一走 helper（无硬编码）
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    agent_published_names = set(
        session.exec(
            select(AgentApp.skill_names).where(AgentApp.id == app_id)
        ).first() or []
    )

    written = 0
    missing = 0
    for name in skill_names:
        _validate_skill_name(name)
        if name in agent_published_names:
            source_path = _agent_skill_file(app_id, name)
        else:
            source_path = _global_skill_file(name)

        if not source_path.exists():
            missing += 1
            logger.warning(
                "test_materialize_source_missing",
                source=str(source_path),
                name=name,
                app_id=app_id,
            )
            continue

        body = await asyncio.to_thread(source_path.read_text, "utf-8")
        await asyncio.to_thread(
            _atomic_write, target_dir / name / _SKILL_FILE_NAME, body
        )
        written += 1

    logger.info(
        "test_skills_materialized_combined",
        target_dir=str(target_dir),
        app_id=app_id,
        requested=len(list(skill_names)),
        written=written,
        missing=missing,
    )
```

#### 5.3.4 `run_subagent_once` 维持 MVP 现状

```python
# app/services/agents/test_runner.py（v3 修订版 · 维持现状 + 文档）

# 当前实现：
skills = list(cfg.skill_names or [])
if skills:
    if tmp_skills_root is None:
        raise ValueError(...)
    await materialize_into_directory(session, tmp_skills_root, skills)
```

**v3 MVP 决策**：
- ✅ `run_subagent_once` 保持仅读 Global（当前实现）
- ✅ `materialize_into_combined_directory` 函数**新增但不强制调用**
- 后续 Phase 5+ 如需要完整模拟 Agent 层，可由 caller 主动调用 combined 版本

**MVP 限制说明（追加到 `run_subagent_once` docstring）**：

```python
"""
MVP 限制（v3 Phase 2）：
- 单独测试 SubAgent 时，仅从 Global 层拉取 skill_names 列表
- 不模拟 AgentApp 内的 effective_skill_names 并集语义
- 若 SubAgent 在某 AgentApp 内的 skill 来自 Agent 层（含 publish 时复制的副本），
  单测不会读取 Agent 层副本，只读 Global 真相源
- 这是 standalone runner 的设计取舍：单元测试仅验证 SubAgent 配置正确性，
  不验证 AgentApp 内的隔离语义（后者由集成测试覆盖）
"""
```

### 5.4 `chatbot` 调用方调整

#### 5.4.1 chat session 启动路径（`app/api/v1/chatbot.py`）

```python
# app/api/v1/chatbot.py（v3 修订版 · 调用方配合）

# 旧：
async def chat_endpoint(...):
    runtime = await get_runtime(session, str(session_cfg.agent_app_id))
    ...

# 新（v3）：
async def chat_endpoint(
    ...,
    current_user: User = Depends(get_current_user),
):
    # 1. 已由 G3 集成点（spec-g3-session.md §12）保证：
    #    - User 已关联 AgentApp
    #    - ensure_user_workspace_up_to_date 已触发
    # 2. v3 必传真实 user_id
    runtime = await get_runtime(
        session,
        str(session_cfg.agent_app_id),
        user_id=current_user.id,
    )
    ...
```

### 5.5 影响下游模块

| 受影响模块 | 变更内容 | 优先级 |
|---|---|---|
| `app/services/agents/runtime.py` | 删除 `_COMPILE_USER_ID`；cache key 升三元组；`get_runtime` 接受 user_id | G2 必改 |
| `app/services/agents/assembly.py` | `compile_agent_app` FilesystemBackend 路径改造；`get_or_compile` 接受 app_id | G2 必改 |
| `app/services/agents/skills_store.py` | 新增 `materialize_into_combined_directory` 函数 | G2 必改 |
| `app/services/agents/test_runner.py` | docstring 补充 MVP 限制说明 | G2 必改 |
| `app/api/v1/chatbot.py` | 调用 `get_runtime(..., user_id=current_user.id)` | G2 必改 |
| `app/api/v1/apps.py` (test endpoint) | 调用 `materialize_into_combined_directory`（可选） | G2 可选 |
| `app/services/agents/agent_apps_service.py` | `ensure_user_workspace_up_to_date` 函数（已在第 3 项落盘） | G2 必改 |
| spec-g2-workspace.md §6.1 | 重写为"职责拆分说明" + 删除 `_validate_user_workspace` 函数定义 | G2 修订 spec |
| spec-g2-workspace.md §7 | 删 DoD 项"compute_fingerprint 新增 workspace_hash 字段"（否决） | G2 修订 spec |

### 5.6 待用户确认事项

| # | 议题 | 备选方案 | v3 修订版建议 |
|---|---|---|---|
| 1 | `_COMPILE_USER_ID` 删除时机 | A: 立即删除；B: 保留向后兼容 | A（v3 阶段 user_id 必真实） |
| 2 | `_runtime_cache` key 升级维度 | A: `(app_id, user_id, fingerprint)`；B: `(app_id, fingerprint)` + 路由时重编译 | A（编译缓存正确性优先） |
| 3 | `_validate_user_workspace` 函数 | A: 保留并简化；B: 完全由 lazy + 启动期覆盖 | B（职责不重复） |
| 4 | `compute_fingerprint` 是否纳入 `workspace_hash` | A: 纳入；B: 不纳入（现状） | B（与 skill_hashes 冗余） |
| 5 | `materialize_into_combined_directory` 调用方 | A: 强制 `run_subagent_once` 使用；B: 仅新增函数 | B（standalone runner MVP 限制） |
| 6 | `SubAgentConfig` 是否加 `agent_app_id` | A: 加；B: 不加 | B（破坏单一所有权） |
| 7 | `_build_filesystem_backend` 单独函数 | A: 抽取；B: 维持内联 | B（单点调用，简化） |

### 5.7 已确认事项

| # | 议题 | 用户确认 |
|---|---|---|
| 1 | `_COMPILE_USER_ID` 立即删除（v3 阶段 user_id 必真实） | ✅ |
| 2 | `_runtime_cache` key 升三元组 `(app_id, user_id, fingerprint)` | ✅ |
| 3 | `_validate_user_workspace` 不新增（由 lazy + 启动期覆盖） | ✅ |
| 4 | `compute_fingerprint` 不纳入 `workspace_hash`（否决 spec §7 该项） | ✅ |
| 5 | `materialize_into_combined_directory` 仅新增不强制调用 | ✅ |
| 6 | `SubAgentConfig` 不加 `agent_app_id`（保留单一所有权） | ✅ |
| 7 | `_build_filesystem_backend` 不抽取（维持内联） | ✅ |
| 8 | spec §6.1 重写为「职责拆分说明」 | ✅ |
| 9 | spec §7 删 DoD 项「compute_fingerprint 新增 workspace_hash 字段」 | ✅ |

---

## 第 6 项：DoD + 兼容迁移收尾（已落盘 · v3 修订版）

### 6.0 关键决策回顾

| 决策 | 结论 | 备注 |
|---|---|---|
| spec §7 全部 DoD 项 | ✅ **逐项映射到第 1-5 项落盘内容** | 部分否决，部分接受 |
| `AGENTS_ROOT` 配置项 | ✅ **不引入** | 第 1 项 MVP 简化（路径直接拼接） |
| `_read_agent_dir_skill_names` 函数（spec §7 第 3 项） | ❌ **否决** | 第 3 项已删除（与 `app_cfg.skill_names` 冗余） |
| `compute_fingerprint` 纳入 `workspace_hash`（spec §7 第 7 项） | ❌ **否决** | 第 5 项已否决（与 `_load_skill_hashes` 冗余） |
| `bootstrap.ensure_default_agent_workspace` 函数名 | ✅ **重命名为 `ensure_all_agent_workspaces`** | 第 4 项已重命名 |
| `_validate_user_workspace`（spec §7 第 8 项） | ❌ **不新增** | 第 5 项已否决（由 lazy + 启动期覆盖） |
| `test_runner.py` 改用 `materialize_into_combined_directory`（spec §7 第 9 项） | ⚠️ **MVP 不强制** | 第 5 项决策（standalone runner 维持 Global-only） |
| 前端 `AgentList.vue` 扩展 "绑定到用户" 操作（spec §7 第 11 项） | ✅ **MVP 暂缓**，由 G3 决定 | frontend stub 状态；不在 G2 必做范围 |
| 文档 §6.6 "三级 Workspace 同步" | ✅ **新增** | 手工冒烟章节 |
| 文档 `authentication.md` 第 7 节 "Workspace 隔离" 小节 | ✅ **新增** | 隔离语义说明 |
| `architecture.md` Workspace 章节 | ✅ **新增/更新** | 三级 Workspace 拓扑说明 |
| **存量迁移脚本 `scripts/migrate_workspace.py`** | ✅ **新增** | 兼容迁移收尾（spec §8 提及） |
| 兼容 SKILLS_ROOT env | ✅ **保留 1 个大版本** | 第 1 项已规划（带 deprecation 警告） |

### 6.1 现状核对

| 现状项 | 文件 / 行号 | 与 v3 修订版的一致性 |
|---|---|---|
| `tests/unit/agents/test_skills_store.py` 既有覆盖 | 561 行 | ✅ 既有；新增 case 见 §6.3 |
| `tests/integration/agents/test_subagent_runner.py` | 275 行 | ✅ 既有；含 `test_subagent_one_shot_test_with_skills` |
| `tests/unit/agents/test_runtime.py` | 1139 行 | ✅ 既有；需新增 cache key 三元组测试 |
| `tests/unit/api/test_agent_apps_api.py` | 1520 行 | ✅ 既有；需新增 publish / associate-user 测试 |
| `app/core/config.py` 仅 `SKILLS_ROOT` | line 171 | ⚠️ 第 1 项已规划 DATA_ROOT + SKILLS_ROOT 兼容 |
| `app/services/agents/skills_store.py` 既有函数 | 611 行 | ✅ 既有；新增函数见第 3、5 项 |
| `app/services/agents/bootstrap.py` `ensure_default_agent_app` | 既有 | ✅ 既有；新增 `ensure_all_agent_workspaces` |
| `app/services/agents/agent_apps_service.py` | **不存在** | ⚠️ 第 3 项新增 |
| `app/services/agents/agents_service.py` | **不存在** | ⚠️ 第 3 项新增 |
| `scripts/migrate_workspace.py` | **不存在** | ⚠️ 本项新增 |
| `docs/agentapp-manual-testing.md` 第 6.6 节 | **不存在** | ⚠️ 本项新增 |
| `docs/authentication.md` 第 7 节 | "Retired: chatbot session API"（不是 Workspace 隔离） | ⚠️ 本项新增/调整 |
| `docs/architecture.md` Workspace 章节 | **不存在** | ⚠️ 本项新增 |
| `agent-web/src/views/agent/AgentList.vue` | 既有 stub | ⚠️ "绑定到用户" 操作 MVP 暂缓 |

### 6.2 spec §7 DoD 逐项审查（v3 修订版）

#### 6.2.1 spec §7 原版 DoD 清单 vs v3 修订版映射

| # | spec §7 原版 DoD | v3 修订版决策 | 落盘位置 |
|---|---|---|---|
| 1 | alembic 迁移：`skill_asset.scope`、`agent_app.agent_dir`、`agent_app.workspace_hash`、`agent_app.agent_workspace_status` | ✅ **保留全部** | 第 2 项 §2.4 |
| 2 | 数据回填：所有现有 AgentApp 的 `agent_dir` 填充、`agent_workspace_status='pending'` | ✅ **保留** | 第 2 项 §2.4 + 第 4 项 `ensure_all_agent_workspaces` |
| 3 | `skills_store.py` 新增 `_agent_skill_dir` / `_agent_skill_file` / `materialize_for_agent` / `materialize_to_user_combined` / `_read_agent_dir_skill_names` / `compute_workspace_hash` / `materialize_into_combined_directory` | ⚠️ **修订**：<br>✅ `_agent_skill_dir` / `_agent_skill_file`（第 1 项）<br>✅ `materialize_for_agent`（第 3 项）<br>✅ `materialize_to_user_combined`（第 3 项）<br>❌ `_read_agent_dir_skill_names`（**否决**，第 3 项）<br>✅ `compute_workspace_hash`（第 3 项）<br>✅ `materialize_into_combined_directory`（第 5 项） | 第 1+3+5 项 |
| 4 | `apps.py` publish 流程新增 Global → Agent 复制步骤；`workspace_hash` 计算 | ✅ **保留**（service 层） | 第 3 项 §3.2 |
| 5 | `apps.py` 新增 `POST /apps/{id}/associate-user/{uid}` 端点 | ✅ **保留**（service 层） | 第 3 项 §3.3 |
| 6 | `bootstrap.py` 新增 `ensure_default_agent_workspace`，启动时自动补建 Agent 层 | ✅ **保留 + 重命名**：<br>`ensure_all_agent_workspaces` | 第 4 项 §4.3 |
| 7 | `assembly.py` `compute_fingerprint` 新增 `workspace_hash` 字段 | ❌ **否决** | 第 5 项 §5.2.4 |
| 8 | `runtime.py` chatbot 装配时校验 User 层与 Agent 层 drift（启动期） | ❌ **不新增**（由 lazy + 启动期覆盖） | 第 5 项 §5.2.3 (6) |
| 9 | `test_runner.py` 改用 `materialize_into_combined_directory`（Global + Agent） | ⚠️ **MVP 不强制**（函数新增，调用方暂缓） | 第 5 项 §5.3 |
| 10 | `app/core/config.py` 新增 `AGENTS_ROOT` 配置项（默认 `SKILLS_ROOT / "agents"`） | ❌ **不引入**（路径直接拼接，MVP 简化） | 第 1 项 §1.2 |
| 11 | 前端 `AgentList.vue`（已在 stub）扩展为支持"绑定到用户"操作 | ⚠️ **MVP 暂缓**（frontend stub 状态，G2 范围外） | 不在本项 |
| 12 | 文档：3 个 doc 更新 | ✅ **保留** | 本项 §6.5 |

**汇总**：

| 类型 | 数量 | 备注 |
|---|---|---|
| ✅ 接受 | 7 | DoD 1, 2, 4, 5, 6, 12（部分）+ §7-2 注 |
| ⚠️ 修订 | 3 | DoD 3（部分否决）+ DoD 9（暂缓）+ DoD 11（暂缓） |
| ❌ 否决 | 3 | DoD 3（`_read_agent_dir_skill_names`）+ DoD 7（`workspace_hash` 字段）+ DoD 8（`_validate_user_workspace`）+ DoD 10（`AGENTS_ROOT`） |

#### 6.2.2 DoD 否决项理由汇总

| DoD 项 | 否决理由 |
|---|---|
| `_read_agent_dir_skill_names`（§7 第 3 项） | 与 `app_cfg.skill_names` 冗余（Agent 层 = publish 时 `app_cfg.skill_names` 的全量副本）；磁盘扫描逻辑不应作为 skill 集合的真相源（DB 才是） |
| `compute_fingerprint` 纳入 `workspace_hash`（§7 第 7 项） | `_load_skill_hashes` 已通过 `content_hash`（DB 真相源）覆盖 skill 内容；`workspace_hash` 是磁盘派生值，纳入是冗余 |
| `_validate_user_workspace`（§7 第 8 项） | 与第 3 项 `ensure_user_workspace_up_to_date` 职责重叠；启动期无需遍历所有 `(app, user)` 对（O(app × user) 复杂度），由 lazy 校验统一兜底 |
| `AGENTS_ROOT` 配置项（§7 第 10 项） | MVP 简化（路径直接拼接）；跨盘 / NFS 需求可在 Phase 5+ 引入 |
| `runtime.py` chatbot 装配时校验 User 层与 Agent 层 drift（§7 第 8 项） | 同上，由 `ensure_all_agent_workspaces`（启动期） + `ensure_user_workspace_up_to_date`（lazy）双层覆盖 |

### 6.3 spec §8 验证（v3 修订版测试矩阵）

#### 6.3.1 单元测试（`tests/unit/agents/test_skills_store.py` 等）

| 测试名 | 验证目标 | spec §8.1 原版 | v3 修订版 |
|---|---|---|---|
| `test_materialize_for_agent_creates_files` | Global → Agent 层复制 | ✅ | ✅ 保留 |
| `test_materialize_for_agent_hash_match_skips_write` | hash 比对优化（v3 新增） | — | ✅ 新增 |
| `test_materialize_to_user_combined_aggregates_global_and_agent` | (Global + Agent) → User 层 | ✅ | ✅ 保留 |
| `test_materialize_to_user_combined_uses_app_cfg_skill_names` | 接受 `(app_cfg, subagent_cfgs)`（第 3 项） | — | ✅ 新增 |
| `test_compute_workspace_hash_stable` | Agent 层指纹稳定 | ✅ | ✅ 保留 |
| `test_agent_skill_overrides_global_in_combined` | Agent 覆盖 Global（Q4 决策） | ✅ | ✅ 保留 |
| `test_sync_user_skills_with_app_id_uses_nested_path` | sync_user_skills 接受 app_id（第 3 项） | — | ✅ 新增 |
| `test_materialize_into_combined_directory_with_agent_layer` | spec §6.2 函数（第 5 项） | — | ✅ 新增 |
| `test_prune_skill_from_all_layers` | 第 3 项场景 D | — | ✅ 新增 |
| `test_ensure_user_workspace_up_to_date_no_drift` | lazy 校验命中（第 3 项） | — | ✅ 新增 |
| `test_ensure_user_workspace_up_to_date_drift_triggers_sync` | lazy 校验 miss | — | ✅ 新增 |

#### 6.3.2 单元测试（`tests/unit/agents/test_runtime.py` 等）

| 测试名 | 验证目标 | v3 修订版 |
|---|---|---|
| `test_get_runtime_cache_returns_same_instance` | 既有：cache hit | ✅ 保留 |
| `test_get_runtime_cache_key_includes_user_id` | 第 5 项：cache key 三元组 | ✅ 新增 |
| `test_get_runtime_eviction_scoped_to_user_id` | 第 5 项：evict 按 `(app_id, user_id)` | ✅ 新增 |
| `test_get_runtime_triggers_lazy_workspace_sync` | 第 3 + 5 项：get_runtime 内 lazy 校验 | ✅ 新增 |
| `test_get_runtime_user_id_required` | 第 5 项：user_id 必填 | ✅ 新增 |

#### 6.3.3 单元测试（`tests/unit/agents/test_assembly.py` 等）

| 测试名 | 验证目标 | v3 修订版 |
|---|---|---|
| `test_compile_agent_app_filesystem_backend_uses_nested_path` | 第 5 项：FilesystemBackend 路径 | ✅ 新增 |
| `test_compile_agent_app_calls_sync_user_skills_with_app_id` | 第 5 项：sync_user_skills 签名升级 | ✅ 新增 |
| `test_get_or_compile_signature_accepts_app_id` | 第 5 项：签名升级 | ✅ 新增 |

#### 6.3.4 单元测试（`tests/unit/agents/test_runner.py`）

| 测试名 | 验证目标 | v3 修订版 |
|---|---|---|
| `test_run_subagent_once_uses_global_only` | 第 5 项：MVP 限制文档化 | ✅ 新增 |
| `test_run_subagent_once_documents_mvp_limitation` | docstring 含 MVP 限制说明 | ✅ 新增 |

#### 6.3.5 单元测试（`tests/unit/api/test_agent_apps_api.py`）

| 测试名 | 验证目标 | v3 修订版 |
|---|---|---|
| `test_publish_success_sets_status_hash_and_version` | 既有：publish 流程 | ✅ 保留 |
| `test_publish_creates_agent_workspace_files` | 第 3 项：Agent 层文件就位 | ✅ 新增 |
| `test_publish_workspace_status_active` | 第 3+4 项：状态机 | ✅ 新增 |
| `test_associate_user_copies_combined_skills` | spec §8.1 | ✅ 保留 |
| `test_associate_user_idempotent` | spec §8.1 | ✅ 保留 |
| `test_associate_user_writes_to_nested_path` | 第 1 项：嵌套路径 | ✅ 新增 |
| `test_disassociate_user_cleans_workspace` | 第 3 项：物理清理 | ✅ 新增 |
| `test_delete_agent_app_cleans_workspace_tree` | 第 3 项场景 F | ✅ 新增 |
| `test_patch_published_app_content_edit_reverts_status_to_draft` | 既有：PATCH 状态机解读 B | ✅ 保留（第 4 项决策） |
| `test_patch_published_app_clears_workspace_hash` | 第 4 项：workspace_hash 清空 | ✅ 新增 |

#### 6.3.6 单元测试（`tests/unit/services/`）

| 测试名 | 验证目标 | v3 修订版 |
|---|---|---|
| `test_ensure_all_agent_workspaces_migrates_pending` | spec §8.1（原 `ensure_default_agent_workspace`） | ✅ 保留（重命名后） |
| `test_ensure_all_agent_workspaces_rematerializes_missing_dir` | 第 4 项场景 I | ✅ 新增 |
| `test_ensure_all_agent_workspaces_draft_state_keeps_pending` | 第 4 项：draft → pending | ✅ 新增 |
| `test_ensure_all_agent_workspaces_isolates_per_app_failure` | 第 4 项：异常隔离 | ✅ 新增 |
| `test_publish_agent_app_calls_materialize_for_agent` | 第 3 项：service 层 | ✅ 新增 |
| `test_associate_user_with_app_upserts_association` | 第 3 项：service 层 | ✅ 新增 |
| `test_ensure_user_workspace_up_to_date_no_op_when_hash_match` | 第 3 项：lazy 校验 | ✅ 新增 |

#### 6.3.7 集成测试（`tests/integration/agents/`）

| 测试名 | 验证目标 | v3 修订版 |
|---|---|---|
| `test_agent_workspace_publish_to_user_chat_full_flow` | spec §8.2 集成 | ✅ 新增 |
| `test_agent_workspace_patch_skill_then_republish_updates_agent_layer` | spec §8.2 | ✅ 新增 |
| `test_agent_workspace_cross_user_isolation` | spec §8.2（user1 关联 + user2 关联互不影响） | ✅ 新增 |
| `test_agent_workspace_disassociate_then_reassociate_recreates` | 第 3 项场景 A | ✅ 新增 |
| `test_subagent_one_shot_test_with_skills` | 既有 | ✅ 保留 |
| `test_subagent_one_shot_test_with_skill_names_none_inherits_empty` | 既有 | ✅ 保留 |

#### 6.3.8 手工冒烟（`docs/agentapp-manual-testing.md` 新增 §6.6）

详见 §6.5 文档更新。

### 6.4 兼容迁移收尾

#### 6.4.1 存量迁移脚本 `scripts/migrate_workspace.py`（v3 新增）

```python
"""一次性迁移脚本：旧 SKILLS_ROOT → 新 DATA_ROOT 嵌套路径。

触发条件：
- 第一次 G2 上线时手动执行
- 旧路径：{SKILLS_ROOT}/global/<name>/SKILL.md
        {SKILLS_ROOT}/users/<uid>/<name>/SKILL.md
- 新路径：{DATA_ROOT}/global/skills/<name>/SKILL.md
        {DATA_ROOT}/agents/<app_id>/users/<uid>/skills/<name>/SKILL.md

迁移逻辑：
1. global/<name>/ → {DATA_ROOT}/global/skills/<name>/
2. users/<uid>/ → 根据 user_agent_app_association 映射到
                 {DATA_ROOT}/agents/<app_id>/users/<uid>/skills/<name>/
3. 若 uid 在任何 association 中都未找到：
   - 旧路径整个目录移到 {DATA_ROOT}/users/<uid>/skills/
   - 标记为 orphan（需人工介入）

前置条件：
- alembic 已执行（含 user_agent_app_association 表创建）
- 所有存量 AgentApp 已 publish（生成 agent 层）
- 旧 SKILLS_ROOT 还有内容

风险：
- 旧路径与新路径同时存在的双轨期
- 建议保留 1 个大版本，旧路径符号链接到新路径
"""
```

**v3 修订版决策**：

| 项 | 决策 | 备注 |
|---|---|---|
| 迁移脚本执行时机 | **G2 上线前手动执行** | 不放进 alembic 迁移（避免长事务） |
| 旧路径双轨期 | **保留 1 个大版本** | 通过符号链接兜底 |
| Orphan user 目录 | **保留到 {DATA_ROOT}/users/<uid>/skills/** | 与第 1 项顶层 users 设计一致 |
| 旧路径清理 | **下个大版本** | 在 README 中说明 |

#### 6.4.2 `app/core/config.py` 兼容改造（v3 修订版）

```python
# app/core/config.py（v3 修订版 · 第 1 项 + 本项合并）

# 替换原 SKILLS_ROOT
self.DATA_ROOT = os.getenv("DATA_ROOT", "./data")

# 兼容旧 SKILLS_ROOT env（一次性迁移期，标记 deprecated）
legacy_skills_root = os.getenv("SKILLS_ROOT", "")
if legacy_skills_root and not os.getenv("DATA_ROOT"):
    self.DATA_ROOT = legacy_skills_root
    logger.warning(
        "skills_root_env_deprecated",
        hint="SKILLS_ROOT is deprecated; use DATA_ROOT instead",
    )

# 不引入 AGENTS_ROOT / USERS_ROOT（路径直接拼接，MVP 简化）
```

**关键决策**：
- ✅ 替换 `SKILLS_ROOT` 为 `DATA_ROOT`（spec §7 第 10 项的替代）
- ❌ 不引入 `AGENTS_ROOT`（MVP 简化，否决 spec §7 第 10 项）
- ✅ 兼容旧 env（带 deprecation 警告）
- ✅ 默认值 `./data`（vs 原 `./data/skills`，与 `LOG_DIR` 命名一致）

#### 6.4.3 存量数据回填（与第 2 项 / 第 4 项配合）

```python
# alembic 迁移末尾 + bootstrap 启动期双层回填
# alembic 迁移（SQL 直执行）：
conn.execute(sa.text("UPDATE skill_asset SET scope = 'global' WHERE scope IS NULL"))
conn.execute(sa.text(
    "UPDATE agent_app SET agent_workspace_status = 'pending' "
    "WHERE agent_workspace_status IS NULL"
))

# bootstrap 启动期（Python 代码）：
# - ensure_all_agent_workspaces：补 agent_dir + 重新 materialize + 设 active
# - 数据迁移脚本单独跑（一次性）
```

### 6.5 文档更新清单

#### 6.5.1 `docs/agentapp-manual-testing.md` 新增第 6.6 节

```markdown
### 6.6 三级 Workspace 同步（v3 新增）

**目的**：验证 Global → Agent → User 三层 skill 文件夹在 publish / associate-user / chat 流程下的同步行为。

#### 6.6.1 publish 后 Agent 层文件就位

```bash
# 1. 创建 + publish 一个含 skill 的 AgentApp
APP_ID=$(curl -s -X POST "$BASE/apps" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-assistant-v3",
    "system_prompt": "...",
    "skill_names": ["markdown-fix"]
  }' | jq -r '.data.id')

curl -s -X POST "$BASE/apps/$APP_ID/publish" \
  -H "Authorization: Bearer $TOKEN"

# 2. 验证 Agent 层目录
ls -la "$DATA_ROOT/agents/$APP_ID/skills/markdown-fix/"
# 预期：包含 SKILL.md
```

#### 6.6.2 associate-user 后 User 层就位

```bash
# 1. 关联 user
curl -s -X POST "$BASE/apps/$APP_ID/associate-user/$USER_ID" \
  -H "Authorization: Bearer $TOKEN"

# 2. 验证 User 层目录（嵌套在 AgentApp 下）
ls -la "$DATA_ROOT/agents/$APP_ID/users/$USER_ID/skills/markdown-fix/"
# 预期：包含 SKILL.md（与 Agent 层内容一致或与 Global 一致）
```

#### 6.6.3 chat session 触发 lazy 校验

```bash
# 1. 创建 chat session
SESSION_RESP=$(curl -s -X POST "$BASE/auth/session" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"agent_app_id\": \"$APP_ID\"}")

# 2. 查看日志（lazy 校验触发）
grep "user_workspace_up_to_date\|user_workspace_drift_detected" /var/log/app.log
```

#### 6.6.4 PATCH skill body 后重新 publish

```bash
# 1. PATCH Global skill
curl -s -X PATCH "$BASE/skills/markdown-fix" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"body": "# 新版本..."}'

# 2. Agent 层不自动同步
ls "$DATA_ROOT/agents/$APP_ID/skills/markdown-fix/SKILL.md"
# 内容仍是旧版本

# 3. 重新 publish 后 Agent 层同步
curl -s -X POST "$BASE/apps/$APP_ID/publish" -H "Authorization: Bearer $TOKEN"
ls "$DATA_ROOT/agents/$APP_ID/skills/markdown-fix/SKILL.md"
# 内容已更新
```

#### 6.6.5 跨用户隔离

```bash
# user1 和 user2 关联同一 AgentApp
# user1 的 User 层：$DATA_ROOT/agents/$APP_ID/users/$USER1_ID/skills/
# user2 的 User 层：$DATA_ROOT/agents/$APP_ID/users/$USER2_ID/skills/
# 互不影响（隔离验证）
```

#### 6.6.6 删除 AgentApp 的连锁清理

```bash
curl -s -X DELETE "$BASE/apps/$APP_ID" \
  -H "Authorization: Bearer $TOKEN"

# 整个 agents/$APP_ID/ 子树被清理
ls "$DATA_ROOT/agents/$APP_ID/"
# 预期：目录不存在
```
```

#### 6.5.2 `docs/authentication.md` 第 7 节新增 "Workspace 隔离"

```markdown
## 7. Workspace 隔离（v3 新增）

每个 User-AgentApp 关联对应一个独立的 per-(app, user) workspace 目录：

```
{DATA_ROOT}/agents/<app_id>/users/<user_id>/skills/<skill_name>/SKILL.md
```

**隔离语义**：
- 同一 User 关联不同 AgentApp：独立 user 目录（互不影响）
- 同一 AgentApp 关联不同 User：独立 user 目录（互不影响）
- User 取消关联后重新关联：自动重建（幂等恢复）
- 删除 AgentApp：物理清理整个 agents/<app_id>/ 子树

**Lazy 校验**：
- 每次 chat session 启动时，对照 (Global + Agent) 实际内容 vs User 层文件 hash
- 不一致 → 自动同步；一致 → 跳过
- 启动期 `bootstrap.ensure_all_agent_workspaces` 校验 Agent 层

**MVP 限制**：
- SubAgent 单元测试（`run_subagent_once`）仅从 Global 层拉取 skill
- 不模拟 AgentApp 内的 effective_skill_names 并集语义
```

#### 6.5.3 `docs/architecture.md` Workspace 章节更新

```markdown
## Workspace 三层架构（v3 新增）

```
{DATA_ROOT}/
├── global/skills/                # 全局共享 skill（DB 真相源 + 磁盘副本）
│   └── <name>/
│       └── SKILL.md
├── agents/<app_id>/              # AgentApp 私有空间
│   ├── skills/                   # Agent 层 skill 快照（publish 时复制）
│   │   └── <name>/SKILL.md
│   └── users/<user_id>/skills/   # per-(app, user) 隔离
│       └── <name>/SKILL.md
└── users/<user_id>/              # 跨 app 共享资源预留
```

**同步流程**：
1. publish: Global → Agent 层（`materialize_for_agent`）
2. associate-user: (Global + Agent) → User 层（`materialize_to_user_combined`）
3. session 启动：lazy 校验 User 层（`ensure_user_workspace_up_to_date`）
4. 进程启动：补全 Agent 层（`ensure_all_agent_workspaces`）

**文件真相源**：DB body（SkillAsset.body），磁盘为运行副本。
```

### 6.6 影响下游模块

| 受影响模块 | 变更内容 | 优先级 |
|---|---|---|
| `scripts/migrate_workspace.py`（新） | 一次性迁移脚本 | G2 上线前必跑 |
| `app/core/config.py` | DATA_ROOT 替换 + 兼容 SKILLS_ROOT | G2 必改 |
| `tests/unit/agents/test_skills_store.py` | 新增 ~10 个测试 | G2 必加 |
| `tests/unit/agents/test_runtime.py` | 新增 ~4 个测试（cache 三元组） | G2 必加 |
| `tests/unit/agents/test_assembly.py` | 新增 ~3 个测试（路径 + 签名） | G2 必加 |
| `tests/unit/api/test_agent_apps_api.py` | 新增 ~5 个测试（publish / associate / PATCH） | G2 必加 |
| `tests/unit/services/test_*.py`（新文件） | service 层测试（publish / associate / ensure_workspace） | G2 必加 |
| `tests/integration/agents/test_agent_workspace.py`（新文件） | 集成测试 4 个 | G2 必加 |
| `docs/agentapp-manual-testing.md` | 新增 §6.6 节 | G2 必加 |
| `docs/authentication.md` | 新增 §7 节 "Workspace 隔离" | G2 必加 |
| `docs/architecture.md` | 新增 "Workspace 三层架构" 章节 | G2 必加 |
| `agent-web/src/views/agent/AgentList.vue` | "绑定到用户" 操作（**MVP 暂缓**） | G2 范围外 |
| `spec-g2-workspace.md` | §7 / §8 / §9 按 v3 修订重写 | G2 必改 |

### 6.7 待用户确认事项

| # | 议题 | 备选方案 | v3 修订版建议 |
|---|---|---|---|
| 1 | spec §7 第 3 项 `_read_agent_dir_skill_names` | A: 实现；B: 否决 | B（与 `app_cfg.skill_names` 冗余） |
| 2 | spec §7 第 7 项 `compute_fingerprint` 纳入 `workspace_hash` | A: 纳入；B: 不纳入 | B（与 `_load_skill_hashes` 冗余） |
| 3 | spec §7 第 8 项 `_validate_user_workspace` 启动期校验 | A: 实现；B: 不新增 | B（与 lazy 校验职责重叠） |
| 4 | spec §7 第 9 项 `test_runner.py` 改用 combined | A: 强制；B: MVP 不强制 | B（standalone runner MVP 限制） |
| 5 | spec §7 第 10 项 `AGENTS_ROOT` 配置 | A: 新增；B: 不新增 | B（MVP 简化，路径直接拼接） |
| 6 | spec §7 第 11 项前端 `AgentList.vue` "绑定到用户" | A: 本期实现；B: MVP 暂缓 | B（frontend stub 状态，G2 范围外） |
| 7 | spec §8.1/§8.2 测试矩阵 | A: 按 spec 全量实现；B: 按 v3 修订版精选实现 | B（第 6.3 节已列出） |
| 8 | 迁移脚本 `scripts/migrate_workspace.py` | A: 一次性脚本；B: 集成进 alembic | A（避免长事务） |
| 9 | 旧 SKILLS_ROOT 双轨期 | A: 立即清理；B: 保留 1 个大版本 | B（兼容性优先） |
| 10 | 文档更新范围 | A: 仅更新 1 个核心文档；B: 更新全部 3 个 | B（覆盖 spec §7 第 12 项） |

### 6.8 已确认事项

| # | 议题 | 用户确认 |
|---|---|---|
| 1 | spec §7 第 3 项 `_read_agent_dir_skill_names` 否决 | ✅ |
| 2 | spec §7 第 7 项 `compute_fingerprint` 不纳入 `workspace_hash` | ✅ |
| 3 | spec §7 第 8 项 `_validate_user_workspace` 不新增（lazy + 启动期覆盖） | ✅ |
| 4 | spec §7 第 9 项 `test_runner` MVP 不强制 combined（standalone 维持 Global-only） | ✅ |
| 5 | spec §7 第 10 项 `AGENTS_ROOT` 不引入（MVP 简化） | ✅ |
| 6 | spec §7 第 11 项前端 `AgentList.vue` "绑定到用户" MVP 暂缓 | ✅ |
| 7 | spec §8.1/§8.2 测试矩阵按 v3 修订版精选实现 | ✅ |
| 8 | 迁移脚本 `scripts/migrate_workspace.py` 一次性手动执行 | ✅ |
| 9 | 旧 SKILLS_ROOT 双轨期保留 1 个大版本 | ✅ |
| 10 | 文档更新覆盖全部 3 个文档（manual-testing / authentication / architecture） | ✅ |
| 11 | `bootstrap.ensure_default_agent_workspace` 重命名为 `ensure_all_agent_workspaces` | ✅ |
| 12 | `compute_fingerprint` 维持原签名（5 输入字段） | ✅ |

---

## 总览总结 + 交付清单（已落盘 · v3 最终版）

### 7.0 审查完成度

| # | 审查主题 | spec 章节 | 落盘版本 | 关键决策数 |
|---|---|---|---|---|
| 1 | 目录结构设计 | §2 | v3 最终版 | 10 |
| 2 | alembic 数据模型迁移 | §3 | v3 修订版 | 7 |
| 3 | 复制逻辑核心 | §4 | v3 原版 + 实现优化 | 12 |
| 4 | fingerprint 锁定 + 启动校验 | §5 | v3 修订版 | 9 |
| 5 | 运行时读取路径调整 | §6 | v3 修订版 | 11 |
| 6 | DoD + 兼容迁移收尾 | §7 + §8 | v3 修订版 | 13 |
| **合计** | — | — | — | **62 项关键决策** |

### 7.1 v3 修订版核心架构决策

#### 7.1.1 目录结构（v3 关键变更）

```
{DATA_ROOT}/
├── global/skills/                  # 全局共享 skill（DB 真相源）
│   └── <name>/SKILL.md
├── agents/
│   └── <app_id>/
│       ├── skills/                 # Agent 层快照（publish 时复制）
│       │   └── <name>/SKILL.md
│       └── users/<user_id>/
│           ├── skills/             # User 层快照（(Global+Agent) 聚合）
│           │   └── <name>/SKILL.md
│           └── sessions/           # G3 预留
└── users/<user_id>/                # 跨 app 共享资源预留
```

**关键决策**：
- ✅ User 层**嵌套**在 AgentApp 下（不是顶层）—— per-(app, user) 真正隔离
- ✅ 顶层 `users/<user_id>/` 保留（跨 app 共享空间，MVP 空）
- ✅ v3 原版保留（Agent 层 `skills/` 目录保留，适合 Phase 5+ 扩展）

#### 7.1.2 数据模型变更

| 新增字段 | 表 | 用途 |
|---|---|---|
| `scope` | `skill_asset` | 默认 `global`，为未来 Phase 5+ `scope='agent'` 预留 |
| `agent_dir` | `agent_app` | AgentApp 私有空间根目录（含 skills/users 子目录） |
| `workspace_hash` | `agent_app` | Agent 层内容指纹（publish 时计算） |
| `agent_workspace_status` | `agent_app` | 两态：`pending` / `active` |

**新增表**：`user_agent_app_association`（v3 关键基础设施）
- `(user_id, agent_app_id)` 联合唯一
- `last_synced_workspace_hash`（增量同步优化）
- FK CASCADE（删 AgentApp/User 自动清理）

#### 7.1.3 复制逻辑

| 触发点 | 复制源 → 目标 | 函数 |
|---|---|---|
| AgentApp publish | Global → Agent 层 | `materialize_for_agent(app_id, skill_names)` |
| 关联 user（首次） | (Global + Agent) → User 层 | `materialize_to_user_combined(app_cfg, subagent_cfgs)` |
| Lazy 校验（session 启动） | 增量同步到 User 层 | `ensure_user_workspace_up_to_date(user_id, app_id)` |
| SubAgent standalone 测试 | Global → tmp | `materialize_into_directory(session, tmp, skills)` |
| Agent 层补建（启动期） | Global → Agent 层 | `materialize_for_agent`（在 `ensure_all_agent_workspaces` 中） |

**关键决策**：
- ✅ Hash 比对优化（source_hash vs existing_hash，仅不一致时写）
- ✅ Prune 过期 skill（在 User 层目录清理不在 effective_skill_names 中的子目录）
- ✅ 删除 `_read_agent_dir_skill_names`（与 `app_cfg.skill_names` 冗余）

#### 7.1.4 状态机

```
draft ──publish──> published ──PATCH──> draft (workspace_hash=None)
                       │
                       └─未 publish──> pending (workspace_hash=None)
                                              │
                                              └─ensure_all_agent_workspaces──> active
```

**关键决策**：
- ✅ **PATCH 解读 B**：published → draft（需重新 publish 才生效）
- ✅ PATCH 后 `workspace_hash` 清空
- ✅ 启动期 `ensure_all_agent_workspaces`（重命名自 `ensure_default_agent_workspace`）：
  - 已 active 状态**仍校验**（防目录丢失）
  - draft 状态保持 pending
  - published 状态必要时 rematerialize
  - 单 App 异常隔离

#### 7.1.5 Runtime 集成

```
启动期：
  bootstrap.ensure_all_agent_workspaces(session)
    └─ 遍历所有 AgentApp，补 Agent 层 + workspace_hash

请求热路径：
  chatbot.ainvoke(messages)
    └─ get_runtime(session, agent_app_id, *, user_id)
       ├─ ensure_user_workspace_up_to_date(...)  [lazy 校验]
       └─ _runtime_cache.get((app_id, user_id, fingerprint))
          └─ hit → 返回
          └─ miss → evict stale → compile → cache
```

**关键决策**：
- ✅ 删除 `_COMPILE_USER_ID = "system"`（v3 必传真实 user_id）
- ✅ `_runtime_cache` key 升三元组 `(app_id, user_id, fingerprint)`
- ✅ Cache 淘汰按 `(app_id, user_id)` 维度
- ✅ `compile_agent_app` FilesystemBackend 路径改造：`{DATA_ROOT}/agents/<app_id>/users/<user_id>/`
- ✅ `assembly.get_or_compile` 接受 `app_id` 透传
- ❌ `compute_fingerprint` 不纳入 `workspace_hash`（与 `_load_skill_hashes` 冗余）
- ❌ `_validate_user_workspace` 不新增（职责重叠）

### 7.2 交付清单（按依赖顺序）

#### 7.2.1 Phase 0：基础设施

| 任务 | 文件 / 函数 | 状态 | 来源 |
|---|---|---|---|
| DATA_ROOT 配置替换 | `app/core/config.py` | G2 必改 | 第 1 项 §1.2 |
| alembic 迁移（4 字段 + 1 新表） | `alembic/versions/<rev>_*.py` | G2 必改 | 第 2 项 §2.4 |
| Model 字段同步 | `app/models/agent_assets.py` | G2 必改 | 第 2 项 §2.5 |

#### 7.2.2 Phase 1：skills_store 路径层

| 任务 | 文件 / 函数 | 状态 | 来源 |
|---|---|---|---|
| 路径 helper 重写 | `app/services/agents/skills_store.py` §1.3 | G2 必改 | 第 1 项 §1.3 |
| `materialize_for_agent` | `app/services/agents/skills_store.py` | G2 必改 | 第 3 项 §3.4.2 |
| `materialize_to_user_combined` | `app/services/agents/skills_store.py` | G2 必改 | 第 3 项 §3.4.3 |
| `compute_workspace_hash` | `app/services/agents/skills_store.py` | G2 必改 | 第 3 项 §3.4.4 |
| `_compute_user_workspace_hash` | `app/services/agents/skills_store.py` | G2 必改 | 第 3 项 §3.4.5 |
| `materialize_into_combined_directory` | `app/services/agents/skills_store.py` | G2 必改 | 第 5 项 §5.3.3 |
| `prune_skill_from_all_layers` | `app/services/agents/skills_store.py` | G2 必改 | 第 3 项 §3.6.4 |
| `sync_user_skills` 签名升级 | `app/services/agents/skills_store.py` | G2 必改 | 第 3 项 §3.5.1 |
| `validate_skill_names_exist` | `app/services/agents/skills_store.py` | G2 必改 | 第 4 项 §4.5 |

#### 7.2.3 Phase 2：service 层（业务编排）

| 任务 | 文件 / 函数 | 状态 | 来源 |
|---|---|---|---|
| `agent_apps_service.py` 新建 | `app/services/agents/agent_apps_service.py` | G2 必改 | 第 3 项 |
| `publish_agent_app` | 同上 | G2 必改 | 第 3 项 §3.2.2 |
| `associate_user_with_app` | 同上 | G2 必改 | 第 3 项 §3.3.1 |
| `disassociate_user_from_app` | 同上 | G2 必改 | 第 3 项 §3.3.1 |
| `delete_agent_app` | 同上 | G2 必改 | 第 3 项 §3.6.6 |
| `patch_agent_app` | 同上 | G2 必改 | 第 4 项 §4.2.2 |
| `ensure_user_workspace_up_to_date` | 同上 | G2 必改 | 第 3 项 §3.4.6 |
| `agents_service.py` 新建 | `app/services/agents/agents_service.py` | G2 必改 | 第 3 项 |
| `list_subagent_cfgs` | 同上 | G2 必改 | 第 3 项 |
| `validate_subagent_skill_visibility` | 同上 | G2 必改 | 第 3 项 |
| `db_service.py` 新增 association CRUD | `app/services/db_service.py` | G2 必改 | 第 3 项 §3.7 |

#### 7.2.4 Phase 3：API 层重构（仅参数校验）

| 任务 | 文件 | 状态 | 来源 |
|---|---|---|---|
| publish 端点重构 | `app/api/v1/apps.py` | G2 必改 | 第 3 项 §3.2.3 |
| associate-user 端点 | 同上 | G2 必改 | 第 3 项 §3.3.2 |
| PATCH 端点状态机处理 | 同上 | G2 必改 | 第 4 项 §4.2.2 |
| delete 端点（连锁清理） | 同上 | G2 必改 | 第 3 项 §3.6.6 |
| chatbot 调用 `get_runtime(..., user_id=...)` | `app/api/v1/chatbot.py` | G2 必改 | 第 5 项 §5.4 |

#### 7.2.5 Phase 4：assembly / runtime 适配

| 任务 | 文件 | 状态 | 来源 |
|---|---|---|---|
| `compile_agent_app` FilesystemBackend 路径 | `app/services/agents/assembly.py:478-479` | G2 必改 | 第 1 + 5 项 |
| `compile_agent_app` 接受 `app_id` | 同上 | G2 必改 | 第 5 项 §5.2.3 (4) |
| `get_or_compile` 接受 `app_id` | 同上 | G2 必改 | 第 5 项 §5.2.3 (5) |
| `sync_user_skills` 调用升级 | `app/services/agents/assembly.py:446` | G2 必改 | 第 5 项 §5.2.3 (4) |
| `_COMPILE_USER_ID` 删除 | `app/services/agents/runtime.py:86` | G2 必改 | 第 5 项 §5.2.3 (1) |
| `_runtime_cache` key 三元组 | `app/services/agents/runtime.py:562` | G2 必改 | 第 5 项 §5.2.3 (2) |
| `get_runtime` 接受 `user_id` | `app/services/agents/runtime.py:738` | G2 必改 | 第 5 项 §5.2.3 (3) |
| cache 淘汰按 `(app_id, user_id)` | `app/services/agents/runtime.py:814-816` | G2 必改 | 第 5 项 §5.2.3 (3) |
| `main.py` lifespan 调用 `ensure_all_agent_workspaces` | `app/main.py` | G2 必改 | 第 4 项 §4.5 |
| `test_runner.py` docstring MVP 限制 | `app/services/agents/test_runner.py` | G2 必改 | 第 5 项 §5.3.4 |

#### 7.2.6 Phase 5：bootstrap

| 任务 | 文件 | 状态 | 来源 |
|---|---|---|---|
| `ensure_all_agent_workspaces` | `app/services/agents/bootstrap.py` | G2 必改 | 第 4 项 §4.3.2 |

#### 7.2.7 Phase 6：迁移脚本 + 文档

| 任务 | 文件 | 状态 | 来源 |
|---|---|---|---|
| `scripts/migrate_workspace.py` | 新建 | G2 上线前必跑 | 第 6 项 §6.4.1 |
| `docs/agentapp-manual-testing.md` §6.6 | 新增节 | G2 必加 | 第 6 项 §6.5.1 |
| `docs/authentication.md` §7 | 新增节 | G2 必加 | 第 6 项 §6.5.2 |
| `docs/architecture.md` Workspace 章节 | 新增节 | G2 必加 | 第 6 项 §6.5.3 |
| `spec-g2-workspace.md` §7/§8/§9 重写 | 重写 | G2 必改 | 第 6 项 §6.6 |

#### 7.2.8 Phase 7：测试

| 任务 | 文件 / 测试数 | 状态 | 来源 |
|---|---|---|---|
| `tests/unit/agents/test_skills_store.py` | +10 测试 | G2 必加 | 第 6 项 §6.3.1 |
| `tests/unit/agents/test_runtime.py` | +4 测试 | G2 必加 | 第 6 项 §6.3.2 |
| `tests/unit/agents/test_assembly.py` | +3 测试 | G2 必加 | 第 6 项 §6.3.3 |
| `tests/unit/agents/test_runner.py` | +2 测试 | G2 必加 | 第 6 项 §6.3.4 |
| `tests/unit/api/test_agent_apps_api.py` | +5 测试 | G2 必加 | 第 6 项 §6.3.5 |
| `tests/unit/services/test_*.py`（新文件） | +7 测试 | G2 必加 | 第 6 项 §6.3.6 |
| `tests/integration/agents/test_agent_workspace.py`（新文件） | +4 测试 | G2 必加 | 第 6 项 §6.3.7 |

**测试总数**：~44 个新测试 + 6 个保留/重命名

### 7.3 G3 集成 TODO

详见 `spec-g3-session.md` §12 "G2 集成接口预留"（已在第 1 项落盘时同步）。

**核心集成点**：
- `ensure_user_workspace_up_to_date(user_id, app_id)` 在 `POST/GET /sessions` 入口调用
- session JSON 路径：`{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.json`

### 7.4 否决项总览

| 否决项 | spec 章节 | 否决理由 | 替代方案 |
|---|---|---|---|
| `_read_agent_dir_skill_names` | §7 第 3 项 | 与 `app_cfg.skill_names` 冗余 | 直接从 `app_cfg.skill_names` 派生 |
| `compute_fingerprint` 纳入 `workspace_hash` | §7 第 7 项 | 与 `_load_skill_hashes` 冗余 | 维持原 5 输入字段 |
| `_validate_user_workspace` | §7 第 8 项 | 与 lazy 校验职责重叠 | `ensure_all_agent_workspaces` + `ensure_user_workspace_up_to_date` 双层覆盖 |
| `AGENTS_ROOT` 配置项 | §7 第 10 项 | MVP 简化 | 路径直接拼接（Phase 5+ 引入） |
| `_build_filesystem_backend` 单独函数 | §6.1 | 单点调用 | 内联在 `compile_agent_app` |
| 前端 "绑定到用户" 操作 | §7 第 11 项 | frontend stub 状态，G2 范围外 | G3 决定 |
| `test_runner` 强制 combined | §7 第 9 项 | standalone runner MVP 限制 | 函数新增，调用方暂缓 |

### 7.5 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| alembic 迁移失败（数据回填） | 中 | 中 | 迁移脚本单独跑 + 数据备份 + 灰度 |
| User 层嵌套后旧路径断链 | 中 | 高 | 兼容 SKILLS_ROOT env（双轨 1 个版本）+ 符号链接 |
| `runtime_cache` 维度变更引发性能波动 | 低 | 低 | LRU 容量 64 不变 + 三元组 key |
| PATCH 状态机解读错误（解读 B） | 低 | 中 | 既有测试 `test_patch_published_app_content_edit_reverts_status_to_draft` 已覆盖 |
| 启动期 `ensure_all_agent_workspaces` 阻塞 | 中 | 中 | 单 App try/except 隔离（第 4 项已修订） |
| lazy 校验性能开销（每个 session 启动） | 中 | 低 | hash 命中即跳过（O(1) 文件读取） |
| 迁移脚本执行期间服务不可用 | 高 | 中 | 维护窗口执行 + read-only 模式（如果可能） |

### 7.6 验收清单

- [ ] 第 1 项 v3 目录结构落地（DATA_ROOT + nested users）
- [ ] 第 2 项 alembic 迁移执行成功（4 字段 + 1 表）
- [ ] 第 3 项 service 层 3 个函数落地（publish/associate/lazy）
- [ ] 第 4 项 `ensure_all_agent_workspaces` 启动期正常
- [ ] 第 5 项 runtime cache 三元组 + `_COMPILE_USER_ID` 删除
- [ ] 第 6 项迁移脚本 + 3 个文档更新
- [ ] 所有 ~44 个新测试通过
- [ ] `make lint` + `make typecheck` 通过
- [ ] 集成测试 4 个场景通过（cross-user isolation 等）
- [ ] 手工冒烟 6 个场景通过（docs/agentapp-manual-testing.md §6.6）

---

**审查完成时间**：2026-08-26
**审查版本**：v3 修订版（最终版）
**审查方法**：分阶段逐项（6 项）；每项落盘后进入下一项
**关键产出**：
- `spec-g2-review.md`（2702 行）：完整审查报告
- 62 项关键决策已全部用户确认
- 否决 4 项 spec DoD 项（理由充分）
- ~44 个新测试用例设计
- 完整的交付清单（Phase 0-7）

---