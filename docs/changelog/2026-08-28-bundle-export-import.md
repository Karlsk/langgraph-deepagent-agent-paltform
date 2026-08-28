## Bundle JSON 统一格式

导出的 JSON 文件格式如下：

```json
{
  "version": "1.0",
  "exported_at": "2026-08-28T12:00:00Z",
  "entities": {
    "providers": [
      {
        "name": "openai",
        "type": "OPENAI_COMPATIBLE",
        "base_url": "https://api.openai.com/v1",
        "models": [
          {
            "name": "gpt-4o",
            "model_id": "gpt-4o",
            "context_size": 128000,
            "max_output_tokens": 16384,
            "enabled": true
          }
        ]
      }
    ],
    "skills": [
      {
        "name": "pdf-export",
        "description": "Export documents to PDF",
        "body": "# Skill content here...",
        "scope": "global"
      }
    ],
    "subagents": [
      {
        "name": "researcher",
        "description": "Research assistant",
        "when_to_use": "When user needs research",
        "system_prompt": "You are a researcher...",
        "allowed_tools": ["web_search", "read_file"],
        "model": null,
        "max_turns": 10,
        "skill_names": ["pdf-export"]
      }
    ],
    "apps": [
      {
        "name": "my-agent",
        "system_prompt": "You are a helpful assistant",
        "allowed_tools": ["web_search"],
        "model": null,
        "skill_names": ["pdf-export"],
        "subagent_names": ["researcher"],
        "interrupt_on": {},
        "knowledge_base_ids": []
      }
    ],
    "mcps": [
      {
        "name": "echo-server",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "mcp_echo"],
        "tools": ["echo", "reverse"]
      }
    ]
  }
}
```

**字段规则：**
- `version`: 固定 `"1.0"`，用于未来格式升级
- `exported_at`: ISO 8601 时间戳
- `entities`: 包含 5 个 key（providers/skills/subagents/apps/mcps），每个是数组
- **排除字段**: Provider 的 `auth_config`；MCP 的 `env`
- **包含关系**: Provider 导出时附带其 `models`（ModelConfig 列表）
- **引用关系**: SubAgent 通过 `skill_names` 引用 Skill；App 通过 `skill_names` + `subagent_names` 引用

---

## 后端新增文件

| 文件 | 用途 |
|------|------|
| `app/schemas/bundle.py` | Bundle JSON schema（Pydantic v2） |
| `app/services/bundle.py` | 导出/导入业务逻辑 |
| `app/api/v1/bundle.py` | 4 个 API 端点 |
| `tests/unit/test_bundle.py` | 单元测试 |

---

## 后端详细设计

### Schema（app/schemas/bundle.py）

```python
VALID_ENTITY_TYPES = ("providers", "skills", "subagents", "apps", "mcps")

class BundleExportRequest(BaseModel):
    """每字段支持 '*'（全选）或 ['name1']（指定条目）。省略/空 = 不导出。"""
    providers: str | list[str] | None = None
    skills: str | list[str] | None = None
    subagents: str | list[str] | None = None
    apps: str | list[str] | None = None
    mcps: str | list[str] | None = None

class BundleFile(BaseModel):
    version: str = "1.0"
    exported_at: str
    entities: dict[str, list]

class BundleImportRequest(BaseModel):
    bundle: BundleFile
    providers: str | list[str] | None = None
    skills: str | list[str] | None = None
    subagents: str | list[str] | None = None
    apps: str | list[str] | None = None
    mcps: str | list[str] | None = None

class CatalogItem(BaseModel):
    name: str
    description: str | None = None

class CatalogResponse(BaseModel):
    providers: list[CatalogItem]
    skills: list[CatalogItem]
    subagents: list[CatalogItem]
    apps: list[CatalogItem]
    mcps: list[CatalogItem]

class PreviewItem(BaseModel):
    name: str
    action: Literal["create", "skip"]
    reason: str | None = None

class PreviewResponse(BaseModel):
    providers: list[PreviewItem]
    skills: list[PreviewItem]
    subagents: list[PreviewItem]
    apps: list[PreviewItem]
    mcps: list[PreviewItem]

class ImportResultItem(BaseModel):
    name: str
    status: Literal["created", "skipped", "error"]
    message: str | None = None

class ImportResponse(BaseModel):
    providers: list[ImportResultItem]
    skills: list[ImportResultItem]
    subagents: list[ImportResultItem]
    apps: list[ImportResultItem]
    mcps: list[ImportResultItem]
```

### Service（app/services/bundle.py）

**核心函数：**

```python
async def get_catalog(db: AsyncSession) -> CatalogResponse:
    """查询 5 个实体表，返回每类的 name+description 列表。"""
    # SELECT name, description FROM provider WHERE deleted_at IS NULL
    # SELECT name, description FROM skill_asset WHERE deleted_at IS NULL
    # SELECT name, description FROM subagent_config WHERE deleted_at IS NULL
    # SELECT name, description FROM agent_app WHERE deleted_at IS NULL
    # SELECT name, description FROM mcp_server_config WHERE deleted_at IS NULL

async def export_bundle(db: AsyncSession, req: BundleExportRequest) -> BundleFile:
    """根据选择规则导出实体。"""
    # 1. 遍历 5 个字段，非空则查询对应表
    # 2. providers: 排除 auth_config，附带 model_configs
    # 3. mcps: 排除 env
    # 4. 按 name 排序保证确定性

async def preview_import(db: AsyncSession, bundle: BundleFile) -> PreviewResponse:
    """逐条标记 create/skip。"""
    # 1. 查询现有实体 name 集合
    # 2. bundle 中每条：存在 → skip，不存在 → create

async def import_bundle(
    db: AsyncSession,
    req: BundleImportRequest,
    current_user: User,
) -> ImportResponse:
    """按依赖顺序导入：providers → mcps → skills → subagents → apps。"""
    # 1. 按选择规则过滤 bundle.entities
    # 2. 按依赖顺序逐条插入
    # 3. 同名跳过，记录结果
    # 4. 新增 provider 时 auth_config 置空
    # 5. 新增 mcp 时 env 置空
```

**依赖顺序：** providers → mcps → skills → subagents → apps（subagent 可引用 skill，app 可引用 skill+subagent）

### API 路由（app/api/v1/bundle.py）

```python
router = APIRouter(prefix="/bundle", tags=["bundle"])

@router.get("/catalog", response_model=CatalogResponse)
@limiter.limit("30/minute")
async def get_bundle_catalog(
    db: AsyncSession = Depends(get_async_session),
    _session: SessionData = Depends(get_current_session),
):
    """查询可选条目目录，供前端展示。"""
    return await bundle_service.get_catalog(db)

@router.post("/export")
@limiter.limit("10/minute")
async def export_bundle(
    req: BundleExportRequest,
    db: AsyncSession = Depends(get_async_session),
    _session: SessionData = Depends(get_current_session),
):
    """导出 bundle JSON 文件。"""
    bundle = await bundle_service.export_bundle(db, req)
    content = bundle.model_dump_json(indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="bundle-{date.today()}.json"'},
    )

@router.post("/import/preview", response_model=PreviewResponse)
@limiter.limit("10/minute")
async def preview_import(
    file: UploadFile,
    db: AsyncSession = Depends(get_async_session),
    _session: SessionData = Depends(get_current_session),
):
    """上传 bundle 文件，预览导入结果。"""
    content = await file.read()
    bundle = BundleFile.model_validate_json(content)
    return await bundle_service.preview_import(db, bundle)

@router.post("/import", response_model=ImportResponse)
@limiter.limit("5/minute")
async def import_bundle(
    req: BundleImportRequest,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user),
):
    """选择性导入 bundle 内容。"""
    return await bundle_service.import_bundle(db, req, current_user)
```

### 敏感字段处理

| 实体 | 字段 | 导出时 | 导入时 |
|------|------|--------|--------|
| Provider | `auth_config` | 排除（不写入 JSON） | 置空 `{}` |
| MCP | `env` | 排除 | 置空 `{}` |

### 测试用例（tests/unit/test_bundle.py）

```
test_catalog_returns_all_entity_types
    # 验证 catalog 返回 5 个类型，每个类型有 name 字段

test_export_full_selection
    # 传入 {"skills": "*"}，验证返回所有 skill

test_export_selective
    # 传入 {"skills": ["pdf-export"]}，验证只返回指定 skill

test_export_excludes_sensitive
    # provider.auth_config 和 mcp.env 不在导出结果中

test_preview_marks_existing_as_skip
    # 现有同名实体 → action=skip

test_preview_marks_new_as_create
    # 不存在的实体 → action=create

test_import_creates_in_dependency_order
    # providers → mcps → skills → subagents → apps

test_import_skips_existing
    # 同名实体被跳过，status=skipped

test_import_creates_new
    # 新实体被创建，status=created

test_import_selective
    # 只导入指定类型
```

---

## 前端新增/修改文件

| 文件 | 用途 |
|------|------|
| `agent-web/src/api/bundle.ts` | 4 个 API 调用（catalog/export/preview/import） |
| `agent-web/src/views/bundle/BundleImportExport.vue` | 导入导出页面 |
| `agent-web/src/router/index.ts` | 新增 `/bundle` 路由 |
| `agent-web/src/App.vue` | 侧边栏新增「配置迁移」菜单项 |

---

## 前端 API 模块（agent-web/src/api/bundle.ts）

```typescript
import { get, post } from '@/utils/request'

export interface CatalogItem {
  name: string
  description?: string
}
export interface CatalogResponse {
  providers: CatalogItem[]
  skills: CatalogItem[]
  subagents: CatalogItem[]
  apps: CatalogItem[]
  mcps: CatalogItem[]
}

export interface PreviewItem {
  name: string
  action: 'create' | 'skip'
  reason?: string
}
export interface PreviewResponse {
  providers: PreviewItem[]
  skills: PreviewItem[]
  subagents: PreviewItem[]
  apps: PreviewItem[]
  mcps: PreviewItem[]
}

export interface ImportResultItem {
  name: string
  status: 'created' | 'skipped' | 'error'
  message?: string
}
export interface ImportResponse {
  providers: ImportResultItem[]
  skills: ImportResultItem[]
  subagents: ImportResultItem[]
  apps: ImportResultItem[]
  mcps: ImportResultItem[]
}

export type EntitySelector = '*' | string[]

export function getBundleCatalog() {
  return get<CatalogResponse>('/bundle/catalog')
}

export function exportBundle(selection: Record<string, EntitySelector>) {
  return post('/bundle/export', selection, { responseType: 'blob' })
}

export function previewBundleImport(file: File) {
  const fd = new FormData()
  fd.append('file', file)
  return post<PreviewResponse>('/bundle/import/preview', fd)
}

export function importBundle(
  bundle: Record<string, unknown>,
  selection: Record<string, EntitySelector>,
) {
  return post<ImportResponse>('/bundle/import', { bundle, ...selection })
}
```

---

## 前端页面设计

```
侧边栏一级菜单：「配置迁移」（index="/bundle"）
  └── 页面内 el-tabs 二级：
        Tab 1：「导出配置」
        Tab 2：「导入配置」
```

### 导出配置 Tab

1. 页面激活时调用 `getBundleCatalog()` 加载 5 个模块条目
2. 每个模块渲染为一个 `content-card` 区块：模块名 + 全选 checkbox + 条目 checkbox-group
3. 底部「导出」按钮 → 构建请求 → 触发浏览器下载 `bundle-YYYY-MM-DD.json`

### 导入配置 Tab

1. el-upload 文件上传区（accept `.json`）
2. 上传后调用 `previewBundle(file)` → 每条 name + el-tag（create/skip）+ checkbox
3. 底部「导入」按钮 → 调用 `importBundle()` → 展示结果摘要 el-alert

---

## 验证计划

```bash
uv run pytest tests/unit/test_bundle.py -v

# 后端 API
curl GET /api/v1/bundle/catalog
curl POST /api/v1/bundle/export -d '{"skills": "*", "apps": ["my-agent"]}'
curl POST /api/v1/bundle/import/preview -F file=@bundle.json
curl POST /api/v1/bundle/import -d '{"bundle": ..., "skills": "*"}'

# 前端：打开 /bundle → 导出 Tab 勾选导出 → 导入 Tab 上传导入
```

---

## 实施步骤

1. **保存计划文档**：将本计划保存至 `docs/changelog/2026-08-28-bundle-export-import.md`
2. **后端实现**（TDD 顺序）：
   - 创建 `app/schemas/bundle.py` - Pydantic models
   - 创建 `app/services/bundle.py` - 业务逻辑
   - 创建 `app/api/v1/bundle.py` - API 路由
   - 创建 `tests/unit/test_bundle.py` - 单元测试
3. **前端实现**：
   - 创建 `agent-web/src/api/bundle.ts` - API 调用层
   - 创建 `agent-web/src/views/bundle/BundleImportExport.vue` - 页面组件
   - 修改 `agent-web/src/router/index.ts` - 添加路由
   - 修改 `agent-web/src/App.vue` - 侧边栏菜单项
4. **验证**：运行单元测试 + API 集成验证