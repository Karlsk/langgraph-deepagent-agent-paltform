"""G2 runtime cache contracts end-to-end (spec v3.3 §8.2.2).

Zero real LLM / zero real MCP by construction (conftest seams). The cache
under test is the process-level triple-key store of ``runtime.py``
(``(app_id, user_id, fingerprint)``); every scenario drives it through the
real ``get_runtime`` entry point against the in-memory DB.
"""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession

from app.core.config import settings
from app.models.user import User
from app.services.agents import runtime as runtime_module
from tests.conftest import unwrap

pytestmark = pytest.mark.integration

API = settings.API_V1_STR


def _seed_second_user(db_engine: Any) -> User:
    """Register a second user directly in the in-memory database."""
    second = User(
        email="bob@example.com",
        hashed_password=User.hash_password("Passw0rd!Strong"),
        username="bob",
    )
    with DBSession(db_engine) as session:
        session.add(second)
        session.commit()
        session.refresh(second)
    return second


def _publish_associated_app(
    client: TestClient, headers: dict[str, str], user_ids: list[int]
) -> int:
    """Create + publish one skill-bound app and associate every listed user."""
    skill = client.post(
        f"{API}/skills",
        json={"name": "cache-style", "description": "Style", "body": "# cache-style\n\nv1\n"},
        headers=headers,
    )
    assert skill.status_code == 201, skill.text
    app = client.post(
        f"{API}/apps",
        json={"name": "cache-app", "system_prompt": "You are cache.", "skill_names": ["cache-style"]},
        headers=headers,
    )
    assert app.status_code == 201, app.text
    app_id = unwrap(app, expected_code=201)["id"]
    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200
    for user_id in user_ids:
        assert client.post(f"{API}/apps/{app_id}/associate-user/{user_id}", headers=headers).status_code == 200
    return app_id


def test_cache_separated_by_user(
    client: TestClient,
    user: User,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """Two users of one app get distinct runtimes under distinct triple keys."""
    headers = user_headers
    second_user = _seed_second_user(db_engine)
    app_id = _publish_associated_app(client, headers, [user.id, second_user.id])

    with DBSession(db_engine) as session:
        alice_runtime = asyncio.run(runtime_module.get_runtime(session, app_id, user_id=user.id))
        bob_runtime = asyncio.run(runtime_module.get_runtime(session, app_id, user_id=second_user.id))

    assert alice_runtime is not bob_runtime
    keys = list(runtime_module._runtime_cache)  # noqa: SLF001 — cache introspection
    assert {key[:2] for key in keys} == {(app_id, user.id), (app_id, second_user.id)}
    assert all(key[2] for key in keys)  # every entry carries a fingerprint


def test_cache_evicted_on_workspace_change(
    client: TestClient,
    user: User,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """A workspace change rebuilds the runtime and evicts the stale fingerprint."""
    headers = user_headers
    app_id = _publish_associated_app(client, headers, [user.id])

    with DBSession(db_engine) as session:
        first_runtime = asyncio.run(runtime_module.get_runtime(session, app_id, user_id=user.id))
    assert len(runtime_module._runtime_cache) == 1  # noqa: SLF001

    # Skill edit + republish change the workspace hash (a fingerprint input).
    assert client.patch(f"{API}/skills/cache-style", json={"body": "# cache-style\n\nv2\n"}, headers=headers).status_code == 200
    assert client.post(f"{API}/apps/{app_id}/publish", headers=headers).status_code == 200

    with DBSession(db_engine) as session:
        second_runtime = asyncio.run(runtime_module.get_runtime(session, app_id, user_id=user.id))

    assert second_runtime is not first_runtime
    keys = list(runtime_module._runtime_cache)  # noqa: SLF001 — stale entry evicted
    assert len(keys) == 1
    assert keys[0][:2] == (app_id, user.id)


def test_get_runtime_concurrent_safety(
    client: TestClient,
    user: User,
    user_headers: dict[str, str],
    scripted_model: Any,
    memory_checkpointer: Any,
    db_engine: Any,
) -> None:
    """Concurrent get_runtime calls converge on one healthy cached runtime."""
    headers = user_headers
    app_id = _publish_associated_app(client, headers, [user.id])

    async def _four_parallel_calls() -> list[Any]:
        with DBSession(db_engine) as session:
            return list(await asyncio.gather(*(runtime_module.get_runtime(session, app_id, user_id=user.id) for _ in range(4))))

    runtimes = asyncio.run(_four_parallel_calls())
    assert all(runtime is not None for runtime in runtimes)

    # The cache settles on a single triple key; the next call is a pure hit.
    keys = list(runtime_module._runtime_cache)  # noqa: SLF001 — cache introspection
    assert len(keys) == 1
    with DBSession(db_engine) as session:
        follow_up = asyncio.run(runtime_module.get_runtime(session, app_id, user_id=user.id))
    assert follow_up is runtime_module._runtime_cache[keys[0]].runtime  # noqa: SLF001
