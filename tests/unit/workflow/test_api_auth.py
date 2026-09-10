"""Unit tests for workflow API authentication (spec-19).

Tests the auth dependency matrix:
- GET/POST endpoints: require get_current_user (any logged-in user)
- PUT/DELETE endpoints: require require_workflow_admin (admin allowlist)
- GET /workflows/capabilities: returns {can_edit: bool} based on allowlist
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.workflow import api as workflow_api
from app.workflow.cli import build_registry
from tests.unit.workflow.test_cli import _ECHO_YAML

pytestmark = pytest.mark.unit


def _make_user(user_id: int, username: str | None) -> MagicMock:
    """Create a mock User with the given id and username."""
    user = MagicMock()
    user.id = user_id
    user.username = username
    user.email = f"user{user_id}@example.com"
    return user


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Minimal FastAPI app hosting the workflow router with auth dependencies."""
    (tmp_path / "echo_demo.yaml").write_text(_ECHO_YAML, encoding="utf-8")
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.state.workflow_directory = tmp_path
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)
    with TestClient(app) as test_client:
        yield test_client


_SAVE_PAYLOAD = {
    "workflow_id": "echo_demo",
    "entry_point": "step_one",
    "nodes": [{"name": "step_one", "type": "echo", "config": {"output": {"result": "ok"}}}],
    "edges": [{"source": "step_one", "target": "END"}],
    "state_schema": {"input": {"type": "str", "description": "input"}},
}


class TestWriteEndpointAuth:
    """PUT/DELETE require require_workflow_admin (admin allowlist)."""

    def test_put_without_auth_returns_403(self, client: TestClient) -> None:
        """PUT without any auth token → 403 (HTTPBearer default)."""
        response = client.put("/workflows/echo_demo", json=_SAVE_PAYLOAD)
        assert response.status_code == 403

    def test_delete_without_auth_returns_403(self, client: TestClient) -> None:
        """DELETE without any auth token → 403 (HTTPBearer default)."""
        response = client.delete("/workflows/echo_demo")
        assert response.status_code == 403

    def test_put_non_admin_returns_403(self, client: TestClient) -> None:
        """PUT by non-admin user → 403."""
        from app.api.v1.auth import get_current_user
        from app.core.config import settings

        regular_user = _make_user(1, "regular_user")

        async def mock_get_current_user() -> MagicMock:
            return regular_user

        client.app.dependency_overrides[get_current_user] = mock_get_current_user

        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.put("/workflows/echo_demo", json=_SAVE_PAYLOAD)

        assert response.status_code == 403

    def test_delete_non_admin_returns_403(self, client: TestClient) -> None:
        """DELETE by non-admin user → 403."""
        from app.api.v1.auth import get_current_user
        from app.core.config import settings

        regular_user = _make_user(1, "regular_user")

        async def mock_get_current_user() -> MagicMock:
            return regular_user

        client.app.dependency_overrides[get_current_user] = mock_get_current_user

        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.delete("/workflows/echo_demo")

        assert response.status_code == 403

    def test_put_admin_passes_through(self, client: TestClient) -> None:
        """PUT by admin user → passes auth (enters spec-16 logic)."""
        from app.api.v1.auth import get_current_user
        from app.core.config import settings

        admin_user = _make_user(1, "admin_user")

        async def mock_get_current_user() -> MagicMock:
            return admin_user

        client.app.dependency_overrides[get_current_user] = mock_get_current_user

        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.put("/workflows/echo_demo", json=_SAVE_PAYLOAD)

        # Should pass auth and enter spec-16 logic (200 or 422, not 401/403)
        assert response.status_code in (200, 422)

    def test_delete_admin_passes_through(self, client: TestClient) -> None:
        """DELETE by admin user → passes auth (enters spec-18 logic)."""
        from app.api.v1.auth import get_current_user
        from app.core.config import settings

        admin_user = _make_user(1, "admin_user")

        async def mock_get_current_user() -> MagicMock:
            return admin_user

        client.app.dependency_overrides[get_current_user] = mock_get_current_user

        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.delete("/workflows/echo_demo")

        # Should pass auth and enter spec-18 logic (200 or 404, not 401/403)
        assert response.status_code in (200, 404)


class TestReadEndpointAuth:
    """GET/POST endpoints require get_current_user (any logged-in user)."""

    def test_list_without_auth_returns_403(self, client: TestClient) -> None:
        """GET /workflows without auth → 403 (HTTPBearer default)."""
        response = client.get("/workflows")
        assert response.status_code == 403

    def test_detail_without_auth_returns_403(self, client: TestClient) -> None:
        """GET /workflows/{id} without auth → 403 (HTTPBearer default)."""
        response = client.get("/workflows/echo_demo")
        assert response.status_code == 403

    def test_execute_without_auth_returns_403(self, client: TestClient) -> None:
        """POST /workflows/{id}/execute without auth → 403 (HTTPBearer default)."""
        response = client.post("/workflows/echo_demo/execute", json={"input": "test"})
        assert response.status_code == 403

    def test_list_with_any_user_passes(self, client: TestClient) -> None:
        """GET /workflows with any logged-in user → passes auth."""
        from app.api.v1.auth import get_current_user

        regular_user = _make_user(1, "regular_user")

        async def mock_get_current_user() -> MagicMock:
            return regular_user

        client.app.dependency_overrides[get_current_user] = mock_get_current_user

        response = client.get("/workflows")
        # Should pass auth (200, not 401)
        assert response.status_code == 200

    def test_execute_with_any_user_passes(self, client: TestClient) -> None:
        """POST /workflows/{id}/execute with any logged-in user → passes auth."""
        from app.api.v1.auth import get_current_user

        regular_user = _make_user(1, "regular_user")

        async def mock_get_current_user() -> MagicMock:
            return regular_user

        client.app.dependency_overrides[get_current_user] = mock_get_current_user

        response = client.post("/workflows/echo_demo/execute", json={"input": "test"})
        # Should pass auth (200, not 403)
        assert response.status_code == 200


class TestCapabilitiesEndpoint:
    """GET /workflows/capabilities returns {can_edit: bool}."""

    def test_capabilities_without_auth_returns_403(self, client: TestClient) -> None:
        """GET /workflows/capabilities without auth → 403 (HTTPBearer default)."""
        response = client.get("/workflows/capabilities")
        assert response.status_code == 403

    def test_capabilities_admin_returns_true(self, client: TestClient) -> None:
        """GET /workflows/capabilities by admin → {can_edit: true}."""
        from app.api.v1.auth import get_current_user
        from app.core.config import settings

        admin_user = _make_user(1, "admin_user")

        async def mock_get_current_user() -> MagicMock:
            return admin_user

        client.app.dependency_overrides[get_current_user] = mock_get_current_user

        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.get("/workflows/capabilities")

        assert response.status_code == 200
        envelope = response.json()
        assert envelope["code"] == 200
        assert envelope["data"]["can_edit"] is True

    def test_capabilities_non_admin_returns_false(self, client: TestClient) -> None:
        """GET /workflows/capabilities by non-admin → {can_edit: false}."""
        from app.api.v1.auth import get_current_user
        from app.core.config import settings

        regular_user = _make_user(1, "regular_user")

        async def mock_get_current_user() -> MagicMock:
            return regular_user

        client.app.dependency_overrides[get_current_user] = mock_get_current_user

        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.get("/workflows/capabilities")

        assert response.status_code == 200
        envelope = response.json()
        assert envelope["code"] == 200
        assert envelope["data"]["can_edit"] is False

    def test_capabilities_empty_allowlist_returns_false(self, client: TestClient) -> None:
        """GET /workflows/capabilities with empty allowlist → {can_edit: false}."""
        from app.api.v1.auth import get_current_user
        from app.core.config import settings

        any_user = _make_user(1, "any_user")

        async def mock_get_current_user() -> MagicMock:
            return any_user

        client.app.dependency_overrides[get_current_user] = mock_get_current_user

        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", []):
            response = client.get("/workflows/capabilities")

        assert response.status_code == 200
        envelope = response.json()
        assert envelope["data"]["can_edit"] is False


class TestH6NoHardcodedUsernames:
    """H6: allowlist comes from env/settings, no hardcoded usernames."""

    def test_no_hardcoded_usernames_in_workflow_module(self) -> None:
        """Grep guard: no hardcoded usernames in app/workflow/."""
        import inspect
        import re

        from app.workflow import api, auth

        api_source = inspect.getsource(api)
        auth_source = inspect.getsource(auth)

        # Look for common username patterns (excluding function/variable names)
        hardcoded_patterns = [
            r'["\']admin["\']',
            r'["\']root["\']',
            r'["\']superuser["\']',
        ]

        for pattern in hardcoded_patterns:
            assert not re.search(pattern, api_source), f"Found hardcoded username pattern in api.py: {pattern}"
            assert not re.search(pattern, auth_source), f"Found hardcoded username pattern in auth.py: {pattern}"
