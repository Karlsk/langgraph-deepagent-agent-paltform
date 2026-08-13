"""Shared async PostgreSQL connection pool provider for LangGraph checkpointing.

Every consumer (agent graph checkpointer, agent-app assembly, chat-history
cleanup) shares a single process-level ``AsyncConnectionPool`` instance.
The provider logic:

- The pool URL/size are derived from environment-specific settings.
- The pool is created lazily on first access and opened with ``autocommit``.
- On creation failure the exception is re-raised outside production, while in
  production the app degrades gracefully and returns ``None`` (the caller
  decides how to proceed without a checkpointer).
- Log event names are unchanged: ``connection_pool_created``,
  ``connection_pool_creation_failed``, ``continuing_without_connection_pool``.
"""

from typing import Optional
from urllib.parse import quote_plus

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import (
    Environment,
    settings,
)
from app.core.logging import logger

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]

# Module-level singleton; ``None`` until first successful creation. Failures
# are never cached so a later call can retry (same semantics as before).
_shared_connection_pool: Optional[PostgresConnPool] = None


async def get_shared_connection_pool() -> Optional[PostgresConnPool]:
    """Get the shared PostgreSQL connection pool using environment-specific settings.

    Returns:
        AsyncConnectionPool or None when the pool fails to initialise in
        production (the app keeps running in a degraded mode).
    """
    global _shared_connection_pool
    if _shared_connection_pool is None:
        try:
            # Configure pool size based on environment
            max_size = settings.POSTGRES_POOL_SIZE

            connection_url = (
                "postgresql://"
                f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
                f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
            )

            _shared_connection_pool = AsyncConnectionPool(
                connection_url,
                open=False,
                max_size=max_size,
                kwargs={
                    "autocommit": True,
                    "connect_timeout": 5,
                    "prepare_threshold": None,
                    "row_factory": dict_row,
                },
            )
            await _shared_connection_pool.open()
            logger.info("connection_pool_created", max_size=max_size, environment=settings.ENVIRONMENT.value)
        except Exception as e:
            logger.error("connection_pool_creation_failed", error=str(e), environment=settings.ENVIRONMENT.value)
            # In production, we might want to degrade gracefully
            if settings.ENVIRONMENT == Environment.PRODUCTION:
                logger.warning("continuing_without_connection_pool", environment=settings.ENVIRONMENT.value)
                return None
            raise e
    return _shared_connection_pool
