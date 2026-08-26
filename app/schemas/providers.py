"""Pydantic schemas for provider and model config endpoints.

The read projections physically exclude ``auth_config`` secrets: only the
masked form ``api_key_masked`` (``****`` + last four characters) is ever
returned, mirroring the retired llm-config masking contract.
"""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator
from pydantic.fields import FieldInfo

from app.schemas.agent_apps import NAME_MAX_LENGTH, _name_field

MODEL_NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]*$"


def _model_name_field(description: str) -> FieldInfo:
    """Build the identifier field for model names (allows dots in version strings)."""
    return Field(
        ...,
        description=description,
        pattern=MODEL_NAME_PATTERN,
        max_length=NAME_MAX_LENGTH,
    )


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class ProviderCreate(BaseModel):
    """Request model for creating a model provider.

    Attributes:
        name: Globally unique provider name (immutable after creation)
        type: Provider family determining the auth_config shape
        base_url: API base endpoint (empty string = SDK env fallback)
        auth_config: Auth material (must carry ``api_key`` unless OLLAMA)
        enabled: Whether models of this provider may be resolved at runtime
    """

    name: str = _name_field("Globally unique provider name")  # pyright: ignore[reportAssignmentType]
    type: Literal["OPENAI", "ANTHROPIC", "OLLAMA", "OPENAI_COMPATIBLE"] = Field(
        ..., description="Provider family determining the auth_config shape"
    )
    base_url: str = Field(default="", max_length=500, description="API base endpoint (empty = SDK env fallback)")
    auth_config: dict = Field(default_factory=dict, description="Auth material shaped by type")
    enabled: bool = Field(default=True, description="Whether this provider may be resolved at runtime")

    @model_validator(mode="after")
    def require_api_key(self) -> "ProviderCreate":
        """Reject non-OLLAMA providers whose auth_config lacks an api_key."""
        api_key = self.auth_config.get("api_key")
        if self.type != "OLLAMA" and not (isinstance(api_key, str) and api_key):
            raise ValueError("auth_config.api_key is required for non-OLLAMA providers")
        return self


class ProviderUpdate(BaseModel):
    """Partial update model for a provider (PATCH semantics; name is immutable).

    ``auth_config`` omitted = the stored credentials are kept unchanged.

    Attributes:
        type: Updated provider family
        base_url: Updated API base endpoint
        auth_config: Replacement auth material (omit to keep the stored one)
        enabled: Updated active flag
    """

    type: Optional[Literal["OPENAI", "ANTHROPIC", "OLLAMA", "OPENAI_COMPATIBLE"]] = Field(
        default=None, description="Updated provider family"
    )
    base_url: Optional[str] = Field(default=None, max_length=500, description="Updated API base endpoint")
    auth_config: Optional[dict] = Field(
        default=None, description="Replacement auth material (omit to keep the stored one)"
    )
    enabled: Optional[bool] = Field(default=None, description="Updated active flag")

    @model_validator(mode="after")
    def require_api_key(self) -> "ProviderUpdate":
        """Reject an explicit auth_config without api_key unless OLLAMA."""
        if self.auth_config is None:
            return self
        target_type = self.type or "OLLAMA"
        # Without a type in the payload the endpoint re-validates against the
        # stored type; here only an explicit OPENAI-family switch is checked.
        api_key = self.auth_config.get("api_key")
        if target_type != "OLLAMA" and self.type is not None and not (isinstance(api_key, str) and api_key):
            raise ValueError("auth_config.api_key is required for non-OLLAMA providers")
        return self


class ProviderRead(BaseModel):
    """Response model for a provider (auth secrets physically excluded).

    Attributes:
        id: Provider primary key
        name: Globally unique provider name
        type: Provider family
        base_url: API base endpoint (empty string = SDK env fallback)
        api_key_masked: Masked form of the stored API key (empty when absent)
        enabled: Whether this provider may be resolved at runtime
        created_by: Audit-only creator identifier
        created_at: Creation timestamp
        updated_at: Last modification timestamp
    """

    id: int = Field(..., description="Provider primary key")
    name: str = Field(..., description="Globally unique provider name")
    type: str = Field(..., description="Provider family")
    base_url: str = Field(default="", description="API base endpoint")
    api_key_masked: str = Field(..., description="Masked form of the stored API key")
    enabled: bool = Field(..., description="Whether this provider may be resolved at runtime")
    created_by: Optional[str] = Field(default=None, description="Audit-only creator identifier")
    created_at: Optional[str] = Field(default=None, description="Creation timestamp")
    updated_at: Optional[str] = Field(default=None, description="Last modification timestamp")


class ProviderHealthRead(BaseModel):
    """Latest on-demand connectivity probe result for one provider.

    Attributes:
        status: Probe outcome (UP|DOWN|DEGRADED|UNKNOWN)
        last_check_at: Timestamp of the most recent probe (any outcome)
        last_success_at: Timestamp of the most recent successful probe
        fail_count: Consecutive failure counter (reset on success)
        latency_ms: Round-trip latency of the most recent probe
        error_message: Last failure reason (truncated to 500 chars)
    """

    status: str = Field(default="UNKNOWN", description="Probe outcome")
    last_check_at: Optional[str] = Field(default=None, description="Most recent probe timestamp")
    last_success_at: Optional[str] = Field(default=None, description="Most recent successful probe timestamp")
    fail_count: int = Field(default=0, description="Consecutive failure counter")
    latency_ms: Optional[int] = Field(default=None, description="Round-trip latency of the most recent probe")
    error_message: Optional[str] = Field(default=None, description="Last failure reason")


class ProviderRowWithMeta(BaseModel):
    """Provider list row enriched with model count and health snapshot.

    Attributes:
        provider: The masked provider projection
        model_count: Enabled, non-deleted model configs under this provider
        health: Latest probe result (UNKNOWN defaults when never probed)
    """

    provider: ProviderRead = Field(..., description="Masked provider projection")
    model_count: int = Field(..., description="Enabled, non-deleted model configs under this provider")
    health: ProviderHealthRead = Field(..., description="Latest probe result")


class ConnectionTestResult(BaseModel):
    """Outcome of an on-demand provider connectivity probe.

    Attributes:
        status: Probe outcome (UP|DOWN|DEGRADED)
        latency_ms: Round-trip latency of the probe (None on failure)
        error_message: Failure reason (None on success)
    """

    status: str = Field(..., description="Probe outcome")
    latency_ms: Optional[int] = Field(default=None, description="Round-trip latency of the probe")
    error_message: Optional[str] = Field(default=None, description="Failure reason (None on success)")


class RemoteModelInfo(BaseModel):
    """Single entry of an upstream ``GET /models`` listing (auth-free projection).

    Returned by the on-demand discover endpoint so the UI can offer one
    selection per model id without echoing auth secrets back.

    Attributes:
        id: Upstream model identifier (used verbatim as the local model_id)
        owned_by: Upstream owner tag when present (informational only)
        raw: Full upstream payload as a dict for future extensibility
    """

    id: str = Field(..., description="Upstream model identifier (used verbatim as the local model_id)")
    owned_by: Optional[str] = Field(default=None, description="Upstream owner tag (informational only)")
    raw: dict[str, Any] = Field(default_factory=dict, description="Full upstream payload as a dict")


# ---------------------------------------------------------------------------
# Model configs
# ---------------------------------------------------------------------------


class ModelConfigCreate(BaseModel):
    """Request model for creating a model config under a provider.

    Attributes:
        name: Display name, unique within the provider (immutable)
        model_id: Identifier sent to the provider API on invocation
        context_size: Optional context window size in tokens
        extra_params: Model-level extra parameters (temperature, max_tokens)
        enabled: Whether this model may be resolved at runtime
    """

    name: str = _model_name_field("Model display name (unique within the provider)")  # pyright: ignore[reportAssignmentType]
    model_id: str = Field(..., min_length=1, max_length=128, description="Identifier sent to the provider API")
    context_size: Optional[int] = Field(default=None, ge=1, description="Context window size in tokens")
    extra_params: dict = Field(default_factory=dict, description="Model-level extra parameters")
    enabled: bool = Field(default=True, description="Whether this model may be resolved at runtime")


class ModelConfigUpdate(BaseModel):
    """Partial update model for a model config (PATCH semantics; name is immutable).

    Attributes:
        model_id: Updated identifier sent to the provider API
        context_size: Updated context window size in tokens
        extra_params: Replacement extra parameters
        enabled: Updated active flag
    """

    model_id: Optional[str] = Field(
        default=None, min_length=1, max_length=128, description="Updated identifier sent to the provider API"
    )
    context_size: Optional[int] = Field(default=None, ge=1, description="Updated context window size in tokens")
    extra_params: Optional[dict] = Field(default=None, description="Replacement extra parameters")
    enabled: Optional[bool] = Field(default=None, description="Updated active flag")


class ModelConfigRead(BaseModel):
    """Response model for a model config.

    Attributes:
        id: Model config primary key
        provider_name: Owning provider name
        name: Display name, unique within the provider
        model_id: Identifier sent to the provider API on invocation
        ref: Asset reference string (``provider_name/name``)
        context_size: Context window size in tokens
        extra_params: Model-level extra parameters
        enabled: Whether this model may be resolved at runtime
        created_by: Audit-only creator identifier
        created_at: Creation timestamp
        updated_at: Last modification timestamp
    """

    id: int = Field(..., description="Model config primary key")
    provider_name: str = Field(..., description="Owning provider name")
    name: str = Field(..., description="Display name, unique within the provider")
    model_id: str = Field(..., description="Identifier sent to the provider API")
    ref: str = Field(..., description="Asset reference string (provider_name/name)")
    context_size: Optional[int] = Field(default=None, description="Context window size in tokens")
    extra_params: dict = Field(default_factory=dict, description="Model-level extra parameters")
    enabled: bool = Field(..., description="Whether this model may be resolved at runtime")
    created_by: Optional[str] = Field(default=None, description="Audit-only creator identifier")
    created_at: Optional[str] = Field(default=None, description="Creation timestamp")
    updated_at: Optional[str] = Field(default=None, description="Last modification timestamp")
