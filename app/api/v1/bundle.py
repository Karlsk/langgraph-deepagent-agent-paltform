"""Bundle export/import API for cross-environment configuration migration.

Provides 4 endpoints:
- ``GET  /bundle/catalog``       – list available entities per type
- ``POST /bundle/export``        – export selected entities as JSON
- ``POST /bundle/import/preview`` – preview import (create vs skip)
- ``POST /bundle/import``        – execute selective import

All endpoints are authenticated and rate-limited.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlmodel import Session as DBSession

from app.api.v1.agent_assets_common import get_db_session
from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.user import User
from app.schemas.base import ApiResponse
from app.schemas.bundle import (
    BundleExportRequest,
    BundleFile,
    BundleImportRequest,
    CatalogResponse,
    ImportResponse,
    PreviewResponse,
    VALID_ENTITY_TYPES,
)
from app.services import bundle as bundle_service

router = APIRouter(tags=["Bundle"])


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get("/bundle/catalog", response_model=ApiResponse[CatalogResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS.get("bundle", ["30 per minute"])[0])
async def get_bundle_catalog(
    request: Request,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[CatalogResponse]:
    """List available entities per type for the export UI.

    Args:
        request: The FastAPI request object for rate limiting.
        db: Request-scoped DB session.
        user: Authenticated user.

    Returns:
        Envelope carrying the catalog of available entities.
    """
    try:
        catalog = bundle_service.get_catalog(db)
        return ApiResponse.success(catalog)
    except Exception as exc:
        logger.exception("bundle_catalog_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.post("/bundle/export")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS.get("bundle", ["10 per minute"])[0])
async def export_bundle(
    request: Request,
    payload: BundleExportRequest,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> Response:
    """Export selected entities as a downloadable JSON bundle.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: Export selection (``"*"`` or name lists per entity type).
        db: Request-scoped DB session.
        user: Authenticated user.

    Returns:
        JSON file response with ``Content-Disposition`` header.
    """
    try:
        bundle = bundle_service.export_bundle(db, payload)
        content = bundle.model_dump_json(indent=2)
        return Response(
            content=content,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="bundle-{date.today()}.json"'
            },
        )
    except Exception as exc:
        logger.exception("bundle_export_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Import preview
# ---------------------------------------------------------------------------


@router.post("/bundle/import/preview", response_model=ApiResponse[PreviewResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS.get("bundle", ["10 per minute"])[0])
async def preview_bundle_import(
    request: Request,
    file: UploadFile,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[PreviewResponse]:
    """Upload a bundle file and preview what the import would do.

    Args:
        request: The FastAPI request object for rate limiting.
        file: Uploaded JSON bundle file.
        db: Request-scoped DB session.
        user: Authenticated user.

    Returns:
        Envelope carrying per-entity action annotations (create/skip).
    """
    try:
        content = await file.read()
        bundle = BundleFile.model_validate_json(content)
        preview = bundle_service.preview_import(db, bundle)
        return ApiResponse.success(preview)
    except Exception as exc:
        logger.exception("bundle_preview_failed")
        raise HTTPException(status_code=422, detail=f"invalid bundle file: {exc}") from exc


# ---------------------------------------------------------------------------
# Import execution
# ---------------------------------------------------------------------------


@router.post("/bundle/import", response_model=ApiResponse[ImportResponse])
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS.get("bundle", ["5 per minute"])[0])
async def import_bundle(
    request: Request,
    payload: BundleImportRequest,
    db: DBSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ApiResponse[ImportResponse]:
    """Execute a selective import from a bundle.

    Args:
        request: The FastAPI request object for rate limiting.
        payload: Import request with bundle + per-type filters.
        db: Request-scoped DB session.
        user: Authenticated user (used for audit attribution).

    Returns:
        Envelope carrying per-entity import results.
    """
    try:
        # Validate entity type keys
        for entity_type in payload.bundle.entities:
            if entity_type not in VALID_ENTITY_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown entity type '{entity_type}', expected one of {VALID_ENTITY_TYPES}",
                )

        result = bundle_service.import_bundle(db, payload, user)
        return ApiResponse.success(result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("bundle_import_failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
