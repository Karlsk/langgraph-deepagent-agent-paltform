"""Unit tests for the G3 compression wiring (spec-g3-session §4.2).

- ``compile_agent_app`` attaches ``SummarizationMiddleware`` with the
  token trigger from ``AgentApp.context_size`` or the settings default
- the compile fingerprint covers ``context_size`` (threshold edits force
  a recompile)
- ``context_compression_total{app_id, status}`` counter exists and the
  runtime event observer dedupes repeated ``_summarization_event`` values
"""

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from prometheus_client import REGISTRY

from app.core.config import settings
from app.core.metrics import context_compression_total
from app.services.agents import assembly, runtime
from tests.unit.agents.test_runtime import ScriptedChatModel, _make_app, _patch_llm_seams

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_caches() -> None:
    assembly.clear_compile_cache()
    runtime.clear_runtime_cache()


@pytest.fixture(autouse=True)
def mock_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub build_tool_catalog/get_mcp_tools so no MCP server is contacted."""
    from langchain_core.tools import BaseTool

    async def fake_build_tool_catalog(session: Any) -> list[dict[str, Any]]:
        return [{"name": "echo", "source": "builtin"}]

    async def fake_get_mcp_tools(session: Any) -> list[BaseTool]:
        return []

    monkeypatch.setattr(assembly, "build_tool_catalog", fake_build_tool_catalog)
    monkeypatch.setattr(assembly, "get_mcp_tools", fake_get_mcp_tools)


def _spy_summarization(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every SummarizationMiddleware construction inside assembly."""
    real_cls = assembly.SummarizationMiddleware
    recorded: list[dict[str, Any]] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        recorded.append(kwargs)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(assembly, "SummarizationMiddleware", spy)

    def fake_create_deep_agent(**kwargs: Any) -> Any:
        return object()

    monkeypatch.setattr(assembly, "create_deep_agent", fake_create_deep_agent)
    return recorded


_SAMPLE = "context_compression_total"


def test_compile_uses_explicit_context_size_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """AgentApp.context_size drives the token trigger (spec §4.2)."""
    _patch_llm_seams(monkeypatch, ScriptedChatModel(responses=[AIMessage(content="ok")]))
    recorded = _spy_summarization(monkeypatch)
    app_cfg = _make_app()
    app_cfg.context_size = 5000

    asyncio.run(assembly.compile_agent_app(object(), app_cfg, subagent_cfgs=[], user_id=1))

    assert recorded, "SummarizationMiddleware must be constructed during compile"
    assert recorded[0]["trigger"] == ("tokens", 5000)
    assert recorded[0]["backend"] is not None


def test_compile_falls_back_to_settings_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """NULL context_size -> settings.DEFAULT_AGENT_CONTEXT_SIZE (spec §4.2)."""
    _patch_llm_seams(monkeypatch, ScriptedChatModel(responses=[AIMessage(content="ok")]))
    recorded = _spy_summarization(monkeypatch)
    app_cfg = _make_app()
    app_cfg.context_size = None

    asyncio.run(assembly.compile_agent_app(object(), app_cfg, subagent_cfgs=[], user_id=1))

    assert recorded[0]["trigger"] == ("tokens", settings.DEFAULT_AGENT_CONTEXT_SIZE)


def test_fingerprint_covers_context_size() -> None:
    """A context_size edit drifts the fingerprint and forces a recompile."""
    base = _make_app()
    tuned = _make_app()
    tuned.context_size = 64000
    assert base.context_size != tuned.context_size

    fp_base = assembly.compute_fingerprint(base, [], {}, "", "")
    fp_tuned = assembly.compute_fingerprint(tuned, [], {}, "", "")
    assert fp_base != fp_tuned


def test_context_compression_counter_labels() -> None:
    """context_compression_total{app_id, status} exists (spec §4.2)."""
    before = REGISTRY.get_sample_value(_SAMPLE, {"app_id": "1", "status": "occurred"}) or 0.0
    context_compression_total.labels(app_id="1", status="occurred").inc()
    after = REGISTRY.get_sample_value(_SAMPLE, {"app_id": "1", "status": "occurred"}) or 0.0
    assert after == before + 1


def _make_runtime(app_id: int = 3) -> runtime.DeepAgentsAppRuntime:
    app_cfg = _make_app()
    app_cfg.id = app_id
    return runtime.DeepAgentsAppRuntime(app_cfg=app_cfg, graph=None, checkpointer=None)  # pyright: ignore[reportArgumentType]


def _event(cutoff: int, summary: str) -> dict[str, Any]:
    return {
        "cutoff_index": cutoff,
        "summary_message": HumanMessage(content=summary),
        "file_path": "/conversation_history/t.md",
    }


def test_observe_compression_counts_each_event_once() -> None:
    """A new summarization event logs + increments; repeats are deduped."""
    rt = _make_runtime()
    label = {"app_id": "3", "status": "occurred"}

    def count() -> float:
        return REGISTRY.get_sample_value(_SAMPLE, label) or 0.0

    before = count()
    first = _event(5, "summarized so far")
    rt._observe_compression({"_summarization_event": first}, session_id="s1")  # noqa: SLF001
    assert count() == before + 1

    rt._observe_compression({"_summarization_event": first}, session_id="s1")  # noqa: SLF001
    assert count() == before + 1  # same event fingerprint: no double count

    second = _event(9, "summarized again")
    rt._observe_compression({"_summarization_event": second}, session_id="s1")  # noqa: SLF001
    assert count() == before + 2


def test_observe_compression_no_event_is_noop() -> None:
    """State without a summarization event never counts."""
    rt = _make_runtime()
    label = {"app_id": "3", "status": "occurred"}
    before = REGISTRY.get_sample_value(_SAMPLE, label) or 0.0

    rt._observe_compression({}, session_id="s2")  # noqa: SLF001
    rt._observe_compression({"messages": []}, session_id="s2")  # noqa: SLF001

    assert (REGISTRY.get_sample_value(_SAMPLE, label) or 0.0) == before
