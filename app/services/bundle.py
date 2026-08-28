"""Bundle export/import service for cross-environment configuration migration.

Supports 5 entity types with dependency-ordered import:
    providers -> mcps -> skills -> subagents -> apps

Sensitive fields (Provider.auth_config, McpServerConfig.env) are excluded
from exports and zeroed on import.
"""

import hashlib
import json as _json
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session as DBSession, col, select

from app.core.logging import logger
from app.models.agent_assets import AgentApp, McpServerConfig, SkillAsset, SubAgentConfig
from app.models.provider import ModelConfig, Provider
from app.models.user import User
from app.schemas.bundle import (
    AppExport,
    BundleExportRequest,
    BundleFile,
    BundleImportRequest,
    CatalogItem,
    CatalogResponse,
    ImportResponse,
    ImportResultItem,
    McpExport,
    ModelConfigExport,
    PreviewItem,
    PreviewResponse,
    ProviderExport,
    SkillExport,
    SubAgentExport,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Entity type -> (ORM model, description column or None, soft-delete filter)
_ENTITY_TABLE_MAP: dict[str, tuple[type[Any], str | None, list[Any]]] = {
    "providers": (Provider, "name", [col(Provider.deleted) == False]),  # noqa: E712
    "skills": (SkillAsset, "description", []),
    "subagents": (SubAgentConfig, "description", []),
    "apps": (AgentApp, "system_prompt", []),
    "mcps": (McpServerConfig, "description", []),
}

# Dependency order for import
_IMPORT_ORDER: list[str] = ["providers", "mcps", "skills", "subagents", "apps"]


def _resolve_selection(
    field_value: str | list[str] | None,
    available_names: set[str],
) -> set[str] | None:
    """Resolve a selection field to a set of names, or None (skip entirely).

    Args:
        field_value: ``"*"`` for all, a list of names, or ``None``.
        available_names: The names that actually exist in the DB.

    Returns:
        A set of resolved names, or ``None`` if the field was omitted.
    """
    if field_value is None:
        return None
    if field_value == "*":
        return available_names
    return set(field_value) & available_names


def _model_to_dict_exclude(row: Any, exclude: set[str]) -> dict[str, Any]:
    """Convert a SQLModel row to a dict, excluding specified column names."""
    return {k: v for k, v in row.__dict__.items() if k not in exclude and not k.startswith("_")}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def get_catalog(db: DBSession) -> CatalogResponse:
    """Query 5 entity tables and return name+description for each.

    Args:
        db: Request-scoped DB session.

    Returns:
        CatalogResponse with items per entity type.
    """
    result = CatalogResponse()

    # Providers (skip soft-deleted)
    providers = db.exec(select(Provider).where(Provider.deleted == False)).all()  # noqa: E712
    result.providers = [CatalogItem(name=p.name, description=p.type) for p in providers]

    # Skills
    skills = db.exec(select(SkillAsset).order_by(col(SkillAsset.name))).all()
    result.skills = [CatalogItem(name=s.name, description=s.description) for s in skills]

    # SubAgents
    subagents = db.exec(select(SubAgentConfig).order_by(col(SubAgentConfig.name))).all()
    result.subagents = [CatalogItem(name=s.name, description=s.description) for s in subagents]

    # Apps
    apps = db.exec(select(AgentApp).order_by(col(AgentApp.name))).all()
    result.apps = [CatalogItem(name=a.name, description=a.system_prompt[:80] if a.system_prompt else None) for a in apps]

    # MCPs
    mcps = db.exec(select(McpServerConfig).order_by(col(McpServerConfig.name))).all()
    result.mcps = [CatalogItem(name=m.name, description=m.description or None) for m in mcps]

    return result


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_bundle(db: DBSession, req: BundleExportRequest) -> BundleFile:
    """Export selected entities as a BundleFile.

    Args:
        db: Request-scoped DB session.
        req: Export selection request.

    Returns:
        BundleFile with the selected entities serialized.
    """
    entities: dict[str, list] = {}

    # Providers
    if req.providers is not None:
        names = req.providers
        stmt = select(Provider).where(Provider.deleted == False)  # noqa: E712
        if isinstance(names, list) and names:
            stmt = stmt.where(col(Provider.name).in_(names))
        providers = db.exec(stmt.order_by(col(Provider.name))).all()
        exports = []
        for p in providers:
            # Fetch model configs for this provider
            models = db.exec(
                select(ModelConfig)
                .where(ModelConfig.provider_id == p.id, ModelConfig.deleted == False)  # noqa: E712
                .order_by(col(ModelConfig.name))
            ).all()
            exports.append(
                ProviderExport(
                    name=p.name,
                    type=p.type,
                    base_url=p.base_url,
                    enabled=p.enabled,
                    models=[
                        ModelConfigExport(
                            name=m.name,
                            model_id=m.model_id,
                            context_size=m.context_size,
                            extra_params=m.extra_params,
                            enabled=m.enabled,
                        )
                        for m in models
                    ],
                )
            )
        entities["providers"] = [e.model_dump() for e in exports]

    # Skills
    if req.skills is not None:
        names = req.skills
        stmt = select(SkillAsset)
        if isinstance(names, list) and names:
            stmt = stmt.where(col(SkillAsset.name).in_(names))
        skills = db.exec(stmt.order_by(col(SkillAsset.name))).all()
        exports = [
            SkillExport(name=s.name, description=s.description, body=s.body, scope=s.scope)
            for s in skills
        ]
        entities["skills"] = [e.model_dump() for e in exports]

    # SubAgents
    if req.subagents is not None:
        names = req.subagents
        stmt = select(SubAgentConfig)
        if isinstance(names, list) and names:
            stmt = stmt.where(col(SubAgentConfig.name).in_(names))
        subagents = db.exec(stmt.order_by(col(SubAgentConfig.name))).all()
        exports = [
            SubAgentExport(
                name=s.name,
                description=s.description,
                when_to_use=s.when_to_use,
                system_prompt=s.system_prompt,
                allowed_tools=s.allowed_tools,
                model=s.model,
                max_turns=s.max_turns,
                skill_names=s.skill_names,
            )
            for s in subagents
        ]
        entities["subagents"] = [e.model_dump() for e in exports]

    # Apps
    if req.apps is not None:
        names = req.apps
        stmt = select(AgentApp)
        if isinstance(names, list) and names:
            stmt = stmt.where(col(AgentApp.name).in_(names))
        apps = db.exec(stmt.order_by(col(AgentApp.name))).all()
        exports = [
            AppExport(
                name=a.name,
                system_prompt=a.system_prompt,
                allowed_tools=a.allowed_tools,
                model=a.model,
                skill_names=a.skill_names or [],
                subagent_names=a.subagent_names or [],
                interrupt_on=a.interrupt_on or {},
                context_size=a.context_size,
                engine=a.engine,
            )
            for a in apps
        ]
        entities["apps"] = [e.model_dump() for e in exports]

    # MCPs (env excluded)
    if req.mcps is not None:
        names = req.mcps
        stmt = select(McpServerConfig)
        if isinstance(names, list) and names:
            stmt = stmt.where(col(McpServerConfig.name).in_(names))
        mcps = db.exec(stmt.order_by(col(McpServerConfig.name))).all()
        exports = [
            McpExport(
                name=m.name,
                transport=m.transport,
                command=m.command,
                args=m.args,
                url=m.url,
                headers=m.headers,
                enabled=m.enabled,
                description=m.description,
            )
            for m in mcps
        ]
        entities["mcps"] = [e.model_dump() for e in exports]

    logger.info(
        "bundle_export_completed",
        entity_counts={k: len(v) for k, v in entities.items()},
    )

    return BundleFile(
        version="1.0",
        exported_at=datetime.now(UTC).isoformat(),
        entities=entities,
    )


# ---------------------------------------------------------------------------
# Import preview
# ---------------------------------------------------------------------------


def preview_import(db: DBSession, bundle: BundleFile) -> PreviewResponse:
    """Preview what an import would do: mark each entity as create or skip.

    Args:
        db: Request-scoped DB session.
        bundle: The bundle file to preview.

    Returns:
        PreviewResponse with per-entity action annotations.
    """
    result = PreviewResponse()

    # Providers
    existing_providers = set(
        db.exec(select(Provider.name).where(Provider.deleted == False)).all()  # noqa: E712
    )
    for item in bundle.entities.get("providers", []):
        name = item.get("name", "")
        result.providers.append(
            PreviewItem(
                name=name,
                action="skip" if name in existing_providers else "create",
                reason="already exists" if name in existing_providers else None,
            )
        )

    # Skills
    existing_skills = set(db.exec(select(SkillAsset.name)).all())
    for item in bundle.entities.get("skills", []):
        name = item.get("name", "")
        result.skills.append(
            PreviewItem(
                name=name,
                action="skip" if name in existing_skills else "create",
                reason="already exists" if name in existing_skills else None,
            )
        )

    # SubAgents
    existing_subagents = set(db.exec(select(SubAgentConfig.name)).all())
    for item in bundle.entities.get("subagents", []):
        name = item.get("name", "")
        result.subagents.append(
            PreviewItem(
                name=name,
                action="skip" if name in existing_subagents else "create",
                reason="already exists" if name in existing_subagents else None,
            )
        )

    # Apps
    existing_apps = set(db.exec(select(AgentApp.name)).all())
    for item in bundle.entities.get("apps", []):
        name = item.get("name", "")
        result.apps.append(
            PreviewItem(
                name=name,
                action="skip" if name in existing_apps else "create",
                reason="already exists" if name in existing_apps else None,
            )
        )

    # MCPs
    existing_mcps = set(db.exec(select(McpServerConfig.name)).all())
    for item in bundle.entities.get("mcps", []):
        name = item.get("name", "")
        result.mcps.append(
            PreviewItem(
                name=name,
                action="skip" if name in existing_mcps else "create",
                reason="already exists" if name in existing_mcps else None,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Import execution
# ---------------------------------------------------------------------------


def _should_import(field_value: str | list[str] | None) -> bool:
    """Check whether the selection field indicates this type should be imported."""
    return field_value is not None


def _filter_items(items: list[dict[str, Any]], names: str | list[str] | None) -> list[dict[str, Any]]:
    """Filter bundle items by name selection."""
    if names is None:
        return []
    if names == "*":
        return items
    name_set = set(names)
    return [i for i in items if i.get("name") in name_set]


def import_bundle(
    db: DBSession,
    req: BundleImportRequest,
    current_user: User,
) -> ImportResponse:
    """Import entities from a bundle in dependency order.

    Order: providers -> mcps -> skills -> subagents -> apps.
    Existing entities (by name) are skipped.

    Args:
        db: Request-scoped DB session.
        req: Import request with bundle and optional per-type filters.
        current_user: Authenticated user for audit attribution.

    Returns:
        ImportResponse with per-entity results.
    """
    creator = current_user.username or str(current_user.id)
    result = ImportResponse()

    # --- Providers ---
    if _should_import(req.providers):
        items = _filter_items(req.bundle.entities.get("providers", []), req.providers)
        existing = set(
            db.exec(select(Provider.name).where(Provider.deleted == False)).all()  # noqa: E712
        )
        for item in items:
            name = item.get("name", "")
            if name in existing:
                result.providers.append(ImportResultItem(name=name, status="skipped", message="already exists"))
                continue
            try:
                provider = Provider(
                    name=name,
                    type=item.get("type", "OPENAI_COMPATIBLE"),
                    base_url=item.get("base_url", ""),
                    auth_config={},  # sensitive: zeroed on import
                    enabled=item.get("enabled", True),
                    created_by=creator,
                )
                db.add(provider)
                db.flush()  # get provider.id for model configs

                # Import associated model configs
                assert provider.id is not None, "Provider ID should be set after flush"
                for mc in item.get("models", []):
                    model = ModelConfig(
                        provider_id=provider.id,
                        name=mc.get("name", ""),
                        model_id=mc.get("model_id", ""),
                        context_size=mc.get("context_size"),
                        extra_params=mc.get("extra_params", {}),
                        enabled=mc.get("enabled", True),
                        created_by=creator,
                    )
                    db.add(model)

                result.providers.append(ImportResultItem(name=name, status="created"))
                logger.info("bundle_import_provider_created", name=name)
            except Exception as exc:  # noqa: BLE001
                result.providers.append(ImportResultItem(name=name, status="error", message=str(exc)))
                logger.exception("bundle_import_provider_failed", name=name)

    # --- MCPs ---
    if _should_import(req.mcps):
        items = _filter_items(req.bundle.entities.get("mcps", []), req.mcps)
        existing = set(db.exec(select(McpServerConfig.name)).all())
        for item in items:
            name = item.get("name", "")
            if name in existing:
                result.mcps.append(ImportResultItem(name=name, status="skipped", message="already exists"))
                continue
            try:
                canonical = _json.dumps(
                    {k: v for k, v in item.items() if k != "name"},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=str,
                )
                content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                mcp = McpServerConfig(
                    name=name,
                    transport=item.get("transport", "stdio"),
                    command=item.get("command"),
                    args=item.get("args", []),
                    env={},  # sensitive: zeroed on import
                    url=item.get("url"),
                    headers=item.get("headers", {}),
                    enabled=item.get("enabled", True),
                    description=item.get("description", ""),
                    content_hash=content_hash,
                    created_by=creator,
                )
                db.add(mcp)
                result.mcps.append(ImportResultItem(name=name, status="created"))
                logger.info("bundle_import_mcp_created", name=name)
            except Exception as exc:  # noqa: BLE001
                result.mcps.append(ImportResultItem(name=name, status="error", message=str(exc)))
                logger.exception("bundle_import_mcp_failed", name=name)

    # --- Skills ---
    if _should_import(req.skills):
        items = _filter_items(req.bundle.entities.get("skills", []), req.skills)
        existing = set(db.exec(select(SkillAsset.name)).all())
        for item in items:
            name = item.get("name", "")
            if name in existing:
                result.skills.append(ImportResultItem(name=name, status="skipped", message="already exists"))
                continue
            try:
                body = item.get("body") or ""
                content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
                skill = SkillAsset(
                    name=name,
                    description=item.get("description", ""),
                    body=body,
                    content_hash=content_hash,
                    created_by=creator,
                    scope=item.get("scope", "global"),
                )
                db.add(skill)
                result.skills.append(ImportResultItem(name=name, status="created"))
                logger.info("bundle_import_skill_created", name=name)
            except Exception as exc:  # noqa: BLE001
                result.skills.append(ImportResultItem(name=name, status="error", message=str(exc)))
                logger.exception("bundle_import_skill_failed", name=name)

    # --- SubAgents ---
    if _should_import(req.subagents):
        items = _filter_items(req.bundle.entities.get("subagents", []), req.subagents)
        existing = set(db.exec(select(SubAgentConfig.name)).all())
        for item in items:
            name = item.get("name", "")
            if name in existing:
                result.subagents.append(ImportResultItem(name=name, status="skipped", message="already exists"))
                continue
            try:
                canonical = _json.dumps(
                    {k: v for k, v in item.items() if k != "name"},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=str,
                )
                content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                sub = SubAgentConfig(
                    name=name,
                    description=item.get("description", ""),
                    when_to_use=item.get("when_to_use", ""),
                    system_prompt=item.get("system_prompt", ""),
                    allowed_tools=item.get("allowed_tools"),
                    model=item.get("model"),
                    max_turns=item.get("max_turns"),
                    skill_names=item.get("skill_names"),
                    content_hash=content_hash,
                    created_by=creator,
                )
                db.add(sub)
                result.subagents.append(ImportResultItem(name=name, status="created"))
                logger.info("bundle_import_subagent_created", name=name)
            except Exception as exc:  # noqa: BLE001
                result.subagents.append(ImportResultItem(name=name, status="error", message=str(exc)))
                logger.exception("bundle_import_subagent_failed", name=name)

    # --- Apps ---
    if _should_import(req.apps):
        items = _filter_items(req.bundle.entities.get("apps", []), req.apps)
        existing = set(db.exec(select(AgentApp.name)).all())
        for item in items:
            name = item.get("name", "")
            if name in existing:
                result.apps.append(ImportResultItem(name=name, status="skipped", message="already exists"))
                continue
            try:
                app = AgentApp(
                    name=name,
                    system_prompt=item.get("system_prompt", ""),
                    allowed_tools=item.get("allowed_tools"),
                    model=item.get("model"),
                    skill_names=item.get("skill_names", []),
                    subagent_names=item.get("subagent_names", []),
                    interrupt_on=item.get("interrupt_on", {}),
                    context_size=item.get("context_size"),
                    engine=item.get("engine", "deepagents"),
                    created_by=creator,
                )
                db.add(app)
                result.apps.append(ImportResultItem(name=name, status="created"))
                logger.info("bundle_import_app_created", name=name)
            except Exception as exc:  # noqa: BLE001
                result.apps.append(ImportResultItem(name=name, status="error", message=str(exc)))
                logger.exception("bundle_import_app_failed", name=name)

    db.commit()

    total_created = sum(
        1
        for items in [result.providers, result.skills, result.subagents, result.apps, result.mcps]
        for i in items
        if i.status == "created"
    )
    total_skipped = sum(
        1
        for items in [result.providers, result.skills, result.subagents, result.apps, result.mcps]
        for i in items
        if i.status == "skipped"
    )
    logger.info(
        "bundle_import_completed",
        created=total_created,
        skipped=total_skipped,
    )

    return result
