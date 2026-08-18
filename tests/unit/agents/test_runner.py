"""Unit tests for the subagent one-shot test runner.

Zero real LLM / zero network: models are scripted ``BaseChatModel``
subclasses, ``build_tool_catalog`` / ``get_mcp_tools`` are monkeypatched,
and SubAgentConfig rows live in an in-memory SQLite database.
"""

import asyncio
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool, tool
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.core.metrics import agent_test_runs_total, subagent_task_duration_seconds
from app.models.agent_assets import SubAgentConfig
from app.models.provider import DEFAULT_MODEL_NAME, DEFAULT_MODEL_REF, DEFAULT_PROVIDER_NAME, ModelConfig, Provider
from app.services.agents import test_runner

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes & fixtures
# ---------------------------------------------------------------------------


class ScriptedChatModel(BaseChatModel):
    """Deterministic chat model replaying canned AIMessages (zero network).

    Every replayed message gets a fresh ``id`` (and fresh tool-call ids) so
    the deepagents messages reducer treats each turn as a distinct message.
    """

    responses: list[AIMessage]
    calls: list[list[BaseMessage]] = []
    bound_tools: list[list[Any]] = []
    n: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":
        """Record the bound tool set; replies stay scripted regardless."""
        self.bound_tools.append(list(tools))
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Record the call and replay the next canned response."""
        self.n += 1
        self.calls.append(list(messages))
        message = self.responses[(self.n - 1) % len(self.responses)].model_copy(deep=True)
        message.id = str(uuid.uuid4())
        for index, tool_call in enumerate(message.tool_calls):
            tool_call["id"] = f"tc-{self.n}-{index}"
        return ChatResult(generations=[ChatGeneration(message=message)])


@tool
def echo(text: str) -> str:
    """Echo the given text back."""
    return f"echo: {text}"


@tool
def upper(text: str) -> str:
    """Uppercase the given text."""
    return text.upper()


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite session seeded with the default provider/model pair."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        provider = Provider(
            name=DEFAULT_PROVIDER_NAME,
            type="OPENAI_COMPATIBLE",
            auth_config={"api_key": "sk-test-default"},
            created_by="test",
        )
        session.add(provider)
        session.commit()
        session.refresh(provider)
        session.add(
            ModelConfig(
                provider_id=provider.id,
                name=DEFAULT_MODEL_NAME,
                model_id=settings.DEFAULT_LLM_MODEL,
                created_by="test",
            )
        )
        session.commit()
        yield session


@pytest.fixture(autouse=True)
def mock_tools(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stub build_tool_catalog/get_mcp_tools so no MCP server is contacted."""
    catalog: list[dict[str, Any]] = [
        {"name": "duckduckgo_results_json", "source": "builtin"},
        {"name": "ask_human", "source": "builtin"},
        {"name": "echo", "source": "mcp", "server": "fake"},
        {"name": "upper", "source": "mcp", "server": "fake"},
    ]

    async def fake_build_tool_catalog(session: Any) -> list[dict[str, Any]]:
        return catalog

    async def fake_get_mcp_tools(session: Any) -> list[BaseTool]:
        return [echo, upper]

    monkeypatch.setattr(test_runner, "build_tool_catalog", fake_build_tool_catalog)
    monkeypatch.setattr(test_runner, "get_mcp_tools", fake_get_mcp_tools)
    return catalog


def _seed_config(session: Session, **overrides: Any) -> SubAgentConfig:
    """Persist a SubAgentConfig row with sensible defaults for runner tests."""
    defaults: dict[str, Any] = {
        "name": "helper",
        "description": "generic helper",
        "when_to_use": "Use for generic help.",
        "system_prompt": "You are a helper.",
        "allowed_tools": None,
        "model": None,
        "max_turns": None,
        "content_hash": "h-helper",
        "version": 1,
    }
    defaults.update(overrides)
    cfg = SubAgentConfig(**defaults)
    session.add(cfg)
    session.commit()
    session.refresh(cfg)
    return cfg


def _patch_registry(monkeypatch: pytest.MonkeyPatch, model: BaseChatModel) -> list[str]:
    """Redirect the ChatOpenAI construction seam; record resolved references."""
    requested: list[str] = []

    def fake_build(provider: Provider, model_cfg: ModelConfig) -> BaseChatModel:
        requested.append(f"{provider.name}/{model_cfg.name}")
        return model

    monkeypatch.setattr(test_runner, "build_chat_model", fake_build)
    return requested


def _seed_model_pair(session: Session, **overrides: Any) -> ModelConfig:
    """Persist an extra provider/model pair for explicit-reference tests."""
    defaults: dict[str, Any] = {
        "provider_name": "minimax",
        "model_name": "m3",
        "model_id": "MiniMax-M3",
    }
    defaults.update(overrides)
    provider = Provider(
        name=defaults["provider_name"],
        type="OPENAI_COMPATIBLE",
        auth_config={"api_key": "sk-test-mini"},
        created_by="test",
    )
    session.add(provider)
    session.commit()
    session.refresh(provider)
    model_cfg = ModelConfig(
        provider_id=provider.id,
        name=defaults["model_name"],
        model_id=defaults["model_id"],
        created_by="test",
    )
    session.add(model_cfg)
    session.commit()
    session.refresh(model_cfg)
    return model_cfg


def _run_counter(status: str) -> float:
    """Read the current value of agent_test_runs_total{status}."""
    return float(agent_test_runs_total.labels(status=status)._value.get())  # noqa: SLF001 — test introspection


def _duration_count(subagent: str) -> float:
    """Read the observation count of subagent_task_duration_seconds{subagent}."""
    for family in subagent_task_duration_seconds.collect():
        for sample in family.samples:
            if sample.name.endswith("_count") and sample.labels.get("subagent") == subagent:
                return sample.value
    return 0.0


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_run_subagent_once_success_path(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """A single-turn run returns the final AIMessage text and counts success."""
    _seed_config(db_session)
    model = ScriptedChatModel(responses=[AIMessage(content="done helping")])
    requested = _patch_registry(monkeypatch, model)

    success_before = _run_counter("success")
    duration_before = _duration_count("helper")

    result = asyncio.run(test_runner.run_subagent_once(db_session, name="helper", prompt="help me"))

    assert result.final_message == "done helping"
    assert result.turns == 1
    assert result.duration_seconds >= 0.0
    assert result.model == settings.DEFAULT_LLM_MODEL  # cfg.model=None -> default pair's model_id
    assert requested == [DEFAULT_MODEL_REF]
    assert _run_counter("success") == success_before + 1
    assert _duration_count("helper") == duration_before + 1


# ---------------------------------------------------------------------------
# provider/model reference resolution
# ---------------------------------------------------------------------------


def test_run_subagent_once_explicit_reference_resolves_pair(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-NULL model field resolves the provider/model pair; result.model is the model_id."""
    _seed_config(db_session, model="minimax/m3")
    _seed_model_pair(db_session)
    model = ScriptedChatModel(responses=[AIMessage(content="ok")])
    requested = _patch_registry(monkeypatch, model)

    result = asyncio.run(test_runner.run_subagent_once(db_session, name="helper", prompt="hi"))

    assert result.model == "MiniMax-M3"
    assert requested == ["minimax/m3"]


def test_run_subagent_once_missing_reference_raises_and_counts_error(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unresolvable model reference raises ValueError and counts status=error."""
    _seed_config(db_session, model="ghost/none")
    _patch_registry(monkeypatch, ScriptedChatModel(responses=[AIMessage(content="ok")]))
    error_before = _run_counter("error")

    with pytest.raises(ValueError, match="ghost/none"):
        asyncio.run(test_runner.run_subagent_once(db_session, name="helper", prompt="hi"))

    assert _run_counter("error") == error_before + 1


# ---------------------------------------------------------------------------
# max_turns hard gate
# ---------------------------------------------------------------------------


def test_run_subagent_once_max_turns_terminates_tool_loop(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A looping tool-call model is hard-stopped by the TurnLimitMiddleware."""
    _seed_config(db_session, allowed_tools=["echo"], max_turns=2)
    loop_call = AIMessage(
        content="", tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "s1", "type": "tool_call"}]
    )
    model = ScriptedChatModel(responses=[loop_call])
    _patch_registry(monkeypatch, model)

    success_before = _run_counter("success")

    result = asyncio.run(test_runner.run_subagent_once(db_session, name="helper", prompt="loop forever"))

    # The gate allowed exactly max_turns real model calls...
    assert model.n == 2
    # ...then short-circuited with a terminal AIMessage (counted as a turn).
    assert result.turns == 3
    assert "turn limit reached" in result.final_message.lower()
    assert _run_counter("success") == success_before + 1


# ---------------------------------------------------------------------------
# Unknown name
# ---------------------------------------------------------------------------


def test_run_subagent_once_unknown_name_raises_and_counts_error(db_session: Session) -> None:
    """A missing SubAgentConfig raises ValueError and counts status=error."""
    error_before = _run_counter("error")
    success_before = _run_counter("success")

    with pytest.raises(ValueError, match="ghost"):
        asyncio.run(test_runner.run_subagent_once(db_session, name="ghost", prompt="hello"))

    assert _run_counter("error") == error_before + 1
    assert _run_counter("success") == success_before


# ---------------------------------------------------------------------------
# allowed_tools filtering
# ---------------------------------------------------------------------------


def test_run_subagent_once_allowed_tools_filter_reaches_model(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the whitelisted tools are bound to the model."""
    _seed_config(db_session, allowed_tools=["echo"])
    model = ScriptedChatModel(responses=[AIMessage(content="ok")])
    _patch_registry(monkeypatch, model)

    result = asyncio.run(test_runner.run_subagent_once(db_session, name="helper", prompt="hi"))

    assert result.final_message == "ok"
    assert model.bound_tools, "expected the model to receive a bound tool set"
    bound_names = {bound.name for bound in model.bound_tools[0]}
    # The whitelisted catalog tool is present; the non-whitelisted ones are not
    # (deepagents additionally injects its own built-in filesystem tools).
    assert "echo" in bound_names
    assert bound_names.isdisjoint({"upper", "ask_human", "duckduckgo_results_json"})


# ---------------------------------------------------------------------------
# Langfuse tracing parity with the chat runtime
# ---------------------------------------------------------------------------


class _CapturingGraph:
    """Fake compiled graph recording the ainvoke config (zero LLM)."""

    def __init__(self, captured: dict[str, Any]) -> None:
        """Store the shared capture dict."""
        self.captured = captured

    async def ainvoke(self, graph_input: Any, config: Any = None) -> dict[str, Any]:
        """Record input/config and return a canned final state."""
        self.captured["input"] = graph_input
        self.captured["config"] = config
        return {"messages": [AIMessage(content="ok")]}


def _patch_graph(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(test_runner, "compile_standalone_subagent", lambda *a, **kw: _CapturingGraph(captured))
    return captured


def test_run_subagent_once_passes_langfuse_callback_when_tracing_enabled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tracing enabled -> the Langfuse handler is attached to the invoke config."""
    _seed_config(db_session)
    _patch_registry(monkeypatch, ScriptedChatModel(responses=[AIMessage(content="ok")]))
    monkeypatch.setattr(settings, "LANGFUSE_TRACING_ENABLED", True)
    sentinel = object()
    monkeypatch.setattr(test_runner, "langfuse_callback_handler", sentinel)
    captured = _patch_graph(monkeypatch)

    result = asyncio.run(test_runner.run_subagent_once(db_session, name="helper", prompt="hi"))

    assert result.final_message == "ok"
    assert captured["config"]["callbacks"] == [sentinel]


def test_run_subagent_once_omits_callback_when_tracing_disabled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tracing disabled -> no callbacks are passed (test isolation)."""
    _seed_config(db_session)
    _patch_registry(monkeypatch, ScriptedChatModel(responses=[AIMessage(content="ok")]))
    monkeypatch.setattr(settings, "LANGFUSE_TRACING_ENABLED", False)
    captured = _patch_graph(monkeypatch)

    asyncio.run(test_runner.run_subagent_once(db_session, name="helper", prompt="hi"))

    assert captured["config"]["callbacks"] == []
