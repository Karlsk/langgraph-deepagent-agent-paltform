"""Integration tests for the Provider hard-delete escape hatch + trash views.

Scope:

- TC5: hard-delete (TC1-4 behavior under the full-stack TestClient).
  - physical-removal verification (raw SELECT no longer finds the row);
  - cascade into ``model_config`` and ``provider_health`` tables;
  - same-name recreate after hard-delete (uniqueness slot is freed).
- TC9: trash endpoint closed-loop integration (soft delete -> list -> get ->
  list-models -> hard delete -> same-name recreate -> soft delete again ->
  list shows the new tombstone).

These tests reuse the ``client`` / ``user_headers`` / ``db_engine`` fixtures
declared in ``tests/integration/api/conftest.py``.

Zero real network / zero real LLM by construction: the in-memory SQLite
engine is injected into ``database_service`` (and the auth module's private
``db_service``) for the lifetime of the test.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.core.config import settings
from app.models.provider import ModelConfig, Provider, ProviderHealth
from tests.conftest import unwrap

pytestmark = pytest.mark.integration

API = settings.API_V1_STR
HARD_DELETE_HEADER = {"X-Confirm-Hard-Delete": "true"}


def _management_headers(client: TestClient, user_headers: dict[str, str]) -> dict[str, str]:
    """Management APIs authenticate with the user access token directly.

    Phase 1 G1: the ``POST /auth/session`` exchange is retired; provider
    endpoints resolve ``get_current_user`` from the same user token.
    """
    del client  # kept for signature symmetry with the retired exchange flow
    return user_headers


def _create_provider(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str,
    type: str = "OPENAI",
    api_key: str = "sk-test-secret-1234567890",
) -> dict[str, Any]:
    """POST a provider and return the envelope data; the request body is built locally."""
    payload = {
        "name": name,
        "type": type,
        "base_url": "https://api.example.com/v1",
        "auth_config": {"api_key": api_key},
        "enabled": True,
    }
    response = client.post(f"{API}/providers", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return unwrap(response)


def _create_model(
    client: TestClient,
    headers: dict[str, str],
    *,
    provider_name: str,
    model_name: str = "gpt-test",
    model_id: str | None = None,
) -> dict[str, Any]:
    """POST a model under an existing provider.

    ``model_id`` defaults to ``model_name`` so two models under one provider
    get distinct identifiers without the caller having to think about it.
    """
    payload = {
        "name": model_name,
        "model_id": model_id if model_id is not None else model_name,
        "context_size": 8192,
        "extra_params": {"temperature": 0.7},
        "enabled": True,
    }
    response = client.post(f"{API}/providers/{provider_name}/models", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return unwrap(response)


def _hard_delete(client: TestClient, headers: dict[str, str], name: str) -> Any:
    """DELETE with hard=true + confirm header (single-shot physical purge)."""
    return client.request(
        "DELETE",
        f"{API}/providers/{name}",
        params={"hard": "true"},
        headers={**headers, **HARD_DELETE_HEADER},
    )


# ---------------------------------------------------------------------------
# TC5 — hard-delete integration (physical removal + same-name recreate)
# ---------------------------------------------------------------------------


def test_hard_delete_physically_removes_provider_row(
    client: TestClient,
    db_engine: Any,
    user_headers: dict[str, str],
) -> None:
    """Hard delete removes the active row from the provider table (raw SELECT empty).

    Asserts ``SELECT ... WHERE name = ...`` returns nothing after the call;
    the management API must physically purge the row, not soft-tombstone it.
    """
    headers = _management_headers(client, user_headers)
    _create_provider(client, headers, name="harddel-physical")

    response = _hard_delete(client, headers, "harddel-physical")
    assert response.status_code == 200, response.text

    with DBSession(db_engine) as session:
        rows = session.exec(select(Provider).where(col(Provider.name) == "harddel-physical")).all()
        assert rows == [], f"hard delete must physically purge rows, got: {rows!r}"


def test_hard_delete_cascades_models_and_health(
    client: TestClient,
    db_engine: Any,
    user_headers: dict[str, str],
) -> None:
    """Hard delete purges every ``model_config`` of the provider plus the health row."""
    headers = _management_headers(client, user_headers)
    _create_provider(client, headers, name="harddel-cascade")
    _create_model(client, headers, provider_name="harddel-cascade", model_name="m1")
    _create_model(client, headers, provider_name="harddel-cascade", model_name="m2")

    # Plant a ProviderHealth row to prove the cascade drops it too.
    with DBSession(db_engine) as session:
        provider = session.exec(select(Provider).where(col(Provider.name) == "harddel-cascade")).one()
        provider_id = provider.id
        session.add(ProviderHealth(provider_id=provider_id, status="UP", fail_count=0, latency_ms=120))
        session.commit()

    response = _hard_delete(client, headers, "harddel-cascade")
    assert response.status_code == 200, response.text

    with DBSession(db_engine) as session:
        # Provider + its models + its health row must all be gone.
        assert session.exec(select(Provider).where(col(Provider.name) == "harddel-cascade")).all() == []
        assert session.exec(select(ModelConfig).where(col(ModelConfig.provider_id) == provider_id)).all() == []
        assert session.exec(select(ProviderHealth).where(col(ProviderHealth.provider_id) == provider_id)).all() == []


def test_hard_delete_unblocks_same_name_recreate(
    client: TestClient,
    user_headers: dict[str, str],
) -> None:
    """Hard delete frees the unique-name slot; recreating the same name returns 201.

    This is the core escape-hatch loop: a soft-deleted provider blocks the
    unique-name constraint, so a recreate returns 422. The hard-delete path
    physically removes the row and frees the slot for the same name.
    """
    headers = _management_headers(client, user_headers)
    _create_provider(client, headers, name="harddel-recreate")

    # Recreate under the same name is blocked while the row exists (active
    # provider occupies the unique-name slot).
    blocked = client.post(
        f"{API}/providers",
        json={
            "name": "harddel-recreate",
            "type": "OPENAI",
            "base_url": "https://api.example.com/v1",
            "auth_config": {"api_key": "sk-blocked-1234567890"},
            "enabled": True,
        },
        headers=headers,
    )
    assert blocked.status_code == 422, blocked.text

    # Hard delete purges the row (no soft tombstone left behind).
    response = _hard_delete(client, headers, "harddel-recreate")
    assert response.status_code == 200, response.text

    # Slot is free; a fresh create succeeds with HTTP 201.
    fresh = _create_provider(client, headers, name="harddel-recreate")
    assert fresh["name"] == "harddel-recreate"
    assert "id" in fresh


# ---------------------------------------------------------------------------
# TC9 — trash endpoint closed-loop integration
# ---------------------------------------------------------------------------


def test_trash_list_reflects_soft_deleted_provider(
    client: TestClient,
    user_headers: dict[str, str],
) -> None:
    """Soft delete puts the row in the trash list; the live list excludes it."""
    headers = _management_headers(client, user_headers)
    _create_provider(client, headers, name="trash-list")

    response = client.delete(f"{API}/providers/trash-list", headers=headers)
    assert response.status_code == 200, response.text

    listed = unwrap(client.get(f"{API}/providers/deleted", headers=headers))
    names = [row["name"] for row in listed]
    assert "trash-list" in names

    # Live list must NOT include the soft-deleted row.
    live = unwrap(client.get(f"{API}/providers", headers=headers))
    assert "trash-list" not in [row["name"] for row in live]


def test_trash_get_returns_masked_payload(
    client: TestClient,
    user_headers: dict[str, str],
) -> None:
    """GET /providers/deleted/{name} returns the trash projection with masked auth."""
    headers = _management_headers(client, user_headers)
    _create_provider(
        client,
        headers,
        name="trash-get",
        api_key="sk-very-long-secret-1234567890",
    )
    response = client.delete(f"{API}/providers/trash-get", headers=headers)
    assert response.status_code == 200, response.text

    detail = unwrap(client.get(f"{API}/providers/deleted/trash-get", headers=headers))
    assert detail["name"] == "trash-get"
    assert detail["deleted"] is True
    # The raw auth_config payload must NEVER appear; only the masked form.
    assert "auth_config" not in detail
    assert detail["api_key_masked"].startswith("****")


def test_trash_models_lists_models_of_soft_deleted_provider(
    client: TestClient,
    user_headers: dict[str, str],
) -> None:
    """GET /providers/deleted/{name}/models returns the tombstoned provider's models."""
    headers = _management_headers(client, user_headers)
    _create_provider(client, headers, name="trash-models")
    _create_model(client, headers, provider_name="trash-models", model_name="alpha")
    _create_model(client, headers, provider_name="trash-models", model_name="beta")

    response = client.delete(f"{API}/providers/trash-models", headers=headers)
    assert response.status_code == 200, response.text

    listed = unwrap(client.get(f"{API}/providers/deleted/trash-models/models", headers=headers))
    model_names = sorted(row["name"] for row in listed)
    assert model_names == ["alpha", "beta"]
    assert all(row["deleted"] is True for row in listed)


def test_trash_models_404_when_provider_is_active(
    client: TestClient,
    user_headers: dict[str, str],
) -> None:
    """Trash models endpoint must NOT return rows for a live (non-soft-deleted) provider."""
    headers = _management_headers(client, user_headers)
    _create_provider(client, headers, name="trash-active")

    response = client.get(f"{API}/providers/deleted/trash-active/models", headers=headers)
    assert response.status_code == 404, response.text


def test_full_lifecycle_soft_delete_then_hard_delete_then_recreate(
    client: TestClient,
    db_engine: Any,
    user_headers: dict[str, str],
) -> None:
    """End-to-end closed loop: soft -> trash visible -> hard -> recreate -> trash reflects new."""
    headers = _management_headers(client, user_headers)
    _create_provider(
        client,
        headers,
        name="lifecycle",
        api_key="sk-lifecycle-1234567890abcdef",
    )
    _create_model(client, headers, provider_name="lifecycle", model_name="m1")

    # 1. Soft delete.
    response = client.delete(f"{API}/providers/lifecycle", headers=headers)
    assert response.status_code == 200, response.text

    # 2. Trash list shows it.
    names_after_soft = [row["name"] for row in unwrap(client.get(f"{API}/providers/deleted", headers=headers))]
    assert "lifecycle" in names_after_soft

    # 3. Hard delete the soft-deleted (tombstoned) row: the escape hatch must
    # reach the tombstone so the unique-name slot frees. The hard path falls
    # back to the trash row when no active row remains.
    response = _hard_delete(client, headers, "lifecycle")
    assert response.status_code == 200, response.text

    # 4. Trash list no longer shows it; recreate succeeds (201).
    names_after_hard = [row["name"] for row in unwrap(client.get(f"{API}/providers/deleted", headers=headers))]
    assert "lifecycle" not in names_after_hard

    recreated = client.post(
        f"{API}/providers",
        json={
            "name": "lifecycle",
            "type": "OPENAI",
            "base_url": "https://api.example.com/v1",
            "auth_config": {"api_key": "sk-recreate-1234567890"},
            "enabled": True,
        },
        headers=headers,
    )
    assert recreated.status_code == 201, recreated.text

    # 5. The raw row count is exactly 1 (the fresh active row); tombstone gone.
    with DBSession(db_engine) as session:
        rows = session.exec(select(Provider).where(col(Provider.name) == "lifecycle")).all()
        assert len(rows) == 1, f"expected 1 active row, got {len(rows)}"
        assert rows[0].deleted is False
        # Cascade purged the soft-deleted model rows as well.
        assert session.exec(select(ModelConfig).where(col(ModelConfig.provider_id) == rows[0].id)).all() == []


def test_hard_delete_tombstoned_provider_requires_confirm_header(
    client: TestClient,
    user_headers: dict[str, str],
) -> None:
    """Hard-deleting a tombstone still demands the confirm header (422 without it)."""
    headers = _management_headers(client, user_headers)
    _create_provider(client, headers, name="tomb-confirm")
    response = client.delete(f"{API}/providers/tomb-confirm", headers=headers)
    assert response.status_code == 200, response.text

    missing_header = client.request(
        "DELETE",
        f"{API}/providers/tomb-confirm",
        params={"hard": "true"},
        headers=headers,
    )
    assert missing_header.status_code == 422, missing_header.text

    # With the header the tombstone purges.
    purged = _hard_delete(client, headers, "tomb-confirm")
    assert purged.status_code == 200, purged.text
    trash = [row["name"] for row in unwrap(client.get(f"{API}/providers/deleted", headers=headers))]
    assert "tomb-confirm" not in trash


def test_hard_delete_model_under_soft_deleted_provider(
    client: TestClient,
    db_engine: Any,
    user_headers: dict[str, str],
) -> None:
    """Model-level escape hatch reaches tombstoned models under a tombstoned provider.

    The trash detail view lists models of a soft-deleted provider; hard-deleting
    one of those models must physically purge the row (not 404).
    """
    headers = _management_headers(client, user_headers)
    _create_provider(client, headers, name="tomb-models")
    _create_model(client, headers, provider_name="tomb-models", model_name="m-tomb")

    response = client.delete(f"{API}/providers/tomb-models", headers=headers)
    assert response.status_code == 200, response.text

    hard_model = client.request(
        "DELETE",
        f"{API}/providers/tomb-models/models/m-tomb",
        params={"hard": "true"},
        headers={**headers, **HARD_DELETE_HEADER},
    )
    assert hard_model.status_code == 200, hard_model.text

    with DBSession(db_engine) as session:
        provider_row = session.exec(select(Provider).where(col(Provider.name) == "tomb-models")).one()
        models = session.exec(select(ModelConfig).where(col(ModelConfig.provider_id) == provider_row.id)).all()
        assert models == [], "tombstoned model row must be physically purged"


def test_full_lifecycle_hard_delete_frees_unique_slot(
    client: TestClient,
    user_headers: dict[str, str],
) -> None:
    """Active-row hard delete closes the unique-name slot escape loop end-to-end.

    Sequence: create active row -> attempt same-name create (blocked 422) ->
    hard delete -> same-name recreate succeeds (201) -> trash list still empty.
    """
    headers = _management_headers(client, user_headers)
    _create_provider(client, headers, name="lifecycle2")

    blocked = client.post(
        f"{API}/providers",
        json={
            "name": "lifecycle2",
            "type": "OPENAI",
            "base_url": "https://api.example.com/v1",
            "auth_config": {"api_key": "sk-blocked-9876543210"},
            "enabled": True,
        },
        headers=headers,
    )
    assert blocked.status_code == 422, blocked.text

    response = _hard_delete(client, headers, "lifecycle2")
    assert response.status_code == 200, response.text

    fresh = _create_provider(client, headers, name="lifecycle2")
    assert fresh["name"] == "lifecycle2"

    # Trash list remains empty (no soft-deleted row).
    trash = unwrap(client.get(f"{API}/providers/deleted", headers=headers))
    assert "lifecycle2" not in [row["name"] for row in trash]
