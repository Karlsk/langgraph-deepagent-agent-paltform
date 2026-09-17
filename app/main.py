"""This file contains the main application entry point."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlmodel import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from asgi_correlation_id import CorrelationIdMiddleware

from app.api.error_handlers import (
    http_exception_handler,
    rate_limit_exceeded_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.api.v1.api import api_router
from app.core.cache import cache_service
from app.core.config import settings
from app.core.langgraph.pool import get_shared_connection_pool
from app.core.limiter import limiter
from app.core.logging import logger
from app.core.metrics import setup_metrics
from app.core.middleware import (
    LoggingContextMiddleware,
    MetricsMiddleware,
    ProfilingMiddleware,
)
from app.core.observability import langfuse_init
from app.services.agents.bootstrap import ensure_all_agent_workspaces, ensure_default_agent_app
from app.services.agents.mcp_manager import ToolCatalogEntry, build_tool_catalog, get_mcp_tools, shutdown_mcp_clients
from app.core.langgraph.tools import tools as builtin_tools
from app.services.agents.workflow_bridge import set_workflow_registry
from app.services.database import database_service
from app.services.llm.provider_service import resolve_chat_model
from app.services.memory import memory_service
from app.workflow.cli import DEFAULT_CONFIG_DIR, build_registry
from app.workflow.store import backfill_workflows_from_disk

# Load environment variables
load_dotenv()
langfuse_init()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events."""
    logger.info(
        "application_startup",
        project_name=settings.PROJECT_NAME,
        version=settings.VERSION,
        api_prefix=settings.API_V1_STR,
    )

    # Initialize cache service (connects to Valkey if configured)
    try:
        await cache_service.initialize()
    except Exception as e:
        logger.exception("cache_initialization_failed", error=str(e))

    # Pre-warm mem0 AsyncMemory: initializes pgvector connection and schema check
    # so the first search() cache miss or add() doesn't pay the ~130ms cold-init cost
    try:
        await memory_service.initialize()
    except Exception as e:
        logger.exception("memory_service_pre_warm_failed", error=str(e))

    # AgentApp bootstrap (G2 v3.3 §6.1.5): default-app provisioning and the
    # MCP tool pre-warm stay (G1 contract, degrading on failure), but the
    # runtime compile pre-warm is gone — ensure_all_agent_workspaces repairs
    # the Agent layers (§5.4) and the first user request compiles per
    # (app_id, user_id).
    try:
        with Session(database_service.engine) as db_session:
            await ensure_default_agent_app(db_session)
            try:
                await get_mcp_tools(db_session)
                logger.info("mcp_tools_pre_warmed")
            except Exception as e:
                logger.exception("mcp_tools_pre_warm_failed_degraded", error=str(e))
            await ensure_all_agent_workspaces(db_session)
            try:
                backfill_workflows_from_disk(db_session)
            except Exception as e:
                logger.exception("workflow_backfill_failed", error=str(e))
    except Exception as e:
        logger.exception("agent_workspace_bootstrap_failed", error=str(e))

    yield

    # Cleanup on shutdown
    await shutdown_mcp_clients()
    # Close the shared checkpoint connection pool (old lifespan contract); the
    # pool may never have been created (lazy init / degraded mode) -> guard.
    pool = await get_shared_connection_pool()
    if pool is not None:
        await pool.close()
    await cache_service.close()
    logger.info("application_shutdown")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# Set up Prometheus metrics
setup_metrics(app)

# Add logging context middleware (must be added before other middleware to capture context)
app.add_middleware(LoggingContextMiddleware)

# Add custom metrics middleware
app.add_middleware(MetricsMiddleware)

# Add profiling middleware (DEBUG only — saves HTML to /tmp on slow requests)
if settings.DEBUG:
    app.add_middleware(ProfilingMiddleware)

# Add correlation ID middleware — must be outermost so request_id is set before all others
app.add_middleware(CorrelationIdMiddleware)

# Set up rate limiter exception handler (unified envelope output)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]

def _build_tool_resolver():
    """Build a sync tool_resolver callable for the workflow engine.

    Returns a callable ``(tool_names, mcp_server_names) -> list[BaseTool]`` that
    opens a short-lived DB session, loads builtin + MCP tools, and filters by
    the requested names/servers. Uses ``asyncio.run`` internally because MCP
    tool loading is async while the resolver is called from a sync context.
    """

    def _resolve(tool_names: list[str] | None, mcp_server_names: list[str] | None) -> list:
        with Session(database_service.engine) as session:
            catalog = asyncio.run(build_tool_catalog(session))
        tool_index: dict[str, ToolCatalogEntry] = {}
        server_tools: dict[str, list[ToolCatalogEntry]] = {}
        for entry in catalog:
            tool_index[entry["name"]] = entry
            server = entry.get("server")
            if server:
                server_tools.setdefault(server, []).append(entry)

        resolved: dict[str, ToolCatalogEntry] = {}
        if tool_names:
            for name in tool_names:
                if name in tool_index:
                    resolved[name] = tool_index[name]
        if mcp_server_names:
            for server_name in mcp_server_names:
                for entry in server_tools.get(server_name, []):
                    resolved[entry["name"]] = entry

        if not resolved:
            return []

        resolved_names = set(resolved.keys())
        with Session(database_service.engine) as session:
            mcp_tools = asyncio.run(get_mcp_tools(session))
        builtin_by_name = {t.name: t for t in builtin_tools}
        mcp_by_name = {t.name: t for t in mcp_tools}
        result = []
        for name in resolved_names:
            tool = builtin_by_name.get(name) or mcp_by_name.get(name)
            if tool is not None:
                result.append(tool)
        return result

    return _resolve


# Inject the workflow registry on app.state (spec-09 TC1, H4/G7: engine keeps no module-level cache).
# The chat model factory and tool resolver are injected here — and only here — so the engine
# can resolve provider_ref credentials and tool names without importing any host module
# (CONTRACT §3 red line 4, S20).
app.state.workflow_registry = build_registry(
    DEFAULT_CONFIG_DIR,
    chat_model_factory=resolve_chat_model,
    tool_resolver=_build_tool_resolver(),
)
logger.info("workflow_registry_built", directory=str(DEFAULT_CONFIG_DIR))

# Hand the registry to the runtime layer: get_runtime builds a WorkflowAppRuntime
# outside any request/app scope, so it reads the registry back through this
# host-level bridge (spec-01 Phase 2; the engine itself keeps no global state).
set_workflow_registry(app.state.workflow_registry)


# Add validation exception handler
app.add_exception_handler(RequestValidationError, validation_exception_handler)  # pyright: ignore[reportArgumentType]


# Envelope handlers for explicit HTTP errors and unexpected failures.
# Starlette resolves handlers by walking the raised exception's MRO class
# by class (most specific first), so the fastapi.HTTPException registration
# wins for business errors while the Starlette base-class registration is
# the fallback that catches router-level errors (unknown route 404, 405).
app.add_exception_handler(HTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # pyright: ignore[reportArgumentType]
app.add_exception_handler(Exception, unhandled_exception_handler)


# Set up CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["root"][0])
async def root(request: Request):
    """Root endpoint returning basic API information."""
    logger.info("root_endpoint_called")
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "healthy",
        "environment": settings.ENVIRONMENT.value,
        "swagger_url": "/docs",
        "redoc_url": "/redoc",
    }


@app.get("/health")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["health"][0])
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint with environment-specific information.

    Returns:
        JSONResponse: Health status payload, with HTTP 503 when the
        database is unreachable so load balancers can drop the instance.
    """
    logger.info("health_check_called")

    # Check database connectivity
    db_healthy = await database_service.health_check()

    response = {
        "status": "healthy" if db_healthy else "degraded",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT.value,
        "components": {"api": "healthy", "database": "healthy" if db_healthy else "unhealthy"},
        "timestamp": datetime.now().isoformat(),
    }

    # If DB is unhealthy, set the appropriate status code
    status_code = status.HTTP_200_OK if db_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(content=response, status_code=status_code)
