"""G3 session CRUD integration tests (spec-g3-session §11.9/§9.3).

Full api_router under TestClient with real dependency resolution (JWT
auth path), in-memory SQLite, a scripted chat model and a shared
MemorySaver checkpointer — zero real PG, zero real LLM, zero MCP. The
runtime turns below drive the production L1 checkpoint + L2 JSONL hooks.
"""

import asyncio
import time
from collections.abc import Generator
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from sqlmodel import Session as DBSession

from app.models.agent_assets import AgentApp
from app.models.user import User
from app.schemas.chat import Message
from app.services.agents import assembly, runtime as runtime_module
from tests.unit.agents.test_runtime import ScriptedChatModel

pytestmark = pytest.mark.integration

API: str = "/api/v1"


@pytest.fixture(autouse=True)
def clean_process_caches() -> Generator[None, None, None]:
    """Isolate the compile + runtime caches between tests."""
    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()
    yield
    assembly.clear_compile_cache()
    runtime_module.clear_runtime_cache()


@pytest.fixture
def scripted_model(monkeypatch: pytest.MonkeyPatch) -> ScriptedChatModel:
    """Serve every chat-model construction from a scripted model."""
    model = ScriptedChatModel(responses=[AIMessage(content="default-reply")])
    monkeypatch.setattr(assembly, "build_chat_model", lambda provider, model_cfg: model)
    return model


@pytest.fixture
def memory_checkpointer(monkeypatch: pytest.MonkeyPatch) -> MemorySaver:
    """Attach a shared in-memory checkpointer to every runtime build."""
    saver = MemorySaver()

    async def fake_build_checkpointer() -> MemorySaver:
        return saver

    monkeypatch.setattr(runtime_module, "_build_checkpointer", fake_build_checkpointer)
    return saver


def _seed_published_app(db_engine: Any, name: str = "crud-app", **extra: Any) -> int:
    """Insert one published AgentApp row and return its id."""
    with DBSession(db_engine) as session:
        app = AgentApp(name=name, system_prompt="x", status="published", allowed_tools=[], **extra)
        session.add(app)
        session.commit()
        session.refresh(app)
        return int(app.id)


def _create_session(client: Any, headers: dict[str, str], app_id: int, name: str = "s") -> str:
    """POST /sessions and return the new session id."""
    resp = client.post(f"{API}/sessions", json={"agent_app_id": app_id, "name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["session_id"]


async def _invoke_and_drain(
    rt: Any, *, session_id: str, user_id: int, text: str
) -> list[Message]:
    """Run one ainvoke turn, then wait for the fire-and-forget L2 hook."""
    out = await rt.ainvoke(
        [Message(role="user", content=text)],
        session_id=session_id,
        user_id=str(user_id),
        username="alice",
    )
    deadline = time.monotonic() + 5.0
    while runtime_module._pending_tasks and time.monotonic() < deadline:  # noqa: SLF001 — test drain seam
        await asyncio.sleep(0.01)
    return out


def _get_runtime(db_engine: Any, app_id: int, user: User) -> Any:
    """Load the runtime on the test engine inside the running loop."""
    with DBSession(db_engine) as db:
        return asyncio.run(runtime_module.get_runtime(db, app_id, user_id=user.id))


def test_full_session_lifecycle(client: Any, user_headers: dict[str, str], db_engine: Any) -> None:
    """Login -> create -> list -> patch -> delete -> gone (§11.9)."""
    app_id = _seed_published_app(db_engine)

    created = client.post(
        f"{API}/sessions", json={"agent_app_id": app_id, "name": "life"}, headers=user_headers
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["data"]["session_id"]

    listed = client.get(f"{API}/sessions", headers=user_headers)
    assert listed.status_code == 200
    assert session_id in [item["session_id"] for item in listed.json()["data"]["items"]]

    patched = client.patch(
        f"{API}/sessions/{session_id}", json={"name": "renamed"}, headers=user_headers
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == "renamed"

    deleted = client.delete(f"{API}/sessions/{session_id}", headers=user_headers)
    assert deleted.status_code == 200
    assert deleted.json()["data"] is None

    assert client.get(f"{API}/sessions/{session_id}", headers=user_headers).status_code == 404


def test_concurrent_delete_idempotent(client: Any, user_headers: dict[str, str], db_engine: Any) -> None:
    """Second DELETE after the L0 row is gone returns 404 (§11.5.1)."""
    app_id = _seed_published_app(db_engine)
    session_id = _create_session(client, user_headers, app_id)

    assert client.delete(f"{API}/sessions/{session_id}", headers=user_headers).status_code == 200
    assert client.delete(f"{API}/sessions/{session_id}", headers=user_headers).status_code == 404


def test_delete_session_cascades_jsonl_file(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    user: User,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
) -> None:
    """After DELETE: L2 JSONL gone AND the checkpoint thread is cleared."""
    from app.services.agents import context_store

    app_id = _seed_published_app(db_engine)
    session_id = _create_session(client, user_headers, app_id)
    scripted_model.responses = [AIMessage(content="r1"), AIMessage(content="r2")]

    rt = _get_runtime(db_engine, app_id, user)
    asyncio.run(_invoke_and_drain(rt, session_id=session_id, user_id=user.id, text="q1"))
    asyncio.run(_invoke_and_drain(rt, session_id=session_id, user_id=user.id, text="q2"))

    l2 = context_store.session_file_path(app_id, user.id, session_id)
    assert l2.exists(), "runtime turns must have produced the L2 transcript"
    rows = asyncio.run(context_store.read_rows(l2))
    assert len(rows) == 4  # 2 turns x (user + assistant)

    assert client.delete(f"{API}/sessions/{session_id}", headers=user_headers).status_code == 200

    assert not l2.exists()
    config = {"configurable": {"thread_id": session_id}}
    assert asyncio.run(memory_checkpointer.aget_tuple(config)) is None


def test_message_count_reflects_langgraph_state(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    user: User,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
) -> None:
    """SessionRead.message_count matches the L2/L1 turn count (§11.9)."""
    app_id = _seed_published_app(db_engine)
    session_id = _create_session(client, user_headers, app_id)
    scripted_model.responses = [AIMessage(content="a1"), AIMessage(content="a2")]

    rt = _get_runtime(db_engine, app_id, user)
    asyncio.run(_invoke_and_drain(rt, session_id=session_id, user_id=user.id, text="m1"))
    asyncio.run(_invoke_and_drain(rt, session_id=session_id, user_id=user.id, text="m2"))

    detail = client.get(f"{API}/sessions/{session_id}", headers=user_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["message_count"] == 4


def test_delete_agent_app_cascades_sessions_and_checkpoints(
    client: Any,
    user_headers: dict[str, str],
    db_engine: Any,
    user: User,
    scripted_model: ScriptedChatModel,
    memory_checkpointer: MemorySaver,
) -> None:
    """Hard-deleting the app wipes Session rows, checkpoints and the dir (§11.5.2)."""
    from app.services.agents import skills_store

    app_id = _seed_published_app(db_engine, name="doom-app")
    session_id = _create_session(client, user_headers, app_id)
    scripted_model.responses = [AIMessage(content="before-doom")]

    rt = _get_runtime(db_engine, app_id, user)
    asyncio.run(_invoke_and_drain(rt, session_id=session_id, user_id=user.id, text="last message"))
    config = {"configurable": {"thread_id": session_id}}
    assert asyncio.run(memory_checkpointer.aget_tuple(config)) is not None
    agent_dir = skills_store._agent_dir(app_id)  # noqa: SLF001 — integration assert
    assert agent_dir.exists()

    resp = client.delete(f"{API}/apps/{app_id}", headers=user_headers)
    assert resp.status_code == 200, resp.text

    assert asyncio.run(memory_checkpointer.aget_tuple(config)) is None
    assert client.get(f"{API}/sessions/{session_id}", headers=user_headers).status_code == 404
    assert not agent_dir.exists()  # rmtree cascaded the L2 JSONL with it
