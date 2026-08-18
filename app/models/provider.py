"""LLM provider, model config and provider health models.

These tables replace the retired ``llm_config`` asset: a ``Provider`` holds
the connection endpoint and auth material, a ``ModelConfig`` row describes
one model offered by that provider, and ``ProviderHealth`` stores the latest
on-demand connectivity probe result (high-frequency writes stay out of the
provider row). Agent asset ``model`` fields reference the pair as
``"<provider name>/<model name>"`` (NULL resolves to ``default/default``).

Soft-delete decision: provider and model rows are never physically deleted
(``deleted`` flag + cascade on provider removal) so health history and audit
trails survive; the health row is dropped together with its provider.
"""

from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field

from app.models.base import BaseModel

DEFAULT_PROVIDER_NAME: str = "default"
DEFAULT_MODEL_NAME: str = "default"
DEFAULT_MODEL_REF: str = f"{DEFAULT_PROVIDER_NAME}/{DEFAULT_MODEL_NAME}"

PROVIDER_TYPES = ("OPENAI", "ANTHROPIC", "OLLAMA", "OPENAI_COMPATIBLE")
HEALTH_STATUSES = ("UP", "DOWN", "DEGRADED", "UNKNOWN")


def _utcnow() -> datetime:
    """Timezone-aware current time used for updated_at defaults/onupdate."""
    return datetime.now(UTC)


class Provider(BaseModel, table=True):
    """Model provider connection configuration (soft-delete).

    Attributes:
        id: Primary key
        name: Globally unique provider name (immutable at the API layer)
        type: Provider family (OPENAI|ANTHROPIC|OLLAMA|OPENAI_COMPATIBLE)
        base_url: API base endpoint (empty string = SDK env fallback)
        auth_config: Auth material shaped by ``type`` (at least ``api_key``
        for non-OLLAMA providers; plaintext by product decision, never
        echoed back by the API layer)
        enabled: Whether models of this provider may be resolved at runtime
        deleted: Soft-delete marker (queries filter it out)
        created_by: Audit-only creator identifier
        updated_at: Last modification timestamp (auto-refreshed on update)
    """

    __tablename__ = "provider"  # pyright: ignore[reportAssignmentType]

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    type: str
    base_url: str = Field(default="")
    auth_config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    deleted: bool = Field(default=False)
    created_by: Optional[str] = Field(default=None)
    updated_at: datetime = Field(default_factory=_utcnow, sa_column_kwargs={"onupdate": _utcnow})


class ModelConfig(BaseModel, table=True):
    """One model offered by a provider (soft-delete).

    Attributes:
        id: Primary key
        provider_id: Owning provider id (no FK constraint at the DB layer,
        matching the project convention for cross-asset references)
        name: Display name, unique within the provider
        model_id: Identifier sent to the provider API on invocation
        context_size: Optional context window size in tokens
        extra_params: Model-level extra parameters (e.g. temperature,
        max_tokens) forwarded to the chat model builder
        enabled: Whether this model may be resolved at runtime
        deleted: Soft-delete marker (queries filter it out)
        created_by: Audit-only creator identifier
        updated_at: Last modification timestamp (auto-refreshed on update)
    """

    __tablename__ = "model_config"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("provider_id", "name", name="uq_model_config_provider_name"),
        UniqueConstraint("provider_id", "model_id", name="uq_model_config_provider_model_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    provider_id: int = Field(index=True)
    name: str
    model_id: str
    context_size: Optional[int] = Field(default=None)
    extra_params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    enabled: bool = Field(default=True)
    deleted: bool = Field(default=False)
    created_by: Optional[str] = Field(default=None)
    updated_at: datetime = Field(default_factory=_utcnow, sa_column_kwargs={"onupdate": _utcnow})


class ProviderHealth(BaseModel, table=True):
    """Latest on-demand connectivity probe result for one provider.

    Attributes:
        id: Primary key
        provider_id: Owning provider id (unique: one health row per provider)
        status: Probe outcome (UP|DOWN|DEGRADED|UNKNOWN)
        last_check_at: Timestamp of the most recent probe (any outcome)
        last_success_at: Timestamp of the most recent successful probe
        fail_count: Consecutive failure counter (reset on success)
        latency_ms: Round-trip latency of the most recent probe
        error_message: Last failure reason (truncated to 500 chars)
        updated_at: Last write timestamp (auto-refreshed on update)
    """

    __tablename__ = "provider_health"  # pyright: ignore[reportAssignmentType]

    id: Optional[int] = Field(default=None, primary_key=True)
    provider_id: int = Field(index=True, unique=True)
    status: str = Field(default="UNKNOWN")
    last_check_at: Optional[datetime] = Field(default=None)
    last_success_at: Optional[datetime] = Field(default=None)
    fail_count: int = Field(default=0)
    latency_ms: Optional[int] = Field(default=None)
    error_message: Optional[str] = Field(default=None, max_length=500)
    updated_at: datetime = Field(default_factory=_utcnow, sa_column_kwargs={"onupdate": _utcnow})
