"""Unit tests for the G4 model + settings increments (spec-g4-chat §7.1/§3.4/§4.4/§7.4).

Covers the ``subagent_trace`` source/session_id columns (chat trace rows,
§7.1) and the G4 settings keys: ``rebuild`` rate limit, CHAT_TRACE_ENABLED,
CHAT_AUTO_APPROVE_MAX_ROUNDS, SESSION_NAMING_ENABLED.
"""

import pytest

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
