"""API v1 router configuration.

This module sets up the main API router and includes all sub-routers for
business endpoints.

Phase 1 G1: the legacy ``/chatbot/*`` router and ``/auth/session*`` chat-session
endpoints were retired together with the chatbot runtime (see
``docs/authentication.md``). They are no longer registered here, so clients
calling those paths receive a 404 response.
"""

from fastapi import APIRouter

from app.api.v1.apps import router as apps_router
from app.api.v1.auth import router as auth_router
from app.api.v1.mcp_servers import router as mcp_servers_router
from app.api.v1.providers import router as providers_router
from app.api.v1.skills import router as skills_router
from app.api.v1.subagents import router as subagents_router
from app.core.logging import logger
from app.workflow.api import router as workflow_router

api_router = APIRouter()

# Include routers
api_router.include_router(auth_router, prefix="/auth", tags=["Auth"])
api_router.include_router(subagents_router, tags=["SubAgents"])
api_router.include_router(skills_router, tags=["Skills"])
api_router.include_router(apps_router, tags=["Agent Apps"])
api_router.include_router(mcp_servers_router, tags=["MCP Servers"])
api_router.include_router(providers_router, tags=["Providers"])
api_router.include_router(workflow_router, tags=["Workflow"])


@api_router.get("/health")
async def health_check():
    """Health check endpoint.

    Returns:
        dict: Health status information.
    """
    logger.info("health_check_called")
    return {"status": "healthy", "version": "1.0.0"}
