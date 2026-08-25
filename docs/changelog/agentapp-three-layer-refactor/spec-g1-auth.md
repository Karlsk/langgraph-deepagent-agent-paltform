# G1 Spec：认证体系简化

> **主题**：把 Session 从 token 层下沉到 API 层；user token 成为唯一鉴权；新增 Refresh Token 机制。
> **关联文档**：`overview.md`（路线图）、`files-risks.md`（文件清单 + 风险）、`open-questions.md`（Q1-Q6 决策记录）
> **目标读者**：后端 + 前端实施者
> **风险等级**：低（直接替换，新项目无存量客户端）
> **估算工时**：1.5 周（含 Refresh Token 设计）

---

## 1. 目标与非目标

### 1.1 目标

1. **Token 单层化**：`POST /auth/login` 返回的 user token 是唯一 JWT 颁发点；`POST /auth/session` 不再颁发 token
2. **Session 显式化**：Chat 类端点（推迟到 Phase 3）通过请求头 `X-Session-Id` 显式接收 session_id；session 元数据仍存 PG
3. **业务端点鉴权单层化**：所有业务端点（subagents / skills / apps / mcp_servers / providers / tools）使用 `Depends(get_current_user)`，不再依赖 session
5. **Refresh Token 机制**：access token 7 天 + refresh token 30 天 + 旋转策略 + 重放检测全量启用
6. **会话 API 注释废弃**：原 `/auth/session` 端点注释（不删除），保留路由 schema；新会话 CRUD 在 Phase 3 实现

### 1.2 非目标（不在本 Phase 范围）

- chatbot API 改造（不在本 Phase 范围；chatbot 路由与业务代码保留现状；chatbot 整体推迟到 Phase 3）
- 新会话 CRUD API（推迟到 Phase 3，含 list / get / create / update（rename）/ delete）
- Workspace 三级改造（Phase 2 范围）
- Session 存储选型（Phase 3 范围，详见 `spec-g3-session.md`）

> 注：原"双轨兼容"策略已**整体删除**。本 Phase 是新项目首版，直接替换；不保留旧 session token 颁发路径。

---

## 2. 当前 vs 提议 详细对比

| 维度 | 当前架构 | 提议架构 |
|---|---|---|
| **鉴权层数** | 两层：user token + session token | 单层：仅 user token |
| **Token 颁发点** | `POST /auth/login` → user token；`POST /auth/session` → session token | `POST /auth/login` → user + refresh；`/auth/session` 注释 |
| **Session 概念位置** | Session 行持久化 + 会话 token 双重表达 | Session 行持久化 + 仅 API 层显式接收 `session_id`（Phase 3） |
| **业务端点鉴权** | `Depends(get_current_session)` | `Depends(get_current_user)` |
| **前端 storage** | localStorage 存 `auth.sessionToken` + `auth.user` 双 key | localStorage 存 `auth.user` + `auth.userToken` 双 key；session 概念推迟到 Phase 3 |
| **会话创建** | `/auth/session` 是"创建 + 颁发 token"二合一 | `/auth/session` 端点注释（仅占位）；新会话 CRUD 在 Phase 3 实现 |
| **多设备登录** | 每设备开 session | 自然支持（user token 不变 + 多 session_id，Phase 3 落地） |
| **JWT 复杂度** | 高（同一 `create_access_token` 服务 user/session，sub 字段语义不同） | 低（`create_access_token` 仅服务 user；refresh token 独立签名） |
| **Token 生命周期** | user token 30 天长期有效 | access token 7 天 + refresh token 30 天 + 旋转 |
| **风险点** | session token 与 user token 混用易引发 401 混淆 | access token 短期 + refresh 旋转；refresh token 哈希存储防泄漏（详见 §10.6 安全） |

---

## 3. 后端 API 契约变更

### 3.1 端点契约

#### `POST /auth/login`（响应 schema 扩展）

```text
旧：
POST /auth/login → ApiResponse[TokenResponse{access_token, token_type, expires_at}]

新：
POST /auth/login → ApiResponse[LoginResponse{
    access_token: str           # 7 天有效
    refresh_token: str          # 30 天有效（一次性，明文返回给客户端）
    token_type: str = "bearer"
    expires_at: datetime        # access_token 过期时间
}]
```

> 变更要点：响应增加 `refresh_token` 字段；access_token 有效期由 30 天缩短为 7 天。

#### `POST /auth/session`（**注释废弃**）

```text
旧：
POST /auth/session → ApiResponse[SessionResponse{session_id, name, token: TokenResponse}]

新（Phase 1 期间）：
POST /auth/session → ApiResponse[None]   # 端点注释，handler 保留为 noop；返回 200 + code=0 + data=null

Phase 3 重做：
POST /sessions → ApiResponse[SessionRead{...}]   # 完整 CRUD 端点（list / get / create / patch / delete）
```

> 端点 URL 在 Phase 3 重命名为 `/sessions`（RESTful 风格）；旧 URL `/auth/session` 注释保留 1 个 release 后删除。

#### `POST /auth/refresh`（新增）

```text
POST /auth/refresh
Content-Type: application/json

Request:
{
    "refresh_token": str   # 上次登录返回的 refresh_token
}

Response:
ApiResponse[LoginResponse{
    access_token: str      # 新的 access_token
    refresh_token: str     # 新的 refresh_token（旧 refresh_token 已撤销）
    token_type: str = "bearer"
    expires_at: datetime
}]

错误：
- 401 INVALID_REFRESH_TOKEN：refresh_token 不存在 / 已过期 / 已撤销
- 401 REFRESH_TOKEN_REPLAY：refresh_token 已被使用过（防重放，触发全 user 强制重新登录）
```

#### `POST /auth/logout`（新增，可选）

```text
POST /auth/logout
Content-Type: application/json

Request:
{
    "refresh_token": str   # 当前会话的 refresh_token
}

Response:
ApiResponse[None]
```

> logout 仅撤销指定 refresh_token；不删除 access_token（access_token 在 7 天内仍有效但无法 refresh）。强制下线需额外机制（待 Q2 决策）。

### 3.2 Schema 变更（`app/schemas/auth.py`）

```python
# === 1. LoginResponse 新增（替换 TokenResponse 用法） ===
class LoginResponse(BaseResponse):
    access_token: str
    refresh_token: str                          # 新增
    token_type: str = "bearer"
    expires_at: datetime

# === 2. SessionResponse / SessionCreate 注释废弃（Phase 3 重做） ===
# 保留类名定义（避免 import 报错），但标注 DEPRECATED
class SessionResponse(BaseResponse):  # DEPRECATED: Phase 3 重做为完整 CRUD schema
    session_id: str = ""
    name: str = ""
    # token 字段已删除（Phase 1 关键变更）

class SessionCreate(BaseModel):  # DEPRECATED: Phase 3 重做
    agent_app_id: int | None = None

# === 3. RefreshTokenRequest 新增 ===
class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=32, max_length=128)

class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., min_length=32, max_length=128)
```

> 向后兼容策略：`TokenResponse` 类保留（不删除），但内部调用统一迁移到 `LoginResponse`。

---

## 4. 依赖层重构（`app/api/v1/auth.py`）

### 4.1 删除章节：`get_current_user_with_session_id`

> 原 plan 中计划新增的 `get_current_user_with_session_id` 依赖**不再需要**——业务端点仅用 `Depends(get_current_user)`，chatbot 端点推迟到 Phase 3，Phase 1 无消费方。

### 4.2 删除章节：双轨兼容 + `create_compat_session_token`

> 整体删除。新项目直接替换，无兼容代码。

### 4.3 `create_access_token` 重构（`app/utils/auth.py`）

```python
# 新签名（仅服务于 user）
def create_access_token(
    subject: str | int,           # 仅接收 user.id
    expires_delta: timedelta | None = None,
) -> Token:
    """颁发 user access_token（Phase 1 唯一鉴权凭证，7 天有效）。

    注意：refresh_token 不通过此函数颁发；走独立的 create_refresh_token。
    """
    ...

# 新增 refresh token 颁发
def create_refresh_token() -> str:
    """生成 refresh_token（明文），返回给客户端；DB 仅存 sha256 哈希。"""
    raw = secrets.token_urlsafe(48)  # 64 字符 base64
    return raw

def hash_refresh_token(raw: str) -> str:
    """对 refresh_token 计算 sha256 哈希（用于 DB 存储）。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

# refresh_token 表操作（详见 §10.2）
class RefreshTokenStore:
    async def create(self, session: AsyncSession, user_id: int, raw_token: str, expires_at: datetime) -> RefreshToken: ...
    async def lookup(self, session: AsyncSession, raw_token: str) -> RefreshToken | None: ...
    async def rotate(self, session: AsyncSession, old: RefreshToken) -> RefreshToken: ...
    async def revoke(self, session: AsyncSession, raw_token: str) -> bool: ...
    async def revoke_all_for_user(self, session: AsyncSession, user_id: int) -> int: ...
```

### 4.4 业务端点改造（核心变更）

```python
# app/api/v1/{subagents,skills,apps,mcp_servers,providers}.py
# 所有受保护端点统一替换：

# 旧（Phase 1 之前）：
from app.api.v1.auth import get_current_session
async def list_skills(
    current_session: ChatSession = Depends(get_current_session),
) -> ApiResponse[list[SkillRead]]:
    user_id = current_session.user_id
    ...

# 新（Phase 1）：
from app.api.v1.auth import get_current_user
async def list_skills(
    user: User = Depends(get_current_user),
) -> ApiResponse[list[SkillRead]]:
    user_id = user.id
    ...

# 文件清单（按改动量排序）：
# - app/api/v1/subagents.py        (~10 端点)
# - app/api/v1/skills.py           (~8 端点)
# - app/api/v1/api.py              (~6 端点, AgentApp CRUD)
# - app/api/v1/mcp_servers.py      (~8 端点)
# - app/api/v1/providers.py        (~6 端点)
# - app/api/v1/tools.py            (~4 端点)
```

> **不涉及的文件**：`app/api/v1/auth.py`（仅增 refresh 路由，不改鉴权依赖）；`app/api/v1/chatbot.py`（chatbot 整体推迟到 Phase 3）。

---

## 5. 前端变更清单

| 文件 | 变更 | 改动量 |
|---|---|---|
| `agent-web/src/api/auth.ts` | `SessionResponse` 改为可空（兼容占位）；新增 `refreshToken()` / `logout()` | 中 |
| `agent-web/src/utils/authStorage.ts` | **保留** `USER_KEY`；**新增** `USER_TOKEN_KEY`；删除 `SESSION_TOKEN_KEY` 相关 | 中 |
| `agent-web/src/composables/useAuth.ts` | `login` 简化为写 user + userToken；**删除** `exchangeSession`；**新增** `refreshUserToken()` | 中 |
| `agent-web/src/utils/request.ts` | 拦截器改为注入 user token（无 X-Session-Id）；**新增** refresh 拦截器（401 自动 refresh + 重发） | 中 |
| `agent-web/src/views/auth/Login.vue` | 不变 | 无 |
| `agent-web/src/views/auth/Register.vue` | 不变 | 无 |
| `agent-web/tests/auth.spec.ts` | 更新断言（userToken 持久化；无 sessionToken）；新增 refresh 拦截器用例 | 小 |

### 5.1 authStorage 双 key 实现（修订版）

```ts
// agent-web/src/utils/authStorage.ts
const USER_KEY = 'auth.user'               // 保留
const USER_TOKEN_KEY = 'auth.userToken'    // 新增（access_token 7 天有效）
const SESSION_TOKEN_KEY = 'auth.sessionToken'  // 删除（不再使用）

// 双 key：auth.user（User 基础信息）+ auth.userToken（access_token 明文）
export function getUser(): User | null { return safeGetJson(USER_KEY) }
export function setUser(u: User): void { safeSetJson(USER_KEY, u) }

export function getUserToken(): string | null { return safeGetItem(USER_TOKEN_KEY) }
export function setUserToken(token: string): void { safeSetItem(USER_TOKEN_KEY, token) }

export function clearAll(): void {
  safeRemoveItem(USER_KEY)
  safeRemoveItem(USER_TOKEN_KEY)
  // 删除遗留的旧 sessionToken key（一次性清理，1 个 release 后移除）
  safeRemoveItem(SESSION_TOKEN_KEY)
}
```

> 安全提示：access_token 7 天有效，泄漏风险较 30 天大幅下降。refresh_token 仍存后端 DB 哈希，不落前端。

### 5.2 request.ts 拦截器变更（修订版）

```ts
// 旧：
config.headers.Authorization = `Bearer ${getSessionToken()}`

// 新：
config.headers.Authorization = `Bearer ${getUserToken()}`  // 来自 authStorage
// 不再注入 X-Session-Id（chatbot 推迟到 Phase 3）
```

### 5.3 useAuth.ts login 流程（修订版）

```ts
export async function login(email: string, password: string): Promise<void> {
  const resp = await loginApi(email, password)        // 返回 LoginResponse
  setUserToken(resp.access_token)
  setRefreshToken(resp.refresh_token)                  // 新增：内存态（不持久化）
  setUser({ id: extractUserId(resp.access_token), email, username: null })
  user.value = getUser()
  userToken.value = getUserToken()
}

// 新增：刷新 access_token
export async function refreshUserToken(): Promise<string | null> {
  const rt = getRefreshToken()
  if (!rt) return null
  try {
    const resp = await refreshApi(rt)                  // POST /auth/refresh
    setUserToken(resp.access_token)
    setRefreshToken(resp.refresh_token)
    userToken.value = getUserToken()
    return resp.access_token
  } catch (e) {
    clearAll()
    return null
  }
}
```

### 5.4 refresh 拦截器（新增，伪代码）

```ts
// agent-web/src/utils/request.ts
import { refreshUserToken, clearAll } from '@/composables/useAuth'
import { useRouter } from 'vue-router'

const router = useRouter()

axios.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401 && !error.config._retried) {
      error.config._retried = true
      const newToken = await refreshUserToken()
      if (newToken) {
        error.config.headers.Authorization = `Bearer ${newToken}`
        return axios.request(error.config)
      }
      // refresh 失败：清空登录态 + 跳登录页
      clearAll()
      router.push('/login')
    }
    return Promise.reject(error)
  }
)
```

---

## 6. DoD（Definition of Done）

- [ ] `POST /auth/login` 响应改为 `LoginResponse{access_token, refresh_token, ...}`
- [ ] `POST /auth/session` 端点注释（handler 改为 noop，返回 `ApiResponse[None]`），路由保留
- [ ] `POST /auth/refresh` 端点实现（旋转策略 + 重放检测）
- [ ] `POST /auth/logout` 端点实现（可选）
- [ ] alembic 迁移：`refresh_token` 表（详见 §10.2）
- [ ] `app/services/refresh_token_store.py` 新增（token 哈希存储 / 旋转 / 撤销）
- [ ] `app/api/v1/{subagents,skills,apps,mcp_servers,providers,tools}.py` 所有受保护端点改用 `Depends(get_current_user)`
- [ ] 删除 `get_current_session` 依赖；删除 `create_compat_session_token`
- [ ] `app/utils/auth.py` 新增 `create_refresh_token` / `hash_refresh_token` / `RefreshTokenStore`
- [ ] 前端 `authStorage.ts` 删除 `SESSION_TOKEN_KEY`；新增 `USER_TOKEN_KEY`
- [ ] 前端 `useAuth.ts` 简化 `login`；新增 `refreshUserToken()`；删除 `exchangeSession`
- [ ] 前端 `request.ts` 改为注入 user token；新增 refresh 拦截器
- [ ] 前端测试 `tests/auth.spec.ts` 更新断言
- [ ] 文档更新：`docs/authentication.md` 重写为单层 + Refresh 章节

---

## 7. 验证（Verification）

### 7.1 单元测试

- `tests/unit/api/test_auth.py`：
  - `test_session_no_token_field`：验证 `/auth/session` 响应无 token
  - `test_session_endpoint_returns_null`：验证 `/auth/session` 返回 `data=null`
  - `test_business_endpoint_accepts_user_token`：验证业务端点用 user token 即可访问
- `tests/unit/utils/test_auth.py`：
  - `test_create_access_token_user_only`：验证 `create_access_token` 仅接受 user.id
  - `test_refresh_token_hash_consistent`：验证 hash 函数稳定
- `tests/unit/services/test_refresh_token_store.py`：
  - `test_create_and_lookup`
  - `test_rotate_revokes_old`
  - `test_revoke_all_for_user`
  - `test_replay_detection_triggers_full_revoke`

### 7.2 集成测试

- `tests/integration/api/test_auth_flow.py`：
  - `test_login_returns_access_and_refresh`：验证 login 返回双 token
  - `test_refresh_rotates_refresh_token`：验证旋转
  - `test_refresh_revokes_old_refresh_token`：验证旧 token 失效
  - `test_logout_revokes_refresh_token`：验证 logout 撤销
  - `test_access_token_expired_can_refresh`：验证 access 过期可用 refresh
  - `test_full_login_to_business`：login → 业务端点访问（user token）→ 成功
  - 推迟到 Phase 3：`test_full_login_to_chat`、`test_multi_device_same_user`

### 7.3 手工冒烟（参考 `docs/agentapp-manual-testing.md` 第 2 节）

1. 新客户端：login → 拿到 access + refresh → 业务端点访问（user token）→ 200 OK
2. Refresh 流程：等待 7 天（或修改配置缩短）→ access 过期 → 业务端点 401 → 自动 refresh → 重发 → 200 OK
3. 登出流程：login → logout → refresh 重试 → 401 → 跳转登录页
4. 重放检测：保存旧 refresh_token → 旋转后用旧 token refresh → 全 user 强制重新登录

---

## 8. 关键变更影响文件（详细见 `files-risks.md` §2）

| 文件 | 变更 |
|---|---|
| `app/api/v1/auth.py` | 新增 `POST /auth/refresh` 与 `POST /auth/logout`；删除 `create_session`/`get_current_session` |
| `app/utils/auth.py` | `create_access_token` 仅服务 user；新增 refresh token 函数 |
| `app/schemas/auth.py` | 新增 `LoginResponse`/`RefreshTokenRequest`/`LogoutRequest`；`SessionResponse`/`SessionCreate` 注释 |
| `app/models/refresh_token.py` | **新增** RefreshToken 模型 |
| `app/services/refresh_token_store.py` | **新增** refresh token CRUD 服务 |
| `alembic/versions/xxx_refresh_token.py` | **新增** alembic 迁移 |
| `app/api/v1/{subagents,skills,apps,mcp_servers,providers,tools}.py` | 鉴权依赖改 `Depends(get_current_user)` |
| `agent-web/src/api/auth.ts` | 适配新 LoginResponse；新增 refresh/logout API |
| `agent-web/src/utils/authStorage.ts` | 双 key（auth.user + auth.userToken） |
| `agent-web/src/composables/useAuth.ts` | 简化 login；新增 refreshUserToken |
| `agent-web/src/utils/request.ts` | 拦截器 + refresh 重发 |
| `agent-web/tests/auth.spec.ts` | 测试更新 |
| `docs/authentication.md` | 重写为单层 + Refresh 章节 |

---

## 9. 回滚策略

如 Phase 1 上线后观测到 refresh token 滥用或鉴权问题：

1. **后端回滚**：revert `Depends(get_current_user)` 替换（恢复 `get_current_session`）；revert refresh 端点（恢复原 `create_access_token(session_id)` 双 sub）
2. **前端回滚**：revert `authStorage.ts` / `useAuth.ts` / `request.ts`；恢复 session token 持久化
3. **数据回滚**：`alembic downgrade -1` 撤销 `refresh_token` 表（注意：已发放的 refresh_token 全部失效，强制用户重新登录）

> 因无双轨兼容期，回滚窗口内所有用户需重新登录。回滚仅建议在发布后 24 小时内执行。

---

## 10. Refresh Token 详细设计

### 10.1 设计原则

- **access token 短期**：7 天有效；泄漏风险较 30 天大幅下降
- **refresh token 长期**：30 天有效；仅通过 HTTPS 传输
- **旋转策略**：每次 refresh 颁发新 refresh_token，旧 token 立即撤销
- **哈希存储**：DB 仅存 sha256(refresh_token)，不存明文；泄漏 DB 不会导致 token 可用
- **重放检测**：已撤销的 refresh_token 再次使用 → 视为攻击，撤销该 user 的全部 refresh_token，强制重新登录

### 10.2 数据模型

alembic 迁移（`alembic/versions/xxx_refresh_token.py`）：

```python
# 新表 refresh_token
class RefreshToken(BaseModel, table=True):
    __tablename__ = "refresh_token"
    id: int = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    token_hash: str = Field(unique=True, index=True)   # sha256(refresh_token)
    expires_at: datetime = Field(index=True)
    revoked: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: datetime | None = Field(default=None)

# 索引：
# - ix_refresh_token_user_id (user_id)
# - uq_refresh_token_token_hash (token_hash, unique)
# - ix_refresh_token_expires_at (expires_at) - 定期清理任务
```

### 10.3 端点细节

**`POST /auth/refresh`**

```python
@router.post("/auth/refresh", response_model=ApiResponse[LoginResponse])
async def refresh_token(req: RefreshTokenRequest) -> ApiResponse[LoginResponse]:
    async with db_service.get_async_session() as session:
        token_hash = hash_refresh_token(req.refresh_token)
        existing = await refresh_token_store.lookup(session, token_hash)

        # 1. 不存在或已过期
        if not existing or existing.expires_at < datetime.utcnow():
            raise HTTPException(401, "INVALID_REFRESH_TOKEN")

        # 2. 已撤销 → 重放检测：撤销该 user 全部 refresh_token
        if existing.revoked:
            logger.warning("refresh_token_replay_detected", user_id=existing.user_id)
            await refresh_token_store.revoke_all_for_user(session, existing.user_id)
            raise HTTPException(401, "REFRESH_TOKEN_REPLAY")

        # 3. 旋转：撤销旧，颁发新
        new_raw = create_refresh_token()
        new_hash = hash_refresh_token(new_raw)
        new_record = await refresh_token_store.create(
            session, user_id=existing.user_id, raw_token=new_raw, expires_at=datetime.utcnow() + timedelta(days=30)
        )
        await refresh_token_store.revoke(session, existing)

        # 4. 颁发新的 access_token
        user = await db_service.get_user_by_id(session, existing.user_id)
        new_access = create_access_token(user.id, expires_delta=timedelta(days=7))

        return ApiResponse.success(LoginResponse(
            access_token=new_access.access_token,
            refresh_token=new_raw,
            token_type="bearer",
            expires_at=new_access.expires_at,
        ))
```

**`POST /auth/logout`**

```python
@router.post("/auth/logout", response_model=ApiResponse[None])
async def logout(req: LogoutRequest) -> ApiResponse[None]:
    async with db_service.get_async_session() as session:
        token_hash = hash_refresh_token(req.refresh_token)
        await refresh_token_store.revoke(session, token_hash)
        return ApiResponse.success(None)
```

### 10.4 旋转策略详解

| 操作 | 颁发新 access | 颁发新 refresh | 撤销旧 refresh |
|---|---|---|---|
| login | ✓ | ✓ | n/a |
| refresh（成功） | ✓ | ✓（旋转） | ✓ |
| refresh（重放检测） | ✗ | ✗ | **全 user 撤销** |
| logout | ✗ | ✗ | ✓（仅本 token） |

### 10.5 配置（`app/core/config.py`）

```python
class Settings(BaseSettings):
    # 旧值：JWT_ACCESS_TOKEN_EXPIRE_DAYS = 30
    JWT_ACCESS_TOKEN_EXPIRE_DAYS: int = 7     # 新值（缩短自 30）
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30   # 新增
    REFRESH_TOKEN_HASH_ALGORITHM: str = "sha256"  # 新增
```

### 10.6 安全清单

- [ ] refresh_token 仅通过 HTTPS 传输（生产环境强制）
- [ ] structlog 不打印 refresh_token 明文（仅记 token_hash 前 8 位用于关联）
- [ ] logout 立即撤销 refresh_token（best-effort，失败也返回 200）
- [ ] 重放检测触发后全 user 强制重新登录（保护账户安全）
- [ ] refresh_token DB 定期清理任务（expires_at < now - 7d 删除）
- [ ] access_token 短期化降低 XSS 攻击窗口
- [ ] `/auth/refresh` 端点速率限制（slowapi，10/min/IP）

### 10.7 监控指标（Prometheus）

| 指标 | 类型 | 说明 |
|---|---|---|
| `auth_refresh_total{status}` | Counter | status ∈ {success, replay_detected, invalid, expired} |
| `auth_refresh_replay_total` | Counter | 重放检测触发次数（告警阈值 > 0） |
| `auth_logout_total` | Counter | logout 调用次数 |
| `refresh_token_active_count` | Gauge | 当前活跃 refresh_token 数 |

---

> **Phase 1 边界说明**：本 spec 范围严格限于 G1（认证体系简化 + Refresh Token 机制）。chatbot API 改造、新会话 CRUD、Workspace 三级改造、Session 存储选型均不在本 Phase 范围，分别由 Phase 2 / Phase 3 对应 spec 覆盖。