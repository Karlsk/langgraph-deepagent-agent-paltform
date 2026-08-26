"""Unit tests for app.models.agent_assets (Task #2: T1 配置模型与 alembic 迁移)."""

import pytest

from app.models.agent_assets import (
    DEFAULT_AGENT_APP_ID,
    AgentApp,
    McpServerConfig,
    SkillAsset,
    SubAgentConfig,
    UserAgentAppAssociation,
)
from app.models.session import Session
from app.models.user import User  # noqa: F401 — required to resolve Session.user relationship mapper

pytestmark = pytest.mark.unit


def test_default_agent_app_id_constant() -> None:
    """The shared default agent app id constant is stable for backfill/seed use."""
    assert DEFAULT_AGENT_APP_ID == "system-default"


def test_table_names() -> None:
    """All four asset tables use stable snake_case table names."""
    assert SubAgentConfig.__tablename__ == "subagent_config"
    assert SkillAsset.__tablename__ == "skill_asset"
    assert AgentApp.__tablename__ == "agent_app"
    assert McpServerConfig.__tablename__ == "mcp_server_config"


def test_subagent_config_defaults() -> None:
    """SubAgentConfig defaults: version=1, optional fields None."""
    config = SubAgentConfig(
        name="researcher",
        description="research sub agent",
        when_to_use="for research tasks",
        system_prompt="You are a researcher.",
        content_hash="sha256:abc",
    )
    assert config.version == 1
    assert config.allowed_tools is None
    assert config.model is None
    assert config.max_turns is None
    assert config.created_by is None


def test_subagent_config_json_field_accepts_list() -> None:
    """allowed_tools is a JSON column that accepts a list of strings."""
    config = SubAgentConfig(
        name="researcher",
        description="research sub agent",
        when_to_use="for research tasks",
        system_prompt="You are a researcher.",
        content_hash="sha256:abc",
        allowed_tools=["search", "read_file"],
    )
    assert config.allowed_tools == ["search", "read_file"]


def test_skill_asset_defaults() -> None:
    """SkillAsset defaults: version=1, created_by None."""
    asset = SkillAsset(name="deploy-skill", description="deploy helper", content_hash="sha256:def")
    assert asset.version == 1
    assert asset.created_by is None


def test_skill_asset_scope_defaults_to_global() -> None:
    """SkillAsset.scope defaults to 'global' (G2 D1; Phase 5+ may extend 'agent')."""
    asset = SkillAsset(name="deploy-skill", description="deploy helper", content_hash="sha256:def")
    assert asset.scope == "global"


def test_agent_app_workspace_fields_defaults() -> None:
    """AgentApp G2 workspace fields default: agent_dir/workspace_hash None, status 'pending'."""
    app = AgentApp(name="support-bot", system_prompt="You are support.")
    assert app.agent_dir is None
    assert app.workspace_hash is None
    assert app.agent_workspace_status == "pending"


def test_user_agent_app_association_defaults() -> None:
    """UserAgentAppAssociation table name + field defaults (G2 D1 v3.3 spec §3.4)."""
    assoc = UserAgentAppAssociation(user_id=1, agent_app_id=2)
    assert UserAgentAppAssociation.__tablename__ == "user_agent_app_association"
    assert assoc.id is None
    assert assoc.user_id == 1
    assert assoc.agent_app_id == 2
    assert assoc.last_synced_workspace_hash is None
    assert assoc.associated_at is not None


def test_agent_app_defaults() -> None:
    """AgentApp defaults: engine/status/version and JSON containers."""
    app = AgentApp(name="support-bot", system_prompt="You are support.")
    assert app.engine == "deepagents"
    assert app.status == "draft"
    assert app.version == 1
    assert app.allowed_tools is None
    assert app.model is None
    assert app.skill_names == []
    assert app.subagent_names == []
    assert app.interrupt_on == {}
    assert app.published_hash is None
    assert app.created_by is None


def test_agent_app_json_fields_accept_list_and_dict() -> None:
    """AgentApp JSON fields accept list/dict payloads."""
    app = AgentApp(
        name="support-bot",
        system_prompt="You are support.",
        allowed_tools=["tool_a"],
        skill_names=["skill_a", "skill_b"],
        subagent_names=["sub_a"],
        interrupt_on={"human_in_the_loop": True},
    )
    assert app.allowed_tools == ["tool_a"]
    assert app.skill_names == ["skill_a", "skill_b"]
    assert app.subagent_names == ["sub_a"]
    assert app.interrupt_on == {"human_in_the_loop": True}


def test_agent_app_json_defaults_are_independent_instances() -> None:
    """default_factory produces independent containers per instance."""
    first = AgentApp(name="app-a", system_prompt="a")
    second = AgentApp(name="app-b", system_prompt="b")
    first.skill_names.append("mutated")
    assert second.skill_names == []
    assert second.subagent_names == []
    assert second.interrupt_on == {}


def test_mcp_server_config_defaults() -> None:
    """McpServerConfig defaults: enabled True, empty JSON containers."""
    server = McpServerConfig(name="local-fs", transport="stdio", content_hash="sha256:ghi")
    assert server.enabled is True
    assert server.args == []
    assert server.env == {}
    assert server.url is None
    assert server.headers == {}
    assert server.description == ""
    assert server.command is None
    assert server.created_by is None


def test_mcp_server_config_json_fields_accept_payloads() -> None:
    """McpServerConfig JSON fields accept list/dict payloads."""
    server = McpServerConfig(
        name="remote-api",
        transport="http",
        content_hash="sha256:jkl",
        url="https://mcp.example.com",
        headers={"Authorization": "Bearer token"},
        args=["--verbose"],
        env={"LOG_LEVEL": "debug"},
        enabled=False,
    )
    assert server.transport == "http"
    assert server.headers == {"Authorization": "Bearer token"}
    assert server.args == ["--verbose"]
    assert server.env == {"LOG_LEVEL": "debug"}
    assert server.enabled is False


def test_session_agent_app_id_defaults_to_none() -> None:
    """Session.agent_app_id is optional and defaults to None (no FK constraint)."""
    session = Session(id="sess-1", user_id=1)
    assert session.agent_app_id is None


def test_session_agent_app_id_accepts_value() -> None:
    """Session.agent_app_id can be bound to an agent app id string."""
    session = Session(id="sess-2", user_id=1, agent_app_id=DEFAULT_AGENT_APP_ID)
    assert session.agent_app_id == DEFAULT_AGENT_APP_ID
