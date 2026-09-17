"""Unit tests for the chatflow AgentApp fields (spec-01 Phase 1).

Covers the ``workflow_id`` ORM column and the ``engine``/``workflow_id``
schema wiring that lets an AgentApp bind a registered workflow
(``engine="workflow"``). Pure schema/model objects — no DB session.
"""

import pytest
from pydantic import ValidationError

from app.models.agent_assets import AgentApp
from app.schemas.agent_apps import AgentAppCreate, AgentAppRead, AgentAppUpdate

pytestmark = pytest.mark.unit


def _app_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"name": "chatflow-demo", "system_prompt": "You are helpful."}
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


def test_agent_app_workflow_id_defaults_none() -> None:
    """A new AgentApp has workflow_id=None and engine='deepagents' by default."""
    app = AgentApp(name="demo", system_prompt="hi")
    assert app.workflow_id is None
    assert app.engine == "deepagents"


def test_agent_app_persists_workflow_id() -> None:
    """The workflow_id column round-trips on the ORM object."""
    app = AgentApp(name="demo", system_prompt="hi", engine="workflow", workflow_id="my-chatflow")
    assert app.engine == "workflow"
    assert app.workflow_id == "my-chatflow"


# ---------------------------------------------------------------------------
# AgentAppCreate
# ---------------------------------------------------------------------------


def test_create_defaults_to_deepagents_engine() -> None:
    """Engine defaults to 'deepagents' and workflow_id to None."""
    schema = AgentAppCreate(**_app_payload())
    assert schema.engine == "deepagents"
    assert schema.workflow_id is None


def test_create_workflow_engine_with_id_ok() -> None:
    """engine='workflow' with a non-empty workflow_id validates."""
    schema = AgentAppCreate(**_app_payload(engine="workflow", workflow_id="my-chatflow"))
    assert schema.engine == "workflow"
    assert schema.workflow_id == "my-chatflow"


def test_create_workflow_engine_missing_id_rejected() -> None:
    """engine='workflow' without workflow_id raises ValidationError (API 422)."""
    with pytest.raises(ValidationError):
        AgentAppCreate(**_app_payload(engine="workflow"))


def test_create_workflow_engine_empty_id_rejected() -> None:
    """engine='workflow' with an empty/blank workflow_id is rejected."""
    with pytest.raises(ValidationError):
        AgentAppCreate(**_app_payload(engine="workflow", workflow_id="   "))


def test_create_unknown_engine_rejected() -> None:
    """Engine is a closed Literal — unknown values are rejected."""
    with pytest.raises(ValidationError):
        AgentAppCreate(**_app_payload(engine="bogus"))


def test_create_deepagents_ignores_workflow_id() -> None:
    """A deepagents app may carry workflow_id=None; the field stays optional."""
    schema = AgentAppCreate(**_app_payload(engine="deepagents"))
    assert schema.workflow_id is None


# ---------------------------------------------------------------------------
# AgentAppUpdate / AgentAppRead
# ---------------------------------------------------------------------------


def test_update_has_optional_workflow_id_but_no_engine() -> None:
    """Update allows rebinding workflow_id; engine is immutable (absent)."""
    assert "workflow_id" in AgentAppUpdate.model_fields
    assert "engine" not in AgentAppUpdate.model_fields
    assert not AgentAppUpdate.model_fields["workflow_id"].is_required()


def test_read_includes_workflow_id() -> None:
    """AgentAppRead serializes workflow_id."""
    assert "workflow_id" in AgentAppRead.model_fields
