"""Provider-backed model resolution facade.

Narrow by design: this module is the entry point for **cross-module callers that
have no request scope** — today that is the workflow engine, whose LLM nodes
resolve credentials at invocation time deep inside a threadpool worker, where
``Depends(get_db_session)`` is unavailable. Such callers must not open sessions
themselves nor reach into the store layer directly.

Request-scoped callers in the same layer (``app/services/agents/*``,
``app/api/v1/providers.py``) keep using ``llm_store`` with their own session so a
single request shares one session/transaction. Do not migrate them here without
revisiting that trade-off.

The broader consolidation — giving providers a real service layer and retiring
the raw ORM queries currently living in ``app/api/v1/providers.py`` — is tracked
in ``docs/changelog/provider-service-consolidation/spec-01-provider-service-layer.md``.
This facade is the seed of that service and is expected to be absorbed by its TC7.

Security note (H6): ``api_key`` never crosses this boundary as data. It is read
inside ``build_chat_model`` and wrapped in a ``SecretStr``; only provider/model
names appear in log events here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langchain_openai import ChatOpenAI
from sqlmodel import Session

from app.core.logging import logger
from app.services.database import database_service
from app.services.llm.llm_store import build_chat_model, load_model_config


@contextmanager
def _session() -> Iterator[Session]:
    """Open a short-lived session bound to the shared engine, closed on exit."""
    with Session(database_service.engine) as session:
        yield session


def resolve_chat_model(reference: str, overrides: dict[str, Any] | None = None) -> ChatOpenAI:
    """Resolve a ``"<provider>/<model>"`` reference into a ready chat client.

    The signature matches ``app.workflow.ports.ChatModelFactory`` exactly, so it
    can be injected into the workflow engine without an adapter.

    Args:
        reference: Model reference (``provider_name/model_name``).
        overrides: Node-level parameters (``temperature``, ``max_tokens``) that
            take precedence over the persisted ``ModelConfig.extra_params``
            (CONTRACT S20 — the workflow author's explicit intent wins).

    Returns:
        A newly constructed chat client.

    Raises:
        ValueError: When the reference is malformed, missing, soft-deleted or
            disabled; the message lists every available reference.
    """
    with _session() as session:
        provider, model = load_model_config(session, reference)
        logger.debug(
            "provider_chat_model_resolved",
            provider=provider.name,
            model=model.name,
            model_id=model.model_id,
            base_url=provider.base_url,
            override_keys=sorted(overrides or {}),
        )
        if not overrides:
            return build_chat_model(provider, model)
        merged = {**(model.extra_params or {}), **overrides}
        return build_chat_model(provider, model.model_copy(update={"extra_params": merged}))


def validate_reference(reference: str) -> None:
    """Assert a reference resolves, for build-time validation (CONTRACT S6/S20).

    Used by the workflow save endpoint so a dangling ``provider_ref`` is rejected
    with HTTP 422 at registration rather than failing later at execution.

    Args:
        reference: Model reference (``provider_name/model_name``).

    Raises:
        ValueError: When the reference is malformed, missing, soft-deleted or disabled.
    """
    with _session() as session:
        load_model_config(session, reference)
