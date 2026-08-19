"""On-demand upstream model discovery for a stored provider.

Wraps the AsyncOpenAI client already used by ``test_provider_connection`` so
the UI can list a provider's upstream models without leaking the stored
``auth_config.api_key`` to the browser. Only providers that expose an
OpenAI-compatible ``GET /models`` endpoint are supported (OPENAI,
OPENAI_COMPATIBLE, OLLAMA via the OpenAI-compat shim); ANTHROPIC is
rejected synchronously since it has no equivalent list endpoint.
"""

from openai import AsyncOpenAI

from app.core.config import settings
from app.models.provider import Provider
from app.schemas.providers import RemoteModelInfo

# Provider families without a list-models endpoint. ANTHROPIC exposes no
# public /models API; adding more entries requires a dedicated SDK adapter.
UNSUPPORTED_TYPES: frozenset[str] = frozenset({"ANTHROPIC"})


async def discover_remote_models(provider: Provider) -> list[RemoteModelInfo]:
    """Fetch the upstream ``/models`` list of one provider and project it.

    The constructed AsyncOpenAI client is always closed in a finally block
    so connection resources never leak on failure paths. ``auth_config``
    is read straight from the ORM row; missing/empty values fall back to the
    SDK's ``"no-key"`` placeholder (OLLAMA-style local install).

    Args:
        provider: The Provider row whose type / base_url / auth_config
            drive the upstream call.

    Returns:
        One ``RemoteModelInfo`` per upstream model id, preserving the
        upstream ordering via AsyncOpenAI's ``models.list()`` pagination.

    Raises:
        ValueError: When the provider type is in ``UNSUPPORTED_TYPES``.
        openai.OpenAIError: When the upstream call fails (the caller
            surfaces it as an HTTP 502).
    """
    if provider.type in UNSUPPORTED_TYPES:
        raise ValueError(
            f"provider type '{provider.type}' does not support auto-discovery"
        )

    api_key = provider.auth_config.get("api_key") if provider.auth_config else None
    client = AsyncOpenAI(
        api_key=api_key if isinstance(api_key, str) and api_key else "no-key",
        base_url=provider.base_url or None,
        timeout=settings.PROVIDER_TEST_TIMEOUT_SECONDS,
    )
    try:
        out: list[RemoteModelInfo] = []
        async for model in client.models.list():
            out.append(
                RemoteModelInfo(
                    id=model.id,
                    owned_by=getattr(model, "owned_by", None),
                    raw=model.model_dump(),
                )
            )
        return out
    finally:
        await client.close()