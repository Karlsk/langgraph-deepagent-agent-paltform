"""Unit tests for the G3 model changes (spec-g3-session §11.4).

- ``Session.agent_app_id`` str -> Optional[int] + new ``updated_at`` column
- ``AgentApp.context_size`` nullable int (compression threshold)
- ``SubAgentTestTrace`` renamed to ``SubAgentTrace`` (table + index)
- settings: ``DEFAULT_AGENT_CONTEXT_SIZE`` + ``sessions`` rate-limit key
"""

import pytest
from sqlalchemy import inspect as sa_inspect

from app.core.config import settings
from app.models.agent_assets import AgentApp
from app.models.session import Session
from app.models.subagent_trace import SubAgentTrace
from app.models.user import User  # noqa: F401 — resolves Session.user relationship mapper

pytestmark = pytest.mark.unit


def test_session_agent_app_id_is_optional_int() -> None:
    """Session.agent_app_id migrated to Optional[int] (spec §11.4.1)."""
    annotation = Session.model_fields["agent_app_id"].annotation
    assert annotation == "Optional[int]" or annotation == int | None or "int" in str(annotation)
    assert "str" not in str(annotation).replace("int", "")


def test_session_updated_at_field_exists() -> None:
    """Session carries updated_at for PATCH rename visibility (spec §11.4.1)."""
    assert "updated_at" in Session.model_fields


def test_agent_app_context_size_field() -> None:
    """AgentApp.context_size nullable int defaults to None (spec §11.4.2)."""
    field = AgentApp.model_fields["context_size"]
    assert field.default is None
    assert "int" in str(field.annotation)


def test_agent_app_column_types_on_metadata() -> None:
    """Column DDL types: context_size INTEGER nullable; session columns match the model."""
    agent_cols = {c.name: c for c in sa_inspect(AgentApp).columns}
    assert agent_cols["context_size"].nullable is True

    session_cols = {c.name: c for c in sa_inspect(Session).columns}
    assert session_cols["agent_app_id"].nullable is True


def test_subagent_trace_renamed() -> None:
    """Model class SubAgentTrace on table subagent_trace with renamed index (spec §11.4.3)."""
    assert SubAgentTrace.__tablename__ == "subagent_trace"
    indexes = {idx.name for idx in SubAgentTrace.__table__.indexes}
    assert "ix_subagent_trace_created_at" in indexes
    assert "ix_subagent_trace_name" in indexes


def test_settings_default_agent_context_size() -> None:
    """settings.DEFAULT_AGENT_CONTEXT_SIZE global fallback = 128000 (spec §11.4.4)."""
    assert settings.DEFAULT_AGENT_CONTEXT_SIZE == 128000


def test_settings_sessions_rate_limit_key() -> None:
    """RATE_LIMIT_ENDPOINTS carries the unified sessions key (spec §11.8)."""
    assert "sessions" in settings.RATE_LIMIT_ENDPOINTS
    assert settings.RATE_LIMIT_ENDPOINTS["sessions"]
