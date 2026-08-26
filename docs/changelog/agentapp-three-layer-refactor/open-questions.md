# 遗留问题（Open Questions）

> **关联文档**：`overview.md`（路线图）、`spec-g1-auth.md`、`spec-g2-workspace.md`、`spec-g3-session.md`、`files-risks.md`
> **目标读者**：决策者（用于确定 Phase 实施细节）
> **状态**：等待用户决策

---

## 1. Q1：Phase 1 双轨兼容期长度

### 问题描述

Phase 1 将 Session 从 token 层下沉到 API 层后，旧客户端（持有 session token）需要过渡期才能切到新模式（user token + X-Session-Id 头）。

### 选项

| 选项 | 描述 | 优势 | 劣势 | 推荐场景 |
|---|---|---|---|---|
| **30 天** | 紧凑过渡期 | 上线快；新特性快速落地 | 旧客户端需紧急适配；可能遗漏边界 case | 小团队 / 内部工具 |
| **60 天**（推荐） | 适中过渡期 | 给旧客户端足够时间；可观测完整周期 | 略长 | 大部分生产场景 |
| **完整 release cycle**（90-180 天） | 宽松过渡期 | 外部客户友好 | 双轨代码保留久，债务大 | 外部客户场景 / SaaS 产品 |

### 建议

**推荐 60 天**，原因：
- 覆盖 2 个完整发布周期（旧客户端有 2 次主动升级机会）
- 双轨代码债务可控（60 天后 `legacy_session_used` 频次应趋近于零）

### 决策

| 待决 | 占位 |
|---|---|
| 选项 | ☐ 30 天 ☐ 60 天 ☐ 完整 release cycle |
| 决策人 | |
| 决策时间 | |
| 备注 | |

---

## 2. Q2：user token 有效期策略

### 问题描述

当前 `JWT_ACCESS_TOKEN_EXPIRE_DAYS=30`（详见 `docs/agentapp-manual-testing.md` §0.2）。Phase 1 后 user token 成为唯一鉴权凭证，泄漏后影响全账户。

### 选项

| 选项 | 描述 | 优势 | 劣势 |
|---|---|---|---|
| **保持 30 天**（最小改动） | 不动 | 无额外工作量 | 泄漏风险高 |
| **缩短到 7 天** | 调短有效期 | 泄漏影响范围小 | 用户每 7 天需重新登录（除非引入 refresh） |
| **7 天 + 引入 refresh token**（推荐） | access 7 天 + refresh 30 天 | 安全 + 体验兼顾 | 需新增 `/auth/refresh` 端点；前端需新增 refresh 拦截器；alembic 需新增 `refresh_token` 表 |

### refresh token 设计要点（若选）

```python
# 伪代码：app/api/v1/auth.py 新增
@router.post("/auth/refresh", response_model=ApiResponse[TokenResponse])
async def refresh_token(refresh_token: str = Form(...)) -> ApiResponse[TokenResponse]:
    """用 refresh token 换新的 access token（一次性 refresh，旧的失效）。"""
```

```sql
-- alembic 新增 refresh_tokens 表
CREATE TABLE refresh_token (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id),
    token_hash VARCHAR(64) UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    revoked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 建议

**推荐 "7 天 access + refresh token"**，原因：
- 安全与体验兼顾
- 与 LangGraph Platform / OpenAI Assistants 等业界主流对齐

### 决策

| 待决 | 占位 |
|---|---|
| 选项 | ☐ 保持 30 天 ☐ 缩短到 7 天 ☐ 7 天 + refresh（推荐） |
| 决策人 | |
| 决策时间 | |
| 备注 | |

---

## 3. Q3：Agent 层 Skill 物理路径命名

### 问题描述

Phase 2 引入 Agent 层后，需要确定 `{AGENTS_ROOT}/<??>/skills/<name>/SKILL.md` 中的 `<??>` 用什么标识。

### 选项

| 选项 | 路径 | 优势 | 劣势 |
|---|---|---|---|
| **`<app_id>`**（推荐） | `{AGENTS_ROOT}/1/skills/csv-report/SKILL.md` | 主键稳定；不可改名 | ID 不直观；运维时需查表 |
| **`<app_name>`** | `{AGENTS_ROOT}/demo-assistant/skills/csv-report/SKILL.md` | 直观；运维友好 | name 可改（虽然当前 immutable，但未来可能放开）→ 改名需迁移路径 |
| **`<app_id>_<app_name>`** | `{AGENTS_ROOT}/1_demo-assistant/skills/csv-report/SKILL.md` | 既稳定又直观 | 路径冗长 |

### 建议

**推荐 `<app_id>`**，原因：
- 主键稳定性（name 未来若放开可改，id 永不）
- 跨环境（dev/staging/prod）name 可能冲突，id 不会
- 运维侧有 `app_id` → `app_name` 的查询接口补偿

### 决策

| 待决 | 决定 |
|---|---|
| 选项 | ✅ **采纳 `<app_id>`**（v3 修订版最终决策） |
| 决策人 | 项目架构组 |
| 决策时间 | 2026-08-26 |
| 备注 | v3 评审通过；详见 `spec-g2-workspace.md` §2.1 与 `spec-g2-review.md` §1.4；主键稳定性优先，name 未来若放开可改。路径模板：`{DATA_ROOT}/agents/<app_id>/skills/<name>/SKILL.md` |

---

## 4. Q4：Agent 层与 Global 冲突时优先级

### 问题描述

Phase 2 引入 Agent 层后，可能出现 Global 与 Agent 层同名 skill 的情况。合并到 User 层时谁优先？

### 选项

| 选项 | 描述 | 适用场景 |
|---|---|---|
| **Agent 覆盖 Global**（推荐） | Agent 是发布时的快照，应优先 | Agent 层是 publish 时定格的，优先符合"快照优先"语义 |
| **Global 永远优先** | Global 是共享基线，不可被 Agent 覆盖 | 防止 Agent 误改全局 |
| **按引用顺序** | `materialize_to_user_combined` 调用顺序后者覆盖前者 | 实现简单但语义模糊 |

### 建议

**推荐 Agent 覆盖 Global**，原因：
- Agent 层是 publish 时的快照，本质是 Agent 拥有者对 Global 的"私有化定制"
- 符合用户对"Agent 专属"的预期（用户发布时改的就是这个）
- 若要全局生效，编辑 Global skill 即可，无需 Agent 层覆盖

### 决策

| 待决 | 决定 |
|---|---|
| 选项 | ✅ **采纳 "Agent 覆盖 Global"**（v3 修订版最终决策） |
| 决策人 | 项目架构组 |
| 决策时间 | 2026-08-26 |
| 备注 | v3 评审通过；详见 `spec-g2-workspace.md` §4.3 与 `spec-g2-review.md` §3.4.3；Agent 层是 publish 时的快照，本质是 Agent 拥有者对 Global 的"私有化定制"，符合用户对"Agent 专属"的预期。实现逻辑：`materialize_to_user_combined` 中 `agent_path if agent_path.exists() else _global_skill_file(name)` |

---

## 5. Q5：Session 存储是否真要改造

### 问题描述

详见 `spec-g3-session.md`。当前 PG 存储运行良好，是否值得引入 SQLite / JSON 改造？

### 选项

| 选项 | 描述 | 实施 |
|---|---|---|
| **保留 PG + JSON 视图层**（推荐） | 主存储不动；仅新增 `GET /sessions/{id}/export` 端点 | Phase 3 推荐方案 A（详见 `spec-g3-session.md` §4） |
| **引入 SQLite** | 主存储改 SqliteSaver；双写 1-2 个版本后切读 | Phase 3 备选方案 B |
| **纯 JSON 文件** | 主存储改 JSON；自实现 Checkpointer | Phase 3 方案 C（不推荐） |

### 建议

**强烈推荐保留 PG**，原因：
- LangGraph checkpointer 天然依赖 SQL（HIL 中断恢复必需）
- 改 SQLite 工作量等同于调研 + 自适配层
- 改 JSON 不可行（LangGraph 不支持 JSON checkpointer）

仅在以下条件满足时启动方案 B：

- LangGraph 1.x 官方 SqliteSaver 兼容性 ≥ 90%
- PG 性能瓶颈明确（chat TPS < 100 / p95 > 500ms）
- 多设备离线场景需求明确

### 决策

| 待决 | 占位 |
|---|---|
| 选项 | ☐ 保留 PG（推荐） ☐ 引入 SQLite ☐ 引入 JSON |
| 决策人 | |
| 决策时间 | |
| 备注 | |

---

## 6. Q6：启动哪个 Phase 的实现细节设计

### 问题描述

评估已完成；下一步是选定 Phase 进入实现细节设计阶段。

### 选项（可多选）

| 选项 | 描述 | 产出物 |
|---|---|---|
| **Phase 1 alembic 迁移骨架** | （Phase 1 不涉及 alembic；跳过） | — |
| **Phase 1 后端 auth.py 重构 spec** | 详细列出每个函数签名、依赖图、回滚步骤 | 详细设计 spec |
| **Phase 1 前端 useAuth.ts 重构 spec** | 详细列出内存态 session_id 设计、request.ts 拦截器变更 | 详细设计 spec |
| **Phase 2 skills_store.py 三级路径方案** | 详细列出每个新函数签名 + 测试用例 | 详细设计 spec + 测试清单 |
| **Phase 2 publish 同步流程** | 详细列出 publish 流程伪代码 + associate-user 端点设计 | 详细设计 spec |
| **Phase 3 Session 存储评估报告** | 输出 LangGraph SqliteSaver 兼容性调研结论 | 调研报告 |
| **Phase 3 JSON 视图层 spec** | 详细列出 export 端点 + 前端集成 | 详细设计 spec |
| **暂不启动** | 仅保留当前评估文档 | — |

### 建议

**推荐组合**（按依赖顺序）：
1. **Phase 1 后端 auth.py 重构 spec**（最关键；其他 Phase 1 工作依赖此）
2. **Phase 1 前端 useAuth.ts 重构 spec**（与后端并行设计）

Phase 2 / Phase 3 待 Phase 1 上线后观测再启动。

### 决策

| 待决 | 占位 |
|---|---|
| 选项（可多选） | ☐ Phase 1 后端 ☐ Phase 1 前端 ☐ Phase 2 � Phase 3 ☐ 暂不启动 |
| 决策人 | |
| 决策时间 | |
| 备注 | |

---

## 7. 决策记录模板

每个决策完成后，更新本节追加决策结果：

```markdown
## 决策记录

### YYYY-MM-DD Q1 双轨兼容期长度
- 决策人：[姓名]
- 选项：60 天
- 备注：覆盖 2 个完整发布周期；与产品 release 节奏对齐
- 影响 spec-g1-auth.md §6；更新默认值为 60 天

### YYYY-MM-DD Q2 user token 有效期
- 决策人：[姓名]
- 选项：7 天 + refresh token
- 备注：...
- 影响 spec-g1-auth.md 新增 §11 "Refresh Token 设计"
```

---

## 7.1 实际决策记录

### 2026-08-26 Q3 Agent 层 Skill 物理路径命名
- **决策人**：项目架构组
- **选项**：✅ **采纳 `<app_id>`**（v3 修订版最终决策）
- **备注**：主键稳定性优先（name 未来若放开可改，id 永不）；跨环境（dev/staging/prod）name 可能冲突，id 不会；运维侧有 `app_id` → `app_name` 的查询接口补偿。评审发现 v3 嵌套设计后，User 层同时嵌套在 `<app_id>` 下，两层命名一致
- **影响 spec**：
  - `spec-g2-workspace.md` §2.1（目录结构：路径模板 `{DATA_ROOT}/agents/<app_id>/skills/<name>/SKILL.md`）
  - `spec-g2-workspace.md` §3.3（agent_dir 字段：`{DATA_ROOT}/agents/<app_id>`）
  - `files-risks.md` §2.2（后端文件清单：路径拼接不依赖 `AGENTS_ROOT` 配置项）
  - `files-risks.md` §4.1（alembic 迁移：agent_dir 回填路径 `{data_root}/agents/{id}`）
- **关联决策**：同时确认**不引入** `AGENTS_ROOT` 配置项（MVP 简化，路径直接拼接；详见 `spec-g2-review.md` §6.2 N4）

### 2026-08-26 Q4 Agent 层与 Global 冲突时优先级
- **决策人**：项目架构组
- **选项**：✅ **采纳 "Agent 覆盖 Global"**（v3 修订版最终决策）
- **备注**：Agent 层是 publish 时的快照，本质是 Agent 拥有者对 Global 的"私有化定制"，符合用户对"Agent 专属"的预期。若要全局生效，编辑 Global skill 即可，无需 Agent 层覆盖
- **实现逻辑**：`materialize_to_user_combined` 中 `agent_path if agent_path.exists() else _global_skill_file(name)` —— Agent 层优先于 Global
- **影响 spec**：
  - `spec-g2-workspace.md` §4.3（`materialize_to_user_combined` 函数：合并去重，Agent 覆盖 Global）
  - `spec-g2-workspace.md` §1.1 目标（重申 Agent 覆盖 Global 语义）
  - `files-risks.md` §2.2（skills_store.py 变更描述）
- **关联决策**：同时确认 `_read_agent_dir_skill_names` 否决（与 `app_cfg.skill_names` 冗余，详见 `spec-g2-review.md` §6.2 N1）

---

## 8. 关联 spec 章节索引

| Q | 影响 spec | 章节 |
|---|---|---|
| Q1 | `spec-g1-auth.md` | §6 双轨兼容策略 |
| Q2 | `spec-g1-auth.md` | （新增 refresh token 章节） |
| Q3 | `spec-g2-workspace.md` | §2.1 目录结构 / §3.1 alembic 迁移 |
| Q4 | `spec-g2-workspace.md` | §4.3 `materialize_to_user_combined` 语义 |
| Q5 | `spec-g3-session.md` | §3 三方案对比 / §4 推荐方案 / §5 备选方案 |
| Q6 | （不直接关联；启动后续 spec 设计） | — |
| Q7 | `spec-g3-session.md` | §11 全新会话 CRUD API（URL / 鉴权 / chatbot 废弃） |

---

## 9. 不属于本次评估的开放问题（后续 Phase 处理）

以下问题在本次评估中**明确不解决**，留给后续 Phase：

- Refresh token 旋转策略（Phase 1.5）
- Session 自动过期清理策略（Phase 3）
- Workspace 跨环境同步（dev/staging/prod）（独立 Phase）
- MCP 工具目录与 Agent 层 Skill 的统一命名空间（独立 Phase）
- Workflow 引擎与 Agent 层的集成（Workflow 单独 spec）
- 多 AgentApp 共享 Agent 层 Skill 的权限控制（独立 Phase）

---

## 10. 决策完成度

| Q | 状态 | 决策时间 |
|---|---|---|
| Q1 | ☑ 已决策（无双轨兼容期） | Phase 1 spec 落地 |
| Q2 | ☑ 已决策（7 天 access + 30 天 refresh） | Phase 1 spec 落地 |
| Q3 | ☑ **已决策（采纳 `<app_id>`）** | **2026-08-26**（G2 v3 评审通过） |
| Q4 | ☑ **已决策（采纳 "Agent 覆盖 Global"）** | **2026-08-26**（G2 v3 评审通过） |
| Q5 | ☑ 已决策（保留 PG + JSON 视图层） | Phase 3 spec 落地 |
| Q6 | ☐ 待决 | — |
| Q7 | ☑ 已决策（RESTful `/sessions` + 单层 user 鉴权 + chatbot 废弃） | Phase 3 spec 落地 |

> **更新说明**：2026-08-26 Q3/Q4 在 G2 spec v3 评审中正式决策（详见 §7.1 实际决策记录）。Q6（启动哪个 Phase 实现细节设计）仍待决，需项目架构组决定启动顺序（推荐：Phase 1 → Phase 2 → Phase 3 严格顺序）。

> **全部核心 Q 决策完成**（Q1/Q2/Q3/Q4/Q5/Q7 已决策，仅 Q6 启动顺序待决）。Q1/Q2/Q5/Q7 在对应 spec 中决策（spec-g1-auth.md / spec-g3-session.md），Q3/Q4 在本文件 §7.1 中决策。

> **代码实施启动条件**：Q6（启动哪个 Phase 实现细节设计）决策后可启动代码实施；Q6 决策前仅可继续完善 spec。
