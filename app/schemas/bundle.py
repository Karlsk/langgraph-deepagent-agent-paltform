"""Bundle export/import schemas for cross-environment configuration migration.

Supports 5 entity types: providers (with model configs), skills, subagents,
agent apps, and MCP server configs. Entities are serialized as a versioned
JSON file with sensitive fields excluded.
"""

from typing import Literal

from pydantic import BaseModel, Field

VALID_ENTITY_TYPES: tuple[str, ...] = ("providers", "skills", "subagents", "apps", "mcps")


# ---------------------------------------------------------------------------
# Catalog: list available entities per type
# ---------------------------------------------------------------------------


class CatalogItem(BaseModel):
    """One entry in the catalog listing."""

    name: str
    description: str | None = None


class CatalogResponse(BaseModel):
    """Catalog of available entities grouped by type."""

    providers: list[CatalogItem] = Field(default_factory=list)
    skills: list[CatalogItem] = Field(default_factory=list)
    subagents: list[CatalogItem] = Field(default_factory=list)
    apps: list[CatalogItem] = Field(default_factory=list)
    mcps: list[CatalogItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Export request / bundle file format
# ---------------------------------------------------------------------------


class BundleExportRequest(BaseModel):
    """Export selection: each field accepts ``"*"`` (all) or a list of names.

    Omitting or setting to ``None`` means do not export that entity type.
    """

    providers: str | list[str] | None = None
    skills: str | list[str] | None = None
    subagents: str | list[str] | None = None
    apps: str | list[str] | None = None
    mcps: str | list[str] | None = None


class ModelConfigExport(BaseModel):
    """Exported representation of a single ModelConfig row."""

    name: str
    model_id: str
    context_size: int | None = None
    extra_params: dict = Field(default_factory=dict)
    enabled: bool = True


class ProviderExport(BaseModel):
    """Exported representation of a Provider row (auth_config excluded)."""

    name: str
    type: str
    base_url: str = ""
    enabled: bool = True
    models: list[ModelConfigExport] = Field(default_factory=list)


class SkillExport(BaseModel):
    """Exported representation of a SkillAsset row."""

    name: str
    description: str
    body: str | None = None
    scope: str = "global"


class SubAgentExport(BaseModel):
    """Exported representation of a SubAgentConfig row."""

    name: str
    description: str
    when_to_use: str
    system_prompt: str
    allowed_tools: list[str] | None = None
    model: str | None = None
    max_turns: int | None = None
    skill_names: list[str] | None = None


class AppExport(BaseModel):
    """Exported representation of an AgentApp row."""

    name: str
    system_prompt: str
    allowed_tools: list[str] | None = None
    model: str | None = None
    skill_names: list[str] = Field(default_factory=list)
    subagent_names: list[str] = Field(default_factory=list)
    interrupt_on: dict = Field(default_factory=dict)
    context_size: int | None = None
    engine: str = "deepagents"


class McpExport(BaseModel):
    """Exported representation of an McpServerConfig row (env excluded)."""

    name: str
    transport: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    headers: dict = Field(default_factory=dict)
    enabled: bool = True
    description: str = ""


class BundleFile(BaseModel):
    """Top-level bundle file format for export/import."""

    version: str = "1.0"
    exported_at: str
    entities: dict[str, list]


# ---------------------------------------------------------------------------
# Import preview
# ---------------------------------------------------------------------------


class PreviewItem(BaseModel):
    """One entry in the import preview."""

    name: str
    action: Literal["create", "skip"]
    reason: str | None = None


class PreviewResponse(BaseModel):
    """Preview of what the import would do, grouped by entity type."""

    providers: list[PreviewItem] = Field(default_factory=list)
    skills: list[PreviewItem] = Field(default_factory=list)
    subagents: list[PreviewItem] = Field(default_factory=list)
    apps: list[PreviewItem] = Field(default_factory=list)
    mcps: list[PreviewItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Import execution
# ---------------------------------------------------------------------------


class BundleImportRequest(BaseModel):
    """Import request: bundle payload + per-type selection filters."""

    bundle: BundleFile
    providers: str | list[str] | None = None
    skills: str | list[str] | None = None
    subagents: str | list[str] | None = None
    apps: str | list[str] | None = None
    mcps: str | list[str] | None = None


class ImportResultItem(BaseModel):
    """Result for one entity after import execution."""

    name: str
    status: Literal["created", "skipped", "error"]
    message: str | None = None


class ImportResponse(BaseModel):
    """Summary of import results grouped by entity type."""

    providers: list[ImportResultItem] = Field(default_factory=list)
    skills: list[ImportResultItem] = Field(default_factory=list)
    subagents: list[ImportResultItem] = Field(default_factory=list)
    apps: list[ImportResultItem] = Field(default_factory=list)
    mcps: list[ImportResultItem] = Field(default_factory=list)
