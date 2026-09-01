"""Unit tests for the G3 runtime context-record hook and checkpoint helper.

Covers spec-g3-session §4.1.2 (``_fire_context_record`` on the ainvoke /
astream success paths, failure never blocks the response) and §11.5.1
(``delete_thread_checkpoint`` module helper: pool None -> warn + skip).
"""

import asyncio
import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.core.config import settings
from app.schemas import Message
from app.services.agents import context_store, runtime
from tests.unit.agents.test_runtime import (
    ScriptedChatModel,
    _compile_runtime,
    _make_app,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clean_caches() -> Generator[None, None, None]:
    """Isolate the process-level compile and runtime caches between tests."""
    from app.services.agents import assembly

    assembly.clear_compile_cache()
    runtime.clear_runtime_cache()
    yield
    assembly.clear_compile_cache()
    runtime.clear_runtime_cache()


@pytest.fixture(autouse=True)
def mock_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub build_tool_catalog/get_mcp_tools so no MCP server is contacted."""
    from langchain_core.tools import BaseTool

    from app.services.agents import assembly

    async def fake_build_tool_catalog(session: Any) -> list[dict[str, Any]]:
        return [{"name": "echo", "source": "builtin"}]

    async def fake_get_mcp_tools(session: Any) -> list[BaseTool]:
        return []

    monkeypatch.setattr(assembly, "build_tool_catalog", fake_build_tool_catalog)
    monkeypatch.setattr(assembly, "get_mcp_tools", fake_get_mcp_tools)


@pytest.fixture(autouse=True)
def mock_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub memory_service search/add so no mem0/PG call is ever made."""
    from app.services.memory import memory_service

    async def fake_search(user_id: str | None, query: str) -> str:
        return ""

    async def fake_add(user_id: str | None, messages: list[dict], metadata: dict | None = None) -> None:
        return None

    monkeypatch.setattr(memory_service, "search", fake_search)
    monkeypatch.setattr(memory_service, "add", fake_add)


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect DATA_ROOT into tmp so L2 files land in isolation."""
    root = tmp_path / "data"
    monkeypatch.setattr(settings, "DATA_ROOT", str(root))
    return root


async def _drain_async() -> None:
    """Wait until every fire-and-forget runtime task has settled."""
    for _ in range(200):
        if not runtime._pending_tasks:  # noqa: SLF001 — assert on module task registry
            break
        await asyncio.sleep(0.01)


def _l2_rows(app_id: int, user_id: int, session_id: str) -> list[dict]:
    path = context_store.session_file_path(app_id, user_id, session_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_ainvoke_writes_user_and_assistant_rows(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Success path records the turn's user input + assistant reply (§4.1.2)."""
    model = ScriptedChatModel(responses=[AIMessage(content="hello there")])
    app_cfg = _make_app()
    app_cfg.id = 7
    rt = _compile_runtime(model, app_cfg, monkeypatch)

    async def run() -> Any:
        result = await rt.ainvoke(
            [Message(role="user", content="hi")], session_id="sess-1", user_id="1", username="ann"
        )
        await _drain_async()  # same loop: let the fire-and-forget task finish
        return result

    result = asyncio.run(run())
    assert result[-1].content == "hello there"

    rows = _l2_rows(7, 1, "sess-1")
    assert [row["type"] for row in rows] == ["message", "message"]
    assert rows[0]["role"] == "user" and rows[0]["content"] == "hi"
    assert rows[1]["role"] == "assistant" and rows[1]["content"] == "hello there"
    assert [row["seq"] for row in rows] == [1, 2]
    assert all(row["ts"] for row in rows)


def test_astream_writes_user_and_assistant_rows(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Astream success path records the same turn transcript (§4.1.2)."""
    model = ScriptedChatModel(responses=[AIMessage(content="streamed answer")])
    app_cfg = _make_app()
    app_cfg.id = 8
    rt = _compile_runtime(model, app_cfg, monkeypatch)

    async def run() -> list[str]:
        chunks = []
        async for chunk in rt.astream(
            [Message(role="user", content="stream me")], session_id="sess-2", user_id="1", username="ann"
        ):
            chunks.append(chunk.content)
        await _drain_async()
        return chunks

    chunks = asyncio.run(run())
    assert any("streamed answer" in text for text in chunks)

    rows = _l2_rows(8, 1, "sess-2")
    assert rows[0]["role"] == "user" and rows[0]["content"] == "stream me"
    assert rows[-1]["role"] == "assistant" and rows[-1]["content"] == "streamed answer"


def test_second_turn_seq_continues(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seq keeps growing across turns (monotonic, 1-based)."""
    model = ScriptedChatModel(responses=[AIMessage(content="one"), AIMessage(content="two")])
    app_cfg = _make_app()
    app_cfg.id = 9
    rt = _compile_runtime(model, app_cfg, monkeypatch)

    async def run() -> None:
        for turn in ("first", "second"):
            await rt.ainvoke(
                [Message(role="user", content=turn)], session_id="sess-3", user_id="1", username="ann"
            )
            await _drain_async()

    asyncio.run(run())

    rows = _l2_rows(9, 1, "sess-3")
    assert [row["seq"] for row in rows] == [1, 2, 3, 4]


def test_non_numeric_user_id_skips_l2(data_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A user id that is not a numeric string cannot address L2 -> no file."""
    model = ScriptedChatModel(responses=[AIMessage(content="reply")])
    app_cfg = _make_app()
    app_cfg.id = 10
    rt = _compile_runtime(model, app_cfg, monkeypatch)

    async def run() -> Any:
        result = await rt.ainvoke(
            [Message(role="user", content="hi")], session_id="sess-4", user_id="u-ann", username="ann"
        )
        await _drain_async()
        return result

    result = asyncio.run(run())
    assert result[-1].content == "reply"

    assert list((data_root / "agents").rglob("*.jsonl")) == []


def test_context_record_failure_never_blocks_ainvoke(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken L2 write is logged, never raised into the response path."""
    model = ScriptedChatModel(responses=[AIMessage(content="still fine")])
    app_cfg = _make_app()
    app_cfg.id = 11
    rt = _compile_runtime(model, app_cfg, monkeypatch)

    async def broken_append(path: Path, rows: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(context_store, "append_rows", broken_append)

    async def run() -> Any:
        result = await rt.ainvoke(
            [Message(role="user", content="hi")], session_id="sess-5", user_id="1", username="ann"
        )
        await _drain_async()
        return result

    result = asyncio.run(run())
    assert result[-1].content == "still fine"  # failure did not propagate


# ---------------------------------------------------------------------------
# Subagent segment accumulation + display-only L2 rows
# ---------------------------------------------------------------------------


async def _chunks(chunks: list[runtime.StreamChunk]) -> Any:
    for chunk in chunks:
        yield chunk


def test_segment_accumulator_groups_subagent_messages() -> None:
    """Per-turn segments: a tool call closes the span; coordinator passes."""

    async def run() -> list[runtime.SubagentSegment]:
        accumulator = runtime._accumulate_stream_segments(  # noqa: SLF001 — unit test targets the helper
            _chunks(
                [
                    runtime.StreamChunk(content="主回复", source="coordinator"),
                    runtime.StreamChunk(content="研究", source="researcher"),
                    runtime.StreamChunk(content="中…", source="researcher"),
                    runtime.StreamChunk(content="out1", source="researcher", type="tool_call", name="search"),
                    runtime.StreamChunk(content="再研究", source="researcher"),
                    runtime.StreamChunk(content="代码", source="coder"),
                    runtime.StreamChunk(content="{}", source="system", type="interrupt"),
                    runtime.StreamChunk(content="收尾", source="coordinator"),
                ]
            )
        )
        relayed = [chunk async for chunk in accumulator.generator]
        assert len(relayed) == 8  # every chunk passes through untouched
        return accumulator.segments

    segments = asyncio.run(run())
    assert [(s.source, s.text, s.tool_calls) for s in segments] == [
        ("researcher", "研究中…", [("search", "out1")]),
        ("researcher", "再研究", []),
        ("coder", "代码", []),
    ]


def test_segment_accumulator_tool_call_opens_segment_without_text() -> None:
    """A tool call before any message chunk still lands in its own segment."""

    async def run() -> list[runtime.SubagentSegment]:
        accumulator = runtime._accumulate_stream_segments(  # noqa: SLF001 — unit test targets the helper
            _chunks(
                [
                    runtime.StreamChunk(content="hit", source="coder", type="tool_call", name="run"),
                    runtime.StreamChunk(content="正文", source="coder"),
                ]
            )
        )
        _ = [chunk async for chunk in accumulator.generator]
        return accumulator.segments

    segments = asyncio.run(run())
    assert [(s.source, s.text, s.tool_calls) for s in segments] == [
        ("coder", "", [("run", "hit")]),
        ("coder", "正文", []),
    ]


def test_append_context_rows_writes_subagent_rows_between_user_and_assistant(tmp_path: Path) -> None:
    """L2 turn rows: user -> per-segment message/tool_call rows (source) -> assistant."""
    path = tmp_path / "sessions" / "sess.jsonl"

    asyncio.run(
        runtime._append_context_rows(  # noqa: SLF001 — unit test targets the helper
            path,
            "hi",
            "final answer",
            [
                runtime.SubagentSegment(source="researcher", text="研究中…", tool_calls=[("search", "out1")]),
                runtime.SubagentSegment(source="coder", text="", tool_calls=[("run", "hit")]),
            ],
        )
    )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert [row["type"] for row in rows] == ["message", "message", "tool_call", "tool_call", "message"]
    assert [row.get("source") for row in rows] == [None, "researcher", "researcher", "coder", None]
    assert rows[2]["name"] == "search" and rows[2]["content"] == "out1"
    assert rows[3]["name"] == "run" and rows[3]["content"] == "hit"  # empty text writes no message row
    assert rows[0]["content"] == "hi" and rows[-1]["content"] == "final answer"
    assert [row["seq"] for row in rows] == [1, 2, 3, 4, 5]


class _RecordingSaver:
    """Minimal checkpointer double recording adelete_thread calls."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


def test_delete_thread_checkpoint_calls_adelete_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """The helper deletes the thread through the shared checkpointer."""
    saver = _RecordingSaver()

    async def fake_build() -> Any:
        return saver

    monkeypatch.setattr(runtime, "_build_checkpointer", fake_build)

    asyncio.run(runtime.delete_thread_checkpoint("sess-9"))
    assert saver.deleted == ["sess-9"]


def test_delete_thread_checkpoint_no_pool_warns_and_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pool unavailable -> warn + skip, never raise (§11.5.1)."""
    async def fake_build() -> None:
        return None

    monkeypatch.setattr(runtime, "_build_checkpointer", fake_build)

    asyncio.run(runtime.delete_thread_checkpoint("sess-10"))  # must not raise
