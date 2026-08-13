"""Unit tests for app.schemas.agent_apps (Task #3: T2 Pydantic schemas)."""

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas.agent_apps import (
    AgentAppCreate,
    AgentAppRead,
    AgentAppUpdate,
    LlmConfigCreate,
    LlmConfigUpdate,
    McpServerCreate,
    McpServerRead,
    McpServerUpdate,
    SkillCreate,
    SkillGenerateRequest,
    SkillGenerateResponse,
    SkillRead,
    SkillUpdate,
    SubAgentCreate,
    SubAgentRead,
    SubAgentTestRequest,
    SubAgentTestResult,
    SubAgentUpdate,
    ToolCatalogEntry,
)

pytestmark = pytest.mark.unit


def _valid_subagent_payload() -> dict[str, str]:
    return {
        "name": "researcher",
        "description": "research sub agent",
        "when_to_use": "for research tasks",
        "system_prompt": "You are a researcher.",
    }


# ---------------------------------------------------------------------------
# Update schemas exclude name (PATCH semantics, name is immutable)
# ---------------------------------------------------------------------------


def test_update_schemas_do_not_contain_name_field() -> None:
    """All Update schemas must omit the immutable `name` field."""
    for schema in (SubAgentUpdate, SkillUpdate, AgentAppUpdate, McpServerUpdate):
        assert "name" not in schema.model_fields, f"{schema.__name__} must not contain 'name'"


def test_update_schemas_all_fields_optional() -> None:
    """Every field of Update schemas is optional (PATCH semantics)."""
    for schema in (SubAgentUpdate, SkillUpdate, AgentAppUpdate, McpServerUpdate):
        for field_name, field_info in schema.model_fields.items():
            assert not field_info.is_required(), f"{schema.__name__}.{field_name} must be optional"


# ---------------------------------------------------------------------------
# name pattern validation on Create schemas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_name", ["Researcher", "-lead", "_lead", "has space", "has.dot", ""])
@pytest.mark.parametrize(
    "schema",
    [SubAgentCreate, SkillCreate, AgentAppCreate, McpServerCreate],
    ids=["SubAgentCreate", "SkillCreate", "AgentAppCreate", "McpServerCreate"],
)
def test_create_name_invalid_pattern_raises(schema: type[BaseModel], bad_name: str) -> None:
    """Invalid name patterns (uppercase, leading -/_, spaces, dots, empty) are rejected."""
    payload: dict[str, str]
    if schema is SubAgentCreate:
        payload = _valid_subagent_payload()
    elif schema is SkillCreate:
        payload = {"description": "d", "body": "# skill"}
    elif schema is AgentAppCreate:
        payload = {"system_prompt": "s"}
    else:
        payload = {"transport": "stdio", "command": "npx"}
    payload["name"] = bad_name
    with pytest.raises(ValidationError):
        schema(**payload)


def test_create_name_max_length_enforced() -> None:
    """Names longer than 64 characters are rejected."""
    payload = _valid_subagent_payload()
    payload["name"] = "a" * 65
    with pytest.raises(ValidationError):
        SubAgentCreate(**payload)


def test_create_name_valid_patterns_accepted() -> None:
    """Valid names: lowercase alphanumerics with -, _ and leading digit."""
    for name in ("researcher", "research-agent", "research_agent", "0-bot", "a"):
        payload = _valid_subagent_payload()
        payload.pop("name")
        assert SubAgentCreate(name=name, **payload).name == name


# ---------------------------------------------------------------------------
# McpServer transport-dependent validation
# ---------------------------------------------------------------------------


def test_mcp_server_stdio_requires_command() -> None:
    """Stdio transport without command raises ValidationError."""
    with pytest.raises(ValidationError):
        McpServerCreate(name="local-fs", transport="stdio")


def test_mcp_server_stdio_rejects_url() -> None:
    """Stdio transport must not carry an http url (mutual exclusion)."""
    with pytest.raises(ValidationError):
        McpServerCreate(name="local-fs", transport="stdio", command="npx", url="https://mcp.example.com")


def test_mcp_server_http_requires_url() -> None:
    """Http transport without url raises ValidationError."""
    with pytest.raises(ValidationError):
        McpServerCreate(name="remote", transport="http")


def test_mcp_server_http_rejects_command() -> None:
    """Http transport must not carry a stdio command (mutual exclusion)."""
    with pytest.raises(ValidationError):
        McpServerCreate(name="remote", transport="http", url="https://mcp.example.com", command="npx")


def test_mcp_server_valid_payloads_pass() -> None:
    """Valid stdio and http payloads pass validation."""
    stdio = McpServerCreate(name="local-fs", transport="stdio", command="npx", args=["-y", "server"])
    assert stdio.command == "npx"
    assert stdio.url is None
    http = McpServerCreate(name="remote", transport="http", url="https://mcp.example.com")
    assert http.url == "https://mcp.example.com"
    assert http.command is None


def test_mcp_server_update_validates_transport_constraints() -> None:
    """McpServerUpdate applies the same transport constraints when fields are provided."""
    with pytest.raises(ValidationError):
        McpServerUpdate(transport="stdio")
    with pytest.raises(ValidationError):
        McpServerUpdate(transport="http")
    assert McpServerUpdate(transport="stdio", command="npx").command == "npx"
    assert McpServerUpdate(transport="http", url="https://mcp.example.com").url == "https://mcp.example.com"


# ---------------------------------------------------------------------------
# SubAgent max_turns constraint
# ---------------------------------------------------------------------------


def test_subagent_max_turns_must_be_at_least_one() -> None:
    """max_turns < 1 raises ValidationError."""
    with pytest.raises(ValidationError):
        SubAgentCreate(max_turns=0, **_valid_subagent_payload())


def test_subagent_max_turns_accepts_one_and_none() -> None:
    """max_turns accepts 1 and defaults to None."""
    assert SubAgentCreate(max_turns=1, **_valid_subagent_payload()).max_turns == 1
    assert SubAgentCreate(**_valid_subagent_payload()).max_turns is None


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_subagent_create_defaults() -> None:
    """SubAgentCreate defaults: optional fields None."""
    created = SubAgentCreate(**_valid_subagent_payload())
    assert created.allowed_tools is None
    assert created.model is None
    assert created.max_turns is None


def test_skill_create_defaults_and_body_required() -> None:
    """SkillCreate defaults description to '' and requires non-empty body."""
    assert SkillCreate(name="deploy", body="# skill").description == ""
    with pytest.raises(ValidationError):
        SkillCreate(name="deploy", body="")


def test_skill_update_defaults() -> None:
    """SkillUpdate fields default to None."""
    update = SkillUpdate()
    assert update.description is None
    assert update.body is None


def test_skill_generate_request_defaults() -> None:
    """SkillGenerateRequest requires description, defaults hint to ''."""
    request = SkillGenerateRequest(description="a deploy skill")
    assert request.hint == ""
    with pytest.raises(ValidationError):
        SkillGenerateRequest()


def test_agent_app_create_defaults() -> None:
    """AgentAppCreate defaults: empty collections, optional fields None."""
    created = AgentAppCreate(name="support-bot", system_prompt="You are support.")
    assert created.allowed_tools is None
    assert created.model is None
    assert created.skill_names == []
    assert created.subagent_names == []
    assert created.interrupt_on == {}


def test_agent_app_create_collection_defaults_are_independent() -> None:
    """default_factory produces independent containers per instance."""
    first = AgentAppCreate(name="app-a", system_prompt="a")
    second = AgentAppCreate(name="app-b", system_prompt="b")
    first.skill_names.append("mutated")
    assert second.skill_names == []


def test_agent_app_update_defaults() -> None:
    """AgentAppUpdate fields default to None (whole-replacement semantics)."""
    update = AgentAppUpdate()
    assert update.system_prompt is None
    assert update.allowed_tools is None
    assert update.model is None
    assert update.skill_names is None
    assert update.subagent_names is None
    assert update.interrupt_on is None


def test_mcp_server_create_defaults() -> None:
    """McpServerCreate defaults: enabled True, empty args/env, description ''."""
    server = McpServerCreate(name="local-fs", transport="stdio", command="npx")
    assert server.enabled is True
    assert server.description == ""
    assert server.args == []
    assert server.env == {}


def test_subagent_test_request_requires_non_empty_prompt() -> None:
    """SubAgentTestRequest requires a non-empty prompt."""
    assert SubAgentTestRequest(prompt="hello").prompt == "hello"
    with pytest.raises(ValidationError):
        SubAgentTestRequest(prompt="")


def test_subagent_test_result_fields() -> None:
    """SubAgentTestResult carries all result fields."""
    result = SubAgentTestResult(final_message="done", turns=3, duration_seconds=1.5, model="gpt-x")
    assert result.final_message == "done"
    assert result.turns == 3
    assert result.duration_seconds == 1.5
    assert result.model == "gpt-x"


def test_tool_catalog_entry_fields() -> None:
    """ToolCatalogEntry: builtin source has no server; mcp entries carry server name."""
    builtin = ToolCatalogEntry(name="search", source="builtin")
    assert builtin.server is None
    mcp = ToolCatalogEntry(name="fetch", source="mcp", server="remote-api")
    assert mcp.server == "remote-api"
    with pytest.raises(ValidationError):
        ToolCatalogEntry(name="bad", source="unknown")  # pyright: ignore[reportArgumentType]


def test_read_schemas_fields() -> None:
    """Read schemas expose content_hash/version/created_by audit fields."""
    subagent = SubAgentRead(
        name="researcher",
        description="d",
        when_to_use="w",
        system_prompt="s",
        content_hash="sha256:abc",
        version=2,
        created_by=None,
    )
    assert subagent.content_hash == "sha256:abc"
    assert subagent.version == 2

    skill = SkillRead(name="deploy", description="d", content_hash="sha256:def", version=1, created_by="alice")
    assert skill.created_by == "alice"

    agent_app = AgentAppRead(
        id=1,
        name="support-bot",
        system_prompt="s",
        allowed_tools=None,
        model=None,
        skill_names=[],
        subagent_names=[],
        interrupt_on={},
        engine="deepagents",
        status="draft",
        published_hash=None,
        version=1,
        created_by=None,
    )
    assert agent_app.id == 1

    server = McpServerRead(
        name="local-fs",
        transport="stdio",
        command="npx",
        args=[],
        env={},
        url=None,
        headers={},
        enabled=True,
        description="",
        content_hash="sha256:ghi",
        created_by=None,
    )
    assert server.content_hash == "sha256:ghi"

    assert SkillGenerateResponse(draft="# draft").draft == "# draft"


# ---------------------------------------------------------------------------
# LLM config schemas — temperature bounds & base_url normalization
# ---------------------------------------------------------------------------


def _llm_create_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {"name": "proxy", "model_name": "m", "api_key": "sk-secret-1234"}
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("temperature", [0.0, 2.0, 1.3])
def test_llm_config_temperature_within_bounds_accepted(temperature: float) -> None:
    """Temperatures inside [0.0, 2.0] validate on both Create and Update."""
    assert LlmConfigCreate(**_llm_create_payload(temperature=temperature)).temperature == temperature
    assert LlmConfigUpdate(temperature=temperature).temperature == temperature


@pytest.mark.parametrize("temperature", [-0.1, 2.1])
def test_llm_config_temperature_outside_bounds_rejected(temperature: float) -> None:
    """Temperatures outside [0.0, 2.0] are rejected on both Create and Update."""
    with pytest.raises(ValidationError):
        LlmConfigCreate(**_llm_create_payload(temperature=temperature))
    with pytest.raises(ValidationError):
        LlmConfigUpdate(temperature=temperature)


def test_llm_config_create_empty_base_url_normalized_to_none() -> None:
    """An empty-string base_url normalizes to None (SDK env fallback chain)."""
    assert LlmConfigCreate(**_llm_create_payload(base_url="")).base_url is None


def test_llm_config_update_empty_base_url_normalized_to_none() -> None:
    """PATCH payloads keep the same empty-string normalization."""
    assert LlmConfigUpdate(base_url="").base_url is None
    assert LlmConfigUpdate(base_url=None).base_url is None  # explicit null stays clearable
