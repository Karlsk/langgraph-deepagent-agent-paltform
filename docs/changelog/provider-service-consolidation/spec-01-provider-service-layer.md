# spec-01 · Provider service 层收敛（技术债，下个周期）

| 里程碑 | 端 | 人日 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| 待定 | 后端 | 4-5 | 无（独立重构） | **未排期** — 2026-09-11 记录 |

> 本文档是**技术债登记 + 可执行方案**，本周期不实现。
> 发现契机：`docs/changelog/workflow-llm-provider-integration/spec-01-contract-change.md` 的 Stage C
> 需要一个「跨模块解析 provider/model」的出口，查证后发现 provider **根本没有 service 层**。

## 1. 问题陈述

### 1.1 provider 不是三层，是「API 层直接写 ORM」

`app/api/v1/providers.py` 共 **1156 行**、15 个端点，其中：

- `select(` / `db.exec` / `Session` 出现 **39 次**
- 直接 import `sqlmodel.select`、`sqlmodel.col`、`sqlalchemy.func`（第 25-28 行）
- 自带 5 个私有 ORM 查询函数：

| 函数 | 行 | 职责 |
| --- | --- | --- |
| `_get_provider(db, name)` | 185 | 按 name 查未删 provider |
| `_list_models(db, provider_id)` | 190 | 查 provider 下未删 model |
| `build_model_catalog(db)` | 201 | 全量 `ref -> (Provider, ModelConfig)` 映射 |
| `_model_fingerprint(catalog)` | 221 | catalog 哈希（编译缓存指纹） |
| `_referencing_owners(db, ref)` | 230 | 反查引用该 ref 的资产 |

### 1.2 查询逻辑与 `llm_store.py` 重复

`app/services/llm/llm_store.py`（250 行）也在做同类查询，两处互不复用：

| providers.py | llm_store.py | 重复点 |
| --- | --- | --- |
| `_get_provider(db, name)` :185 | `load_model_config` 内的 provider 查询 :139-143 | 同一条 `SELECT Provider WHERE name=? AND deleted=false` |
| `_list_models(db, provider_id)` :190 | `list_models_under_deleted_provider(db, name)` :236 | 都是「某 provider 下的 model 列表」 |
| `build_model_catalog(db)` :201 | `_available_refs(session)` :120 | 都全表扫 provider + model 再拼 ref |

### 1.3 已造成的真实缺陷

**`_referencing_owners`（providers.py:230-234）只扫 `AgentApp.model` 与 `SubAgentConfig.model`，扫不到工作流定义。**

后果：删除一个被工作流引用的 provider/model 不会返回 422，该工作流会在**执行期**才失败。因为「谁在引用这个 ref」没有统一出口，每个新引用方都要记得去改 `_referencing_owners`——而工作流接入时（`workflow-llm-provider-integration`）就漏了。

这是分层缺失的**典型代价**：引用完整性检查散落在 API 层的私有函数里，新增消费方无从知晓。

### 1.4 现有 5 个调用方全部直连 store

| 文件 | import |
| --- | --- |
| `app/services/agents/assembly.py:76` | `build_chat_model, load_model_config` |
| `app/services/agents/test_runner.py:36` | `build_chat_model, load_model_config` |
| `app/services/agents/bootstrap.py:51` | `compute_model_config_hash` |
| `app/services/agents/runtime.py:76` | `compute_model_config_hash, parse_model_ref` |
| `app/api/v1/providers.py:63` | 多个 |

### 1.5 `app/services/llm/service.py` 不承担此职责（避免误解）

`LLMService`（372 行）是**调用**服务，与 provider 解析无关：

- 职责：`call(messages, model_name, response_format, **kwargs)` + tenacity 重试 + 环形 fallback
- 模型来源：`LLMRegistry`（`registry.py:52`），在 **import 期**用 `settings.LLM_MODELS` + `settings.OPENAI_API_KEY` 静态构建（`registry.py:16, 31-35`）
- **零** `Provider` / `ModelConfig` 知识：无 session、无 `load_model_config`、无 `build_chat_model`
- `async` 且返回 message，不是 client

即：仓库里存在**第三条**模型解析路径（settings 驱动），与 provider DB 路径并行。本 spec 不合并它（见 §6 范围外），但需登记其存在。

## 2. 目标分层

```
app/api/v1/providers.py        仅 HTTP 关注点：路由/限流/鉴权/信封/状态码映射
        │
        ▼
app/services/llm/provider_service.py   ← 新建：唯一 provider 业务出口
        │                                 - 查询/写入/引用完整性/客户端构建
        │                                 - 两种 session 形态（见 §3.2）
        ▼
app/services/llm/llm_store.py  纯数据访问：ORM 查询 + 行映射，不含业务策略
        │
        ▼
app/models/provider.py         ORM 模型
```

**判定标准**：`providers.py` 内不得再出现 `select(` / `db.exec`；`llm_store` 不得再被 `app/api/*` 或 `app/workflow/*` 直接 import。

## 3. 方案

### 3.1 `provider_service.py` 接口

按现有调用方的真实需求归并，不臆造：

```python
# --- 查询 ---
def get_provider(session, name) -> Provider | None
def list_providers(session) -> list[Provider]
def list_providers_page(session, *, page, page_size, keyword) -> PageResult[ProviderRowWithMeta]
def list_models(session, provider_name) -> list[ModelConfig]
def build_model_catalog(session) -> dict[str, tuple[Provider, ModelConfig]]

# --- 解析（跨模块唯一出口）---
def resolve_provider_model(session, reference) -> tuple[Provider, ModelConfig]
def resolve_chat_model(session, reference, overrides=None) -> ChatOpenAI
def resolve_chat_model_detached(reference, overrides=None) -> ChatOpenAI   # 自持 session，见 §3.2
def validate_reference(session, reference) -> None                          # S6 注册期校验

# --- 引用完整性 ---
def find_referencing_owners(session, ref) -> list[str]   # 修 §1.3：含工作流定义

# --- 写入 ---
def create_provider / update_provider / delete_provider
def create_model / update_model / delete_model
def hard_delete_provider / list_deleted_* / get_deleted_*

# --- 健康与探测 ---
def record_health(session, provider, snapshot) -> None
def test_connection(session, provider_name) -> ConnectionTestResult
```

`_mask_api_key` / `_iso` / `_provider_read` / `_health_read` / `_model_read` / `_provider_trash_read` 这 6 个**纯投影函数**（不含 ORM）迁到 `app/schemas/providers.py` 或新建 `provider_projection.py`——它们是 DTO 装配，属 schema 层职责。

### 3.2 两种 session 形态（关键设计点）

调用方分两类，**不能一刀切**：

| 场景 | 形态 | 理由 |
| --- | --- | --- |
| request-scoped（API 端点、agents 装配） | `f(session, ...)` 显式传 session | 一个请求内多次解析共用同一 session/事务；`assembly.py:471` 的 `resolve_model` 闭包依赖外层 session |
| 无 request scope（工作流节点调用期，在 `run_in_threadpool` 深处） | `*_detached(...)` 自持 session | 拿不到 `Depends(get_db_session)`（`agent_assets_common.py:37`） |

`*_detached` 版本内部用私有 `_session()` contextmanager 基于 `database_service.engine`，且**只在无 request scope 时使用**。若强行让所有调用方走 detached，会变成「每模型一次新 session」，是性能与事务语义的倒退——这是本周期 Stage C 选择「窄门面」而非全面迁移的原因。

### 3.3 引用完整性修复（§1.3 的解）

`find_referencing_owners` 统一扫描全部引用方：

1. `AgentApp.model`
2. `SubAgentConfig.model`
3. **已注册工作流定义**中每个 llm 节点的 `config.provider_ref` —— 经 `app.state.workflow_registry` 或扫描 `config/user/*.yaml`
4. 未来新增引用方只需在此单点登记

provider/model 删除端点改为调用它，命中即 422 并列出受影响清单。

## 4. 任务拆分（TDD，逐卡可独立提交）

| 卡 | 内容 | 门禁 |
| --- | --- | --- |
| TC1 | 建 `provider_service.py` 骨架 + 迁移 5 个查询函数（`providers.py` 私有 helper → service），`providers.py` 改为委托 | 端点测试全绿；`grep select\( providers.py` 仅剩 0 处 |
| TC2 | 迁移 6 个投影函数到 schema 层 | 响应字段逐字不变（快照测试） |
| TC3 | 迁移写入路径（create/update/delete provider + model）、软删/硬删/回收站 | `test_providers_api.py` + `test_providers_hard_delete.py` 全绿 |
| TC4 | 迁移健康记录与 `test_connection` | 现有健康端点测试全绿 |
| TC5 | 迁移 4 个 agents 调用方（`assembly`/`test_runner`/`bootstrap`/`runtime`）到 service（request-scoped 形态） | agents 装配测试全绿；`grep llm_store app/services/agents/` 零匹配 |
| TC6 | **修引用完整性**：`find_referencing_owners` 纳入工作流定义；删除端点接上 | 新增测试：删被工作流引用的 provider → 422 且消息含 workflow_id |
| TC7 | `llm_store.py` 收窄为纯数据访问；`workflow-llm-provider-integration` 的窄门面并入 service 后删除 | 分层守卫测试（见 §5）全绿 |
| TC8 | 文档：`docs/frontend-development-guide.md` / `AGENTS.md` 补 provider 分层约定；更新本 spec 状态 | — |

依赖顺序：TC1 → TC2/TC3/TC4（可并行）→ TC5 → TC6 → TC7 → TC8。

## 5. 验收门限（DoD Gate）

- [ ] `grep -nE "select\(|\.exec\(" app/api/v1/providers.py` → **零匹配**
- [ ] `grep -rn "llm_store" app/api/ app/workflow/ app/services/agents/` → **零匹配**（仅 `provider_service.py` 可 import）
- [ ] 分层守卫测试：源码扫描断言 `app/api/v1/providers.py` 不 import `sqlmodel.select`；`app/workflow/*` 除既有入口层例外外不 import `app.services.*`
- [ ] **引用完整性**：删除被工作流引用的 provider → 422，消息列出受影响 workflow_id
- [ ] provider CRUD / 回收站 / 健康 / 发现 / agents 装配 / 工作流执行 全部既有测试零回归
- [ ] `make lint` / `make typecheck` / `uv run pytest -m unit` 全绿；覆盖率不降
- [ ] 响应 DTO 字段**逐字不变**（前端零改动）
- [ ] 每卡独立提交，conventional commits

## 6. 范围外（明确不做）

- **不合并第三条路径**：`LLMService` / `LLMRegistry`（settings 驱动）与 provider DB 路径的合一是更大的决策，涉及 `settings.LLM_MODELS` 的存废与 fallback 语义，需独立评审。本 spec 只登记其存在（§1.5）。
- **不改 provider DTO / 端点契约**：前端零感知。
- **不改 `discovery.py`**：`discover_remote_models(provider)` 是无状态外部调用，已隔离良好；仅在 TC4 顺带把「取 provider」的动作换成 service 调用。
- **不引入 repository 模式 / 泛型 base service**：现有 `*_store.py` 命名约定够用，避免过度抽象。

## 7. 风险

| 风险 | 缓解 |
| --- | --- |
| `providers.py` 1156 行大改，回归面广 | 严格按 TC 卡分批，每卡跑全量 provider 测试；TC1-TC4 保持行为逐字不变（纯搬迁，不改逻辑） |
| session 形态选错导致事务语义变化 | §3.2 明确两类形态；TC5 迁移 agents 时保持 request-scoped，`assembly.py` 的 `resolve_model` 闭包继续复用外层 session |
| TC6 扫工作流定义可能引入 `app.api` → `app.workflow` 反向依赖 | 经 `provider_service` 注入的「引用扫描器」列表实现，或由组合根注册扫描器，避免 service 直接 import workflow |
| 与 `workflow-llm-provider-integration` 的窄门面产生重叠 | TC7 显式负责收编并删除窄门面，避免两套并存 |
