"""Unit tests for the G4 model + settings increments (spec-g4-chat §7.1/§3.4/§4.4/§7.4).

Covers the ``subagent_trace`` source/session_id columns (chat trace rows,
§7.1) and the G4 settings keys: ``rebuild`` rate limit, CHAT_TRACE_ENABLED,
CHAT_AUTO_APPROVE_MAX_ROUNDS, SESSION_NAMING_ENABLED.
"""

import pytest

from app.core.config import Settings, settings
from app.models.subagent_trace import SubAgentTrace

pytestmark = pytest.mark.unit


def test_subagent_trace_source_column() -> None:
    """Source column exists, defaults to "test" (legacy rows, spec §7.1)."""
    assert "source" in SubAgentTrace.model_fields
    field = SubAgentTrace.model_fields["source"]
    assert field.default == "test"
    assert "str" in str(field.annotation)


def test_subagent_trace_session_id_column() -> None:
    """session_id column exists, nullable (chat rows only, spec §7.1)."""
    assert "session_id" in SubAgentTrace.model_fields
    field = SubAgentTrace.model_fields["session_id"]
    assert field.default is None
    annotation = str(field.annotation)
    assert "str" in annotation


def test_subagent_trace_columns_on_table_metadata() -> None:
    """Both G4 columns materialise on the table metadata (migration parity)."""
    columns = {column.name for column in SubAgentTrace.__table__.columns}
    assert {"source", "session_id"} <= columns


def test_settings_rebuild_rate_limit_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """RATE_LIMIT_ENDPOINTS carries the new rebuild key (spec §3.2/§3.4).

    Rebuilds Settings against a clean env so the default is asserted even when
    a local env file (e.g. .env.development) overrides RATE_LIMIT_REBUILD.
    """
    monkeypatch.delenv("RATE_LIMIT_REBUILD", raising=False)
    fresh = Settings()
    assert "rebuild" in fresh.RATE_LIMIT_ENDPOINTS
    assert fresh.RATE_LIMIT_ENDPOINTS["rebuild"] == ["5 per minute"]


def test_settings_chat_trace_enabled_defaults_true() -> None:
    """CHAT_TRACE_ENABLED defaults to True (spec §7.2)."""
    assert settings.CHAT_TRACE_ENABLED is True


def test_settings_chat_auto_approve_max_rounds() -> None:
    """CHAT_AUTO_APPROVE_MAX_ROUNDS defaults to 10 (spec §4.4)."""
    assert settings.CHAT_AUTO_APPROVE_MAX_ROUNDS == 10


def test_settings_session_naming_enabled_restored() -> None:
    """SESSION_NAMING_ENABLED restored with the pre-G1 default True (spec §8.1)."""
    assert settings.SESSION_NAMING_ENABLED is True
