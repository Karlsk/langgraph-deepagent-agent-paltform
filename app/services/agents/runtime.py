"""AgentApp runtime objects: engine-agnostic execution layer over compiled graphs.

``AgentAppRuntime`` is the unified interface the API layer talks to. The
abstract base class fixes the cross-cutting semantics as template methods
(mirroring ``app/core/langgraph/graph.py``):

- config construction: ``thread_id=session_id``, Langfuse callback when
  tracing is enabled, metadata with user_id/username/session_id/environment;
- resume detection: a pending checkpoint (``aget_state().next`` non-empty)
  turns the invocation into ``Command(resume=<last user message>)``;
- interrupt extraction: ``state.tasks[0].interrupts[0].value`` with the
  "Waiting for input." fallback, plus a ``GraphInterrupt`` safety net;
- fire-and-forget long-term memory write-back after successful responses
  (``memory_service.add`` over ``convert_to_openai_messages`` output).

Concrete runtimes only implement the raw primitives ``_run`` / ``_stream`` /
``_history`` / ``_clear`` (plus the ``_get_state`` hook the templates need).

Streaming API decision (verified against installed langgraph 1.2.x):
``astream_events(version="v3")`` exists and returns typed projections, but the
protocol is experimental and its ``.messages`` projection excludes subagent
tokens (root scope only), and there is no async interleave to merge it with
``.subagents`` into one ordered stream. Therefore ``DeepAgentsAppRuntime``
streams via ``astream(stream_mode="messages", subgraphs=True)`` and derives
the chunk source from the message metadata (``lc_agent_name`` carries the
subagent config name; empty namespace -> ``"coordinator"``).

``assembly`` remains the only module importing ``deepagents`` — this module
stays engine-agnostic and only consumes ``CompiledStateGraph`` objects.
"""

import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional, cast, override

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
    convert_to_openai_messages,
)
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.langgraph.pool import get_shared_connection_pool
from app.core.logging import logger
from app.core.metrics import (
    context_compression_total,
    llm_inference_duration_seconds,
    subagent_task_duration_seconds,
)
from app.core.observability import langfuse_callback_handler
from app.models.agent_assets import (
    AgentApp,
    SkillAsset,
    SubAgentConfig,
)
from app.models.provider import DEFAULT_MODEL_REF, ModelConfig, Provider
from app.schemas import Message
from app.services.agents import assembly, context_store
from app.services.agents.mcp_manager import load_mcp_servers
from app.services.llm.llm_store import compute_model_config_hash, parse_model_ref
from app.services.memory import memory_service
from app.utils import dump_messages, extract_text_content

_INTERRUPT_FALLBACK_TEXT = "Waiting for input."


def _utc_now_iso() -> str:
    """Current UTC time as an ISO8601 ``...Z`` string (L2 row ``ts`` field)."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _coerce_numeric_user_id(user_id: Optional[str]) -> Optional[int]:
    """Return the user id as an int when it is a numeric string, else None."""
    if user_id is None:
        return None
    try:
        return int(user_id)
    except ValueError:
        return None


async def _append_context_rows(path: Path, user_content: str, assistant_content: str) -> None:
    """Append one turn (user + assistant rows) to the L2 JSONL file."""
    seq = await context_store.next_seq(path)
    ts = _utc_now_iso()
    rows: list[dict[str, Any]] = []
    if user_content:
        rows.append({"seq": seq, "ts": ts, "type": "message", "role": "user", "content": user_content})
    if assistant_content:
        rows.append({"seq": seq + 1, "ts": ts, "type": "message", "role": "assistant", "content": assistant_content})
    if rows:
        await context_store.append_rows(path, rows)


# Strong references to fire-and-forget memory write-back tasks (prevents GC
# before completion); done callbacks remove entries and log failures.
_pending_tasks: set[asyncio.Task[Any]] = set()

# Checkpoint DDL must run at most once per process (multi-worker first-compile
# races would otherwise create tables concurrently).
_checkpointer_setup_done = False
_checkpointer_setup_lock = asyncio.Lock()


@dataclass
class StreamChunk:
    """One streamed content fragment with its origin.

    Attributes:
        content: Text fragment produced by the graph (message body, tool
            output, interrupt projection JSON or summary text).
        source: Origin tag — a subagent name, ``"coordinator"`` for the main
            agent, or ``"system"`` for runtime-generated chunks (interrupts).
        type: Frame kind (spec-g4-chat §4.1): ``message`` (default) |
            ``tool_call`` | ``interrupt`` | ``summary``.
        name: Tool name for ``tool_call`` chunks.
    """

    content: str
    source: Optional[str] = None
    type: str = "message"
    name: Optional[str] = None


def project_interrupt(value: Any) -> Optional[dict[str, Any]]:
    """Project a raw HITL interrupt value onto the stable G4 contract.

    The langchain ``HumanInTheLoopMiddleware`` interrupt value carries
    ``action_requests`` items keyed ``name``/``args`` (plus internal fields
    like ``description`` and a sibling ``review_configs`` list). The G4
    contract (spec-g4-chat §4.2) keeps only ``tool`` + ``args`` per item so
    the frontend approval UI never depends on middleware internals.

    Args:
        value: Raw interrupt value from the checkpoint state.

    Returns:
        ``{"action_requests": [{"tool": ..., "args": ...}, ...]}`` or None
        when the value cannot be projected (non-dict, no usable list).
    """
    if not isinstance(value, dict):
        return None
    requests = value.get("action_requests")
    if not isinstance(requests, list) or not requests:
        return None
    projected: list[dict[str, Any]] = []
    for item in requests:
        if not isinstance(item, dict):
            continue
        tool = item.get("name") or item.get("tool")
        if not tool:
            continue
        args = item.get("args")
        projected.append({"tool": str(tool), "args": args if isinstance(args, dict) else {}})
    return {"action_requests": projected} if projected else None


def _first_interrupt_value(state: StateSnapshot, default: Any = None) -> Any:
    """Extract the first interrupt value from a snapshot without IndexError.

    Args:
        state: The checkpoint snapshot to inspect.
        default: Value returned when no interrupt is present.

    Returns:
        The first interrupt value, or ``default`` when tasks/interrupts are empty.
    """
    if state.tasks:
        interrupts = state.tasks[0].interrupts
        if interrupts:
            return interrupts[0].value
    return default


# ---------------------------------------------------------------------------
# Abstract base runtime (template methods for cross-cutting semantics)
# ---------------------------------------------------------------------------


class AgentAppRuntime(ABC):
    """Unified execution interface for a published AgentApp.

    Subclasses implement the raw primitives (``_run``, ``_stream``,
    ``_history``, ``_clear`` and the ``_get_state`` hook); every public
    method applies the shared cross-cutting semantics described in the module
    docstring.
    """

    # Owning AgentApp id for L2 context records; None on abstract/fake
    # runtimes disables the hook (set by concrete __init__ implementations).
    app_id: Optional[int] = None

    # Per-session fingerprints of already-reported summarization events
    # (initialised by concrete __init__ implementations); the private
    # ``_summarization_event`` state key persists in the checkpoint, so
    # without dedup every post-compression turn would recount.
    _compression_seen: dict[str, tuple]

    def _build_config(self, session_id: str, user_id: Optional[str], username: Optional[str]) -> RunnableConfig:
        """Build the RunnableConfig used for every graph operation."""
        callbacks: list[BaseCallbackHandler] = [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        return {
            "configurable": {"thread_id": session_id},
            "callbacks": callbacks,
            "metadata": {
                "user_id": user_id,
                "username": username,
                "session_id": session_id,
                "environment": settings.ENVIRONMENT.value,
                "debug": settings.DEBUG,
            },
        }

    def _model_label(self) -> str:
        """Model label for metrics: the resolved real model name.

        The label must carry the upstream model identifier of the bound
        model config row (resolved at construction time), never the config
        reference name; degradation/missing paths fall back to the
        configured default model.
        """
        resolved = getattr(self, "resolved_model_name", None)
        return str(resolved) if resolved else settings.DEFAULT_LLM_MODEL

    # -- primitives implemented by concrete runtimes -------------------------

    @abstractmethod
    async def _get_state(self, config: RunnableConfig) -> StateSnapshot:
        """Return the current checkpoint snapshot for the config's thread."""

    @abstractmethod
    async def _run(self, graph_input: Any, config: RunnableConfig) -> dict[str, Any]:
        """Execute one full graph invocation and return the final state dict."""

    @abstractmethod
    def _stream(self, graph_input: Any, config: RunnableConfig) -> AsyncGenerator[StreamChunk, None]:
        """Yield raw StreamChunks for one graph invocation."""

    @abstractmethod
    async def _history(self, config: RunnableConfig) -> list[BaseMessage]:
        """Return the raw checkpoint messages of the config's thread."""

    @abstractmethod
    async def _clear(self, session_id: str) -> None:
        """Delete every checkpoint of the given thread."""

    # -- shared template internals -------------------------------------------

    def _build_resume_value(self, messages: list[Message], interrupt_value: Any) -> Any:
        """Build the resume payload for a paused thread.

        Default semantics mirror ``graph.py``: the last user message text is
        resumed verbatim. Engines expecting a structured payload override
        this hook.

        Args:
            messages: Conversation messages (the last one is the user reply).
            interrupt_value: The interrupt value the thread paused on.

        Returns:
            The value passed to ``Command(resume=...)``.
        """
        return messages[-1].content

    async def _prepare_input(self, messages: list[Message], config: RunnableConfig) -> Any:
        """Return the graph input: a resume Command or a fresh message batch."""
        state = await self._get_state(config)
        thread_id = cast(Optional[str], config.get("configurable", {}).get("thread_id"))
        if state.next:
            interrupt_value = _first_interrupt_value(state)
            logger.info("resuming_interrupted_graph", session_id=thread_id, next_nodes=state.next)
            return Command(resume=self._build_resume_value(messages, interrupt_value))
        return {"messages": dump_messages(messages)}

    async def _pending_interrupt_value(self, config: RunnableConfig) -> Optional[str]:
        """Return the interrupt value when the thread is paused, else None."""
        state = await self._get_state(config)
        if not state.next:
            return None
        interrupt_value = _first_interrupt_value(state, default=_INTERRUPT_FALLBACK_TEXT)
        logger.info(
            "graph_interrupted",
            session_id=config.get("configurable", {}).get("thread_id"),
            interrupt_value=str(interrupt_value),
        )
        return str(interrupt_value)

    def _fire_memory_add(
        self, messages: Sequence[BaseMessage], user_id: Optional[str], config: RunnableConfig
    ) -> None:
        """Fire-and-forget long-term memory write-back (successful runs only).

        The task is anchored in the module-level ``_pending_tasks`` set so it
        is not garbage-collected mid-flight; the done callback removes it and
        logs any exception instead of dropping it silently.
        """
        openai_msgs = cast(list[dict], convert_to_openai_messages(list(messages)))
        task = asyncio.create_task(
            memory_service.add(user_id, openai_msgs, cast(Optional[dict], config.get("metadata")))
        )
        _pending_tasks.add(task)

        def _on_done(done: asyncio.Task[Any]) -> None:
            _pending_tasks.discard(done)
            if done.cancelled():
                return
            exception = done.exception()
            if exception is not None:
                logger.error(
                    "memory_writeback_failed",
                    error=str(exception),
                    error_type=type(exception).__name__,
                )

        task.add_done_callback(_on_done)

    def _fire_context_record(
        self,
        messages: Sequence[Message],
        response_messages: Sequence[BaseMessage],
        *,
        session_id: str,
        user_id: Optional[str],
    ) -> None:
        """Fire-and-forget L2 context record write (G3 §4.1.2, success paths).

        Records the turn's user input (from the invoke arguments) and the
        assistant's final reply (last AIMessage of the response state) into
        the session JSONL file. Like ``_fire_memory_add`` the task is anchored
        in ``_pending_tasks`` and its failure is logged, never raised — a
        broken L2 write must not block the response. Runtimes without an
        ``app_id`` or with a non-numeric ``user_id`` cannot address the L2
        path and skip the write.
        """
        app_id = self.app_id
        numeric_user_id = _coerce_numeric_user_id(user_id)
        if app_id is None or numeric_user_id is None:
            logger.debug(
                "context_record_skipped_no_address",
                session_id=session_id,
                app_id=app_id,
                user_id=user_id,
            )
            return

        user_message = next((message for message in reversed(list(messages)) if message.role == "user"), None)
        assistant_text = next(
            (
                extract_text_content(message.content)
                for message in reversed(list(response_messages))
                if isinstance(message, AIMessage)
            ),
            "",
        )
        user_content = str(user_message.content) if user_message is not None else ""
        path = context_store.session_file_path(app_id, numeric_user_id, session_id)
        task = asyncio.create_task(_append_context_rows(path, user_content, str(assistant_text)))
        _pending_tasks.add(task)

        def _on_record_done(done: asyncio.Task[Any]) -> None:
            _pending_tasks.discard(done)
            if done.cancelled():
                return
            exception = done.exception()
            if exception is not None:
                logger.error(
                    "context_record_failed",
                    session_id=session_id,
                    app_id=app_id,
                    error=str(exception),
                    error_type=type(exception).__name__,
                )

        task.add_done_callback(_on_record_done)

    def _observe_compression(self, state_values: Optional[Mapping[str, Any]], *, session_id: str) -> None:
        """Log + count a NEW summarization event in the final state (G3 §4.2).

        ``SummarizationMiddleware`` records its latest event under the private
        ``_summarization_event`` state key where it persists across turns; a
        per-session fingerprint registry ensures each distinct event is
        reported exactly once per runtime instance.
        """
        if not isinstance(state_values, Mapping):
            return
        event = state_values.get("_summarization_event")
        if not isinstance(event, dict):
            return
        summary_message = event.get("summary_message")
        fingerprint = (
            event.get("cutoff_index"),
            str(getattr(summary_message, "content", summary_message))[:128],
            event.get("file_path"),
        )
        if self._compression_seen.get(session_id) == fingerprint:
            return
        self._compression_seen[session_id] = fingerprint
        logger.info(
            "context_compression_occurred",
            session_id=session_id,
            app_id=self.app_id,
            cutoff_index=event.get("cutoff_index"),
            file_path=event.get("file_path"),
        )
        context_compression_total.labels(app_id=str(self.app_id), status="occurred").inc()

    def _process_messages(self, messages: Sequence[BaseMessage]) -> list[Message]:
        """Project raw messages to user/assistant chat Messages."""
        openai_style_messages = convert_to_openai_messages(list(messages))
        return [
            Message(role=message["role"], content=str(message["content"]))
            for message in openai_style_messages
            if message["role"] in ("assistant", "user") and message["content"]
        ]

    # -- public unified interface ---------------------------------------------

    async def ainvoke(
        self,
        messages: list[Message],
        *,
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> list[Message]:
        """Run one turn and return the assistant reply (or the interrupt text).

        Args:
            messages: Conversation messages; the last user message also serves
                as the resume value when the thread is paused on an interrupt.
            session_id: Chat session id used as the checkpoint thread_id.
            user_id: Calling user id (memory partition + tracing metadata).
            username: Display name of the calling user.

        Returns:
            The response messages; an interrupted run returns a single
            assistant Message carrying the interrupt value.
        """
        config = self._build_config(session_id, user_id, username)
        try:
            graph_input = await self._prepare_input(messages, config)
            started = time.perf_counter()
            response = await self._run(graph_input, config)
            llm_inference_duration_seconds.labels(model=self._model_label()).observe(time.perf_counter() - started)

            interrupt_value = await self._pending_interrupt_value(config)
            if interrupt_value is not None:
                return [Message(role="assistant", content=interrupt_value)]

            response_messages = cast(list[BaseMessage], response["messages"])
            self._fire_memory_add(response_messages, user_id, config)
            self._fire_context_record(messages, response_messages, session_id=session_id, user_id=user_id)
            self._observe_compression(response, session_id=session_id)
            return self._process_messages(response_messages)
        except GraphInterrupt:
            state = await self._get_state(config)
            interrupt_value = _first_interrupt_value(state, default=_INTERRUPT_FALLBACK_TEXT)
            logger.info("graph_interrupted", session_id=session_id, interrupt_value=str(interrupt_value))
            return [Message(role="assistant", content=str(interrupt_value))]
        except Exception as e:
            logger.exception("agent_app_invoke_failed", error=str(e), session_id=session_id)
            raise

    def astream(
        self,
        messages: list[Message],
        *,
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream one turn as StreamChunks.

        The runtime only produces chunks; done-terminator semantics are the
        caller's responsibility. An interrupt hit during the run is emitted
        as the final chunk with ``source="system"``.

        Args:
            messages: Conversation messages (last one doubles as resume value).
            session_id: Chat session id used as the checkpoint thread_id.
            user_id: Calling user id (memory partition + tracing metadata).
            username: Display name of the calling user.

        Yields:
            StreamChunk objects in arrival order.
        """
        config = self._build_config(session_id, user_id, username)

        async def _generate() -> AsyncGenerator[StreamChunk, None]:
            started = time.perf_counter()
            try:
                graph_input = await self._prepare_input(messages, config)
                async for chunk in self._stream(graph_input, config):
                    yield chunk

                llm_inference_duration_seconds.labels(model=self._model_label()).observe(time.perf_counter() - started)
                state = await self._get_state(config)
                if state.next:
                    interrupt_value = _first_interrupt_value(state, default=_INTERRUPT_FALLBACK_TEXT)
                    logger.info(
                        "graph_interrupted",
                        session_id=config.get("configurable", {}).get("thread_id"),
                        interrupt_value=str(interrupt_value),
                    )
                    yield self._interrupt_tail_chunk(interrupt_value)
                    return

                if state.values and "messages" in state.values:
                    final_messages = cast(list[BaseMessage], state.values["messages"])
                    self._fire_memory_add(final_messages, user_id, config)
                    self._fire_context_record(messages, final_messages, session_id=session_id, user_id=user_id)
                self._observe_compression(state.values, session_id=session_id)
            except GraphInterrupt:
                llm_inference_duration_seconds.labels(model=self._model_label()).observe(time.perf_counter() - started)
                state = await self._get_state(config)
                interrupt_value = _first_interrupt_value(state, default=_INTERRUPT_FALLBACK_TEXT)
                logger.info("graph_interrupted_stream", session_id=session_id, interrupt_value=str(interrupt_value))
                yield self._interrupt_tail_chunk(interrupt_value)
            except Exception as e:
                logger.exception("agent_app_stream_failed", error=str(e), session_id=session_id)
                raise

        return _generate()

    def _interrupt_tail_chunk(self, interrupt_value: Any) -> StreamChunk:
        """Build the typed interrupt tail chunk (spec-g4-chat §4.1).

        The payload is the §4.2 projection serialised to JSON, fixing the
        pre-G4 ``str()`` defect where the raw middleware dict (with internal
        fields) leaked into the stream; unprojectable values keep the plain
        text form so the frontend still sees a non-empty frame.

        Args:
            interrupt_value: Raw interrupt value from the checkpoint.

        Returns:
            A ``type="interrupt"`` / ``source="system"`` StreamChunk.
        """
        projection = project_interrupt(interrupt_value)
        payload = json.dumps(projection) if projection is not None else str(interrupt_value)
        return StreamChunk(content=payload, source="system", type="interrupt")

    async def get_chat_history(self, session_id: str) -> list[Message]:
        """Return the user/assistant history of one thread.

        Args:
            session_id: Chat session id (checkpoint thread_id).

        Returns:
            The projected chat messages (empty when the thread is unknown).
        """
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        return self._process_messages(await self._history(config))

    async def clear_chat_history(self, session_id: str) -> None:
        """Delete every checkpoint of one thread.

        Args:
            session_id: Chat session id (checkpoint thread_id).
        """
        await self._clear(session_id)
        logger.info("agent_app_history_cleared", session_id=session_id)

    async def get_pending_interrupt(self, session_id: str) -> Optional[dict[str, Any]]:
        """Probe a thread for a paused interrupt (spec-g4-chat §4.4).

        Args:
            session_id: Chat session id (checkpoint thread_id).

        Returns:
            The §4.2 projection when the thread is paused on a HITL
            interrupt, else None (live/unknown threads).
        """
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        state = await self._get_state(config)
        if not state.next:
            return None
        return project_interrupt(_first_interrupt_value(state))


# ---------------------------------------------------------------------------
# deepagents-backed runtime
# ---------------------------------------------------------------------------


class DeepAgentsAppRuntime(AgentAppRuntime):
    """Runtime executing an AgentApp compiled by ``assembly`` into a graph."""

    def __init__(
        self,
        *,
        app_cfg: AgentApp,
        graph: CompiledStateGraph,
        checkpointer: BaseCheckpointSaver | None,
        resolved_model_name: str | None = None,
    ) -> None:
        """Bind the compiled graph and its checkpointer to the app config.

        Args:
            app_cfg: The (possibly HIL-degraded copy of the) AgentApp row.
            graph: Compiled graph from ``assembly.get_or_compile``.
            checkpointer: Checkpointer attached to the graph (may be None).
            resolved_model_name: Real upstream model name of the resolved
                app-level model config (metrics label source; None falls
                back to ``settings.DEFAULT_LLM_MODEL``).
        """
        self.app_cfg = app_cfg
        self._graph = graph
        self._checkpointer = checkpointer
        self.resolved_model_name = resolved_model_name
        self.app_id = app_cfg.id
        self._compression_seen: dict[str, tuple] = {}

    @override
    def _build_resume_value(self, messages: list[Message], interrupt_value: Any) -> Any:
        """Translate the user reply into the HIL response the middleware expects.

        The langchain HumanInTheLoopMiddleware resumes with
        ``{"decisions": [Decision, ...]}`` (one decision per interrupted
        action). The user message is parsed as that JSON payload; anything
        else (plain text, malformed JSON, "no") falls back to the SAFE
        default: an equal number of reject decisions, so an unstructured
        reply can never silently approve a pending action. The pending count
        is inferred from the interrupt value's ``action_requests`` (single
        reject when it cannot be inferred).

        Args:
            messages: Conversation messages (the last one is the user reply).
            interrupt_value: The HITL request dict the thread paused on.

        Returns:
            A structured HITL response payload.
        """
        content = messages[-1].content
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("decisions"), list) and parsed["decisions"]:
            return parsed

        pending = len(interrupt_value.get("action_requests", [])) if isinstance(interrupt_value, dict) else 1
        return {"decisions": [{"type": "reject"} for _ in range(max(pending, 1))]}

    @override
    async def _get_state(self, config: RunnableConfig) -> StateSnapshot:
        """Return the checkpoint snapshot of the bound graph."""
        return await self._graph.aget_state(config)

    @override
    async def _run(self, graph_input: Any, config: RunnableConfig) -> dict[str, Any]:
        """Invoke the bound graph once."""
        return cast(dict[str, Any], await self._graph.ainvoke(graph_input, config=config))

    @override
    async def _stream(self, graph_input: Any, config: RunnableConfig) -> AsyncGenerator[StreamChunk, None]:
        """Yield message/tool_call chunks tagged with their source agent.

        Uses ``astream(stream_mode="messages", subgraphs=True)``: chunk shape
        is ``(namespace, (message, metadata))``; ``metadata["lc_agent_name"]``
        carries the subagent config name, empty namespace means coordinator.
        AIMessages stream as ``message`` chunks; ToolMessages surface as
        ``tool_call`` chunks (name + output text, spec-g4-chat §4.1) so the
        frontend can render collapsible tool panels. While streaming,
        per-subagent first/last chunk timestamps are tracked and observed
        on ``subagent_task_duration_seconds`` once the stream ends
        (coordinator/system/empty sources are never timed).
        """
        timings: dict[str, tuple[float, float]] = {}
        async for chunk in self._graph.astream(graph_input, config, stream_mode="messages", subgraphs=True):
            namespace, payload = chunk
            message, metadata = cast(tuple[BaseMessage, dict[str, Any]], payload)
            if isinstance(message, ToolMessage):
                text = extract_text_content(message.content)
                if text and message.name:
                    source = str(metadata.get("lc_agent_name")) if namespace else "coordinator"
                    yield StreamChunk(content=text, source=source, type="tool_call", name=message.name)
                continue
            if not isinstance(message, (AIMessage, AIMessageChunk)):
                continue
            text = extract_text_content(message.content)
            if not text:
                continue
            source = str(metadata.get("lc_agent_name")) if namespace else "coordinator"
            if source and source not in ("coordinator", "system"):
                now = time.perf_counter()
                first, _ = timings.get(source, (now, now))
                timings[source] = (first, now)
            yield StreamChunk(content=text, source=source)
        for source, (first, last) in timings.items():
            subagent_task_duration_seconds.labels(subagent=source).observe(last - first)

    @override
    async def _history(self, config: RunnableConfig) -> list[BaseMessage]:
        """Read the raw messages stored in the thread checkpoint."""
        state = await self._get_state(config)
        if not state.values:
            return []
        return cast(list[BaseMessage], state.values.get("messages", []))

    @override
    async def _clear(self, session_id: str) -> None:
        """Delete the thread via the checkpointer.

        Raises:
            RuntimeError: When no checkpointer is attached — the legacy
                runtime contract surfaced this as a 500; silently
                pretending success would lie about the deletion.
        """
        if self._checkpointer is None:
            logger.warning("clear_history_skipped_no_checkpointer", session_id=session_id)
            raise RuntimeError("cannot clear chat history: no checkpointer attached")
        await self._checkpointer.adelete_thread(session_id)


class WorkflowAppRuntime(AgentAppRuntime):
    """Placeholder runtime reserved for the declarative workflow engine."""

    def __init__(self, *, app_cfg: AgentApp, resolved_model_name: str | None = None) -> None:
        """Bind the app config (no engine wiring yet).

        Args:
            app_cfg: The AgentApp row of engine="workflow".
            resolved_model_name: Real upstream model name of the resolved
                app-level model config (metrics label source; may be None).
        """
        self.app_cfg = app_cfg
        self.resolved_model_name = resolved_model_name
        self.app_id = app_cfg.id
        self._compression_seen: dict[str, tuple] = {}

    @override
    async def _get_state(self, config: RunnableConfig) -> StateSnapshot:
        """Reserved — always raises."""
        raise NotImplementedError("workflow engine runtime reserved")

    @override
    async def _run(self, graph_input: Any, config: RunnableConfig) -> dict[str, Any]:
        """Reserved — always raises."""
        raise NotImplementedError("workflow engine runtime reserved")

    @override
    def _stream(self, graph_input: Any, config: RunnableConfig) -> AsyncGenerator[StreamChunk, None]:
        """Reserved — always raises."""
        raise NotImplementedError("workflow engine runtime reserved")
        yield  # pragma: no cover — makes the function an async generator

    @override
    async def _history(self, config: RunnableConfig) -> list[BaseMessage]:
        """Reserved — always raises."""
        raise NotImplementedError("workflow engine runtime reserved")

    @override
    async def _clear(self, session_id: str) -> None:
        """Reserved — always raises."""
        raise NotImplementedError("workflow engine runtime reserved")


# ---------------------------------------------------------------------------
# Runtime resolution, fingerprint cache and HIL degradation
# ---------------------------------------------------------------------------

# Process-level runtime cache bounds (spec-g2-workspace v3.3 §6.1.2).
_RUNTIME_CACHE_MAX_SIZE = 1000  # entries; the least recently used one is evicted
_RUNTIME_CACHE_TTL = 3600  # seconds; older entries are evicted as stale


@dataclass
class _CacheEntry:
    """One cached runtime with its TTL/LRU timestamps (spec §6.1.2)."""

    runtime: AgentAppRuntime
    created_at: float  # time.time()
    last_accessed: float  # time.time() — LRU basis


# G2 v3 (spec §6.1.2): keyed by the (app_id, user_id, fingerprint) triple.
# The compiled graph embeds a per-user FilesystemBackend, so entries of
# different users must never alias.
_runtime_cache: dict[tuple[int, int, str], _CacheEntry] = {}


def clear_runtime_cache() -> None:
    """Drop every cached runtime (test isolation / shutdown hook)."""
    _runtime_cache.clear()


def _evict_stale_entries() -> None:
    """Evict entries older than the TTL (spec §6.1.2, H1-1)."""
    now = time.time()
    stale_keys = [key for key, entry in _runtime_cache.items() if now - entry.created_at > _RUNTIME_CACHE_TTL]
    for key in stale_keys:
        del _runtime_cache[key]


def _evict_oldest_if_full() -> None:
    """Evict the least recently accessed entry once the cache is at capacity."""
    if len(_runtime_cache) >= _RUNTIME_CACHE_MAX_SIZE:
        oldest_key = min(_runtime_cache, key=lambda k: _runtime_cache[k].last_accessed)
        del _runtime_cache[oldest_key]


def evict_runtime_cache(app_id: int, user_id: int) -> None:
    """Evict every cached runtime of one (app_id, user_id) pair (spec §6.1.2)."""
    for stale_key in [key for key in _runtime_cache if key[0] == app_id and key[1] == user_id]:
        del _runtime_cache[stale_key]


async def _load_agent_app(session: Session, app_id: int) -> AgentApp:
    """Load the AgentApp row by its integer primary key (G2 v3.3 §6.1.1).

    The ``"system-default"`` placeholder / NULL resolution is gone: callers
    pass the real ``AgentApp.id`` int (legacy session rows were already
    backfilled to the concrete id).

    Args:
        session: SQLModel database session.
        app_id: AgentApp integer primary key.

    Returns:
        The AgentApp row (status NOT checked here).

    Raises:
        ValueError: When the row does not exist.
    """
    app_cfg = session.get(AgentApp, app_id)
    if app_cfg is None:
        raise ValueError(f"agent app {app_id} not found")
    return app_cfg


def _load_subagents(session: Session, names: Sequence[str]) -> list[SubAgentConfig]:
    """Load the SubAgentConfig rows bound to an app, ordered by name."""
    if not names:
        return []
    statement = select(SubAgentConfig).where(col(SubAgentConfig.name).in_(names)).order_by(col(SubAgentConfig.name))
    return list(session.exec(statement).all())


async def _load_skill_hashes(
    session: Session,
    names: Sequence[str],
    subagent_cfgs: Sequence[SubAgentConfig] = (),
) -> dict[str, str]:
    """Map bound skill names to their persisted content hashes.

    The fingerprint input must cover the union of ``app_cfg.skill_names`` and
    every bound subagent's explicit ``skill_names`` (the inherited/None case
    contributes nothing because the sub-agent resolves to the app's set at
    compile time, which is already covered by ``app_cfg.skill_names``).
    Missing subagent-only skills in this projection would silently skip the
    recompile that should fire when a subagent-only skill's body changes.

    Args:
        session: SQLModel database session.
        names: Skill names from the parent AgentApp ``skill_names``.
        subagent_cfgs: Bound SubAgentConfig rows whose explicit
            ``skill_names`` contribute to the fingerprint.

    Returns:
        Mapping of skill name -> content hash for every name listed.
    """
    extra = {n for cfg in subagent_cfgs for n in (cfg.skill_names or [])}
    lookup = sorted(set(names) | extra)
    if not lookup:
        return {}
    assets = session.exec(select(SkillAsset).where(col(SkillAsset.name).in_(lookup))).all()
    return {asset.name: asset.content_hash for asset in assets}


async def _load_mcp_fingerprint(session: Session) -> str:
    """Fingerprint the enabled MCP server set (same shape as mcp_manager's)."""
    servers = load_mcp_servers(session)
    return "|".join(sorted(f"{server.name}:{server.content_hash}" for server in servers))


async def _load_model_fingerprint(
    session: Session, app_cfg: AgentApp, subagent_cfgs: Sequence[SubAgentConfig]
) -> tuple[str, str]:
    """Fingerprint the provider/model pairs referenced by the app and its subagents.

    Every NULL ``model`` reference resolves to the default pair, so the
    reference set always contains it. A missing, deleted or disabled pair
    fails fast so a broken model configuration never reaches the compile
    path.

    Args:
        session: SQLModel database session.
        app_cfg: The AgentApp configuration row.
        subagent_cfgs: Bound SubAgentConfig rows.

    Returns:
        A tuple of ``ref:content_hash`` pairs of the referenced model
        configs (sorted and pipe-joined, same shape as
        ``_load_mcp_fingerprint``) and the resolved upstream model id of the
        app-level pair (``app_cfg.model`` reference or the default pair).

    Raises:
        ValueError: When any referenced provider/model pair is missing,
            deleted, disabled or malformed.
    """
    refs = {app_cfg.model or DEFAULT_MODEL_REF}
    refs.update(cfg.model or DEFAULT_MODEL_REF for cfg in subagent_cfgs)

    pairs: dict[str, tuple[Provider, ModelConfig]] = {}
    broken: list[str] = []
    for ref in sorted(refs):
        try:
            provider_name, model_name = parse_model_ref(ref)
        except ValueError:
            broken.append(ref)
            continue
        provider = session.exec(
            select(Provider).where(col(Provider.name) == provider_name, col(Provider.deleted) == False)  # noqa: E712
        ).first()
        model = (
            session.exec(
                select(ModelConfig).where(
                    col(ModelConfig.provider_id) == provider.id,
                    col(ModelConfig.name) == model_name,
                    col(ModelConfig.deleted) == False,  # noqa: E712
                )
            ).first()
            if provider is not None
            else None
        )
        if provider is None or model is None or not provider.enabled or not model.enabled:
            broken.append(ref)
            continue
        pairs[ref] = (provider, model)

    if broken:
        raise ValueError(
            f"agent app {app_cfg.name!r} references missing or disabled model config(s): {', '.join(broken)}"
        )

    fingerprint = "|".join(
        sorted(f"{ref}:{compute_model_config_hash(provider, model)}" for ref, (provider, model) in pairs.items())
    )
    app_model = pairs[app_cfg.model or DEFAULT_MODEL_REF][1]
    return fingerprint, app_model.model_id


async def _build_checkpointer() -> BaseCheckpointSaver | None:
    """Build an AsyncPostgresSaver over the shared pool, or None when unavailable.

    The checkpoint DDL (``setup()``) runs at most once per process under an
    asyncio lock, so concurrent first-compiles (multi-worker startup) never
    race on table creation.
    """
    global _checkpointer_setup_done  # noqa: PLW0603 — process-level one-shot DDL flag
    pool = await get_shared_connection_pool()
    if pool is None:
        return None
    checkpointer = AsyncPostgresSaver(pool)
    if not _checkpointer_setup_done:
        async with _checkpointer_setup_lock:
            if not _checkpointer_setup_done:
                await checkpointer.setup()
                _checkpointer_setup_done = True
    return checkpointer


async def delete_thread_checkpoint(session_id: str) -> None:
    """Delete every checkpoint of one thread WITHOUT requiring an AgentApp.

    G3 §11.5.1: unlike ``clear_chat_history`` (an instance method bound to a
    loaded app config), this module-level helper only depends on the shared
    connection pool, so it stays callable after the app was deleted or
    unpublished. A unavailable pool (``None``) skips the cleanup with a
    warning instead of blocking the cascade.
    """
    checkpointer = await _build_checkpointer()
    if checkpointer is None:
        logger.warning("session_cascade_checkpoint_skipped_no_pool", session_id=session_id)
        return
    await checkpointer.adelete_thread(session_id)


async def get_runtime(session: Session, app_id: int, *, user_id: int) -> AgentAppRuntime:
    """Load, validate and return the AgentApp runtime for one user.

    G2 v3.3 (spec-g2-workspace §6.1.1): the runtime is isolated per
    ``(app_id, user_id)``. Every call first runs the lazy user-layer
    validation (``agent_apps_service.ensure_user_workspace_up_to_date``,
    refilling the nested User workspace), then resolves the 5-input config
    fingerprint and serves the ``(app_id, user_id, fingerprint)`` triple from
    the TTL+LRU bounded cache (§6.1.2).

    Args:
        session: SQLModel database session.
        app_id: AgentApp integer primary key (no placeholder resolution).
        user_id: User whose nested workspace backs the skills backend.

    Returns:
        The cached or freshly built AgentAppRuntime.

    Raises:
        ValueError: When the app is missing, not published, its engine is
            unknown (anything other than ``deepagents``/``workflow``), or a
            referenced model config is missing/disabled.
    """
    # Local import: agent_apps_service imports this module at top level.
    from app.services.agents import agent_apps_service

    app_cfg = await _load_agent_app(session, app_id)
    if app_cfg.status != "published":
        raise ValueError(f"agent app {app_cfg.name!r} is not published (status={app_cfg.status})")

    # Lazy user-layer validation (D21): refill the nested User workspace
    # before any compile reads it; silently no-ops for unassociated users.
    await agent_apps_service.ensure_user_workspace_up_to_date(session, user_id=user_id, app_id=app_id)

    subagent_cfgs = _load_subagents(session, app_cfg.subagent_names)
    skill_hashes = await _load_skill_hashes(session, app_cfg.skill_names, subagent_cfgs)
    mcp_fingerprint = await _load_mcp_fingerprint(session)
    model_fingerprint, resolved_model_name = await _load_model_fingerprint(session, app_cfg, subagent_cfgs)
    fingerprint = assembly.compute_fingerprint(
        app_cfg, subagent_cfgs, skill_hashes, mcp_fingerprint, model_fingerprint
    )

    cache_key = (app_id, user_id, fingerprint)
    cached = _runtime_cache.get(cache_key)
    if cached is not None:
        # Refresh the LRU timestamp on every hit (spec §6.1.2).
        cached.last_accessed = time.time()
        logger.debug("agent_app_runtime_cache_hit", app_name=app_cfg.name, app_id=app_id, user_id=user_id)
        return cached.runtime

    checkpointer = await _build_checkpointer()

    degraded = False
    if app_cfg.engine == "deepagents":
        # HIL degradation (T10): without a checkpointer interrupts cannot be
        # persisted/resumed, so compile a copy of the config without interrupt_on.
        compile_cfg = app_cfg
        if checkpointer is None and app_cfg.interrupt_on:
            logger.warning("hil_disabled_no_checkpointer", app_name=app_cfg.name, app_id=app_cfg.id)
            compile_cfg = app_cfg.model_copy(update={"interrupt_on": {}})
            degraded = True
        graph = await assembly.get_or_compile(
            session,
            compile_cfg,
            subagent_cfgs=subagent_cfgs,
            skill_hashes=skill_hashes,
            mcp_fingerprint=mcp_fingerprint,
            model_fingerprint=model_fingerprint,
            user_id=user_id,
            checkpointer=checkpointer,
        )
        runtime_obj: AgentAppRuntime = DeepAgentsAppRuntime(
            app_cfg=compile_cfg,
            graph=graph,
            checkpointer=checkpointer,
            resolved_model_name=resolved_model_name,
        )
    elif app_cfg.engine == "workflow":
        runtime_obj = WorkflowAppRuntime(app_cfg=app_cfg, resolved_model_name=resolved_model_name)
    else:
        raise ValueError(f"unknown engine {app_cfg.engine!r} for agent app {app_cfg.name!r}")

    if degraded:
        # A checkpointer-less degraded runtime must not pollute the cache: once
        # the shared pool recovers the next request rebuilds the HIL runtime.
        logger.debug("agent_app_runtime_not_cached_degraded", app_name=app_cfg.name, app_id=app_cfg.id)
        return runtime_obj

    _evict_stale_entries()
    _evict_oldest_if_full()
    now = time.time()
    _runtime_cache[cache_key] = _CacheEntry(runtime=runtime_obj, created_at=now, last_accessed=now)

    # Evict stale fingerprints of the same (app_id, user_id) pair (D22):
    # the cache keeps one runtime per user per app.
    for stale_key in [key for key in _runtime_cache if key[0] == app_id and key[1] == user_id and key != cache_key]:
        del _runtime_cache[stale_key]
    logger.info(
        "agent_app_runtime_ready", app_name=app_cfg.name, app_id=app_cfg.id, user_id=user_id, engine=app_cfg.engine
    )
    return runtime_obj
