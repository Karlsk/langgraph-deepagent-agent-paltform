"""HTTP 节点 URL 校验测试（spec-20）。.

覆盖：
- validate_http_url() 纯函数：scheme 拦截 + host 缺失 + 白名单 + IP 段校验（条件）
- 注册期校验：PUT 含非法 url（mock 关闭）→ 422
- 注册期豁免：url 含未解析 `{占位符}`（模板）→ 跳过，由执行期渲染后校验兜住
- 执行期校验：http 节点请求前二次校验（defense in depth）
- mock_enabled=true 豁免（零网络，S9）
- allow_private_networks 字段：默认 True（允许内网），False 时启用 IP 段校验
"""
from unittest.mock import patch

import pytest
import httpx
from fastapi.testclient import TestClient

from app.core.config import settings
from app.workflow.models import WorkflowValidationError
from app.workflow.security import validate_http_url


pytestmark = pytest.mark.unit


# ─── spec-20 §4: 拦截规则 ─────────────────────────────────────────────

class TestSchemeValidation:
    """scheme 仅允许 http/https."""

    def test_file_scheme_rejected(self):
        """file:// scheme 拒绝."""
        with pytest.raises(WorkflowValidationError, match="scheme"):
            validate_http_url("file:///etc/passwd")

    def test_gopher_scheme_rejected(self):
        """gopher:// scheme 拒绝."""
        with pytest.raises(WorkflowValidationError, match="scheme"):
            validate_http_url("gopher://evil.com/api")

    def test_ftp_scheme_rejected(self):
        """ftp:// scheme 拒绝."""
        with pytest.raises(WorkflowValidationError, match="scheme"):
            validate_http_url("ftp://example.com/file")


class TestHostValidation:
    """host 必须存在."""

    def test_missing_host_rejected(self):
        """URL 缺少 host 拒绝."""
        with pytest.raises(WorkflowValidationError, match="missing host"):
            validate_http_url("http:///path/only")


class TestValidUrls:
    """合法 URL 通过."""

    def test_https_example_com(self):
        """https://example.com 通过."""
        validate_http_url("https://example.com/api")

    def test_http_example_com(self):
        """http://example.com 通过."""
        validate_http_url("http://example.com/api")

    def test_https_subdomain(self):
        """https://api.example.com 子域名通过."""
        validate_http_url("https://api.example.com/v1")


class TestPrivateNetworks:
    """allow_private_networks 控制 IP 段校验."""

    def test_private_ip_allowed_by_default(self):
        """默认 allow_private_networks=True，私网 IP 允许."""
        validate_http_url("http://192.168.1.1/api")

    def test_loopback_allowed_by_default(self):
        """默认 allow_private_networks=True，环回地址允许."""
        validate_http_url("http://127.0.0.1/api")

    def test_link_local_allowed_by_default(self):
        """默认 allow_private_networks=True，链路本地地址允许."""
        validate_http_url("http://169.254.169.254/latest/meta-data")

    def test_private_ip_blocked_when_disabled(self, monkeypatch):
        """allow_private_networks=False 时，私网 IP 拒绝."""
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("192.168.1.1", 0))],
        )
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            validate_http_url("http://192.168.1.1/api", allow_private_networks=False)

    def test_loopback_blocked_when_disabled(self, monkeypatch):
        """allow_private_networks=False 时，环回地址拒绝."""
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("127.0.0.1", 0))],
        )
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            validate_http_url("http://127.0.0.1/api", allow_private_networks=False)

    def test_link_local_blocked_when_disabled(self, monkeypatch):
        """allow_private_networks=False 时，链路本地地址拒绝."""
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("169.254.169.254", 0))],
        )
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved|link-local"):
            validate_http_url("http://169.254.169.254/latest/meta-data", allow_private_networks=False)

    def test_public_ip_allowed_when_disabled(self, monkeypatch):
        """allow_private_networks=False 时，公网 IP 允许."""
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        validate_http_url("https://example.com/api", allow_private_networks=False)


class TestHostAllowlist:
    """WORKFLOW_HTTP_ALLOWED_HOSTS 白名单模式."""

    def test_empty_allowlist_allows_all(self):
        """空白名单（默认）允许所有 host."""
        with patch.object(settings, "WORKFLOW_HTTP_ALLOWED_HOSTS", []):
            validate_http_url("https://example.com/api")

    def test_nonempty_allowlist_permits_listed_host(self):
        """非空白名单允许名单内 host."""
        with patch.object(settings, "WORKFLOW_HTTP_ALLOWED_HOSTS", ["api.example.com"]):
            validate_http_url("https://api.example.com/v1")

    def test_nonempty_allowlist_blocks_unlisted_host(self):
        """非空白名单拒绝名单外 host."""
        with patch.object(settings, "WORKFLOW_HTTP_ALLOWED_HOSTS", ["api.example.com"]):
            with pytest.raises(WorkflowValidationError, match="not in allowed hosts"):
                validate_http_url("https://other.com/api")


# ─── 注册期校验（spec-16 链） ──────────────────────────────────────────

class TestRegistrationTimeValidation:
    """PUT 注册时校验 http 节点 url."""

    def test_put_with_invalid_scheme_rejected_422(self, client):
        """PUT 含非法 scheme（mock 关闭）→ 422."""
        payload = {
            "workflow_id": "ssrf_test",
            "nodes": [
                {
                    "name": "fetch",
                    "type": "http",
                    "config": {
                        "url": "file:///etc/passwd",
                        "method": "GET",
                        "mock_enabled": False,
                    },
                }
            ],
            "entry_point": "fetch",
            "state_schema": {"input": {"type": "str", "description": "user input"}},
        }
        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.put("/workflows/ssrf_test", json=payload)
        assert response.status_code == 422
        assert "scheme" in response.json()["message"].lower()

    def test_put_with_private_url_allowed_by_default(self, client):
        """PUT 含私网 url（默认 allow_private_networks=True）→ 通过."""
        payload = {
            "workflow_id": "private_test",
            "nodes": [
                {
                    "name": "fetch",
                    "type": "http",
                    "config": {
                        "url": "http://192.168.1.1/api",
                        "method": "GET",
                        "mock_enabled": False,
                    },
                }
            ],
            "entry_point": "fetch",
            "state_schema": {"input": {"type": "str", "description": "user input"}},
        }
        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.put("/workflows/private_test", json=payload)
        assert response.status_code == 200

    def test_put_with_private_url_blocked_when_disabled(self, client, monkeypatch):
        """PUT 含私网 url + allow_private_networks=False → 422."""
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("192.168.1.1", 0))],
        )
        payload = {
            "workflow_id": "private_blocked_test",
            "nodes": [
                {
                    "name": "fetch",
                    "type": "http",
                    "config": {
                        "url": "http://192.168.1.1/api",
                        "method": "GET",
                        "mock_enabled": False,
                    },
                }
            ],
            "entry_point": "fetch",
            "state_schema": {"input": {"type": "str", "description": "user input"}},
            "allow_private_networks": False,
        }
        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.put("/workflows/private_blocked_test", json=payload)
        assert response.status_code == 422
        assert "private/loopback" in response.json()["message"].lower()

    def test_put_with_mock_enabled_true_skips_validation(self, client):
        """PUT 含非法 url 但 mock_enabled=true → 通过（零网络，S9）."""
        payload = {
            "workflow_id": "mock_test",
            "nodes": [
                {
                    "name": "fetch",
                    "type": "http",
                    "config": {
                        "url": "file:///etc/passwd",
                        "method": "GET",
                        "mock_enabled": True,
                        "mock_responses": {
                            "GET file:///etc/passwd": '{"data": "mocked"}'
                        },
                    },
                }
            ],
            "entry_point": "fetch",
            "state_schema": {"input": {"type": "str", "description": "user input"}},
        }
        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.put("/workflows/mock_test", json=payload)
        assert response.status_code == 200

    def test_put_with_public_url_allowed(self, client):
        """PUT 含公网 url（mock 关闭）→ 通过."""
        payload = {
            "workflow_id": "public_test",
            "nodes": [
                {
                    "name": "fetch",
                    "type": "http",
                    "config": {
                        "url": "https://api.example.com/v1",
                        "method": "GET",
                        "mock_enabled": False,
                    },
                }
            ],
            "entry_point": "fetch",
            "state_schema": {"input": {"type": "str", "description": "user input"}},
        }
        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            response = client.put("/workflows/public_test", json=payload)
        assert response.status_code == 200

    @pytest.mark.parametrize(
        ("workflow_id", "url"),
        [
            ("tpl_host", "{sdn_base_url}/oauth/token"),
            ("tpl_path", "https://unresolvable.invalid/{version}/v1"),
            ("tpl_query", "https://unresolvable.invalid/api?pageNumber={page}"),
        ],
    )
    def test_put_with_template_url_skips_registration_validation(self, client, workflow_id, url):
        """含 `{占位符}` 的 url 是模板：host 要到运行期才知道，注册期无从校验（spec-20 修订）."""
        payload = {
            "workflow_id": workflow_id,
            "nodes": [
                {
                    "name": "fetch",
                    "type": "http",
                    "config": {"url": url, "method": "GET", "mock_enabled": False},
                }
            ],
            "entry_point": "fetch",
            "state_schema": {"input": {"type": "str", "description": "user input"}},
        }
        with patch.object(settings, "WORKFLOW_ADMIN_USERNAMES", ["admin_user"]):
            with patch("app.workflow.api.validate_http_url") as spy:
                response = client.put(f"/workflows/{workflow_id}", json=payload)
        assert response.status_code == 200
        spy.assert_not_called()


# ─── 执行期校验（defense in depth） ────────────────────────────────────

class TestExecutionTimeValidation:
    """执行期二次校验（即使绕过注册）."""

    def test_execution_blocks_invalid_scheme(self):
        """执行期请求前校验拦截非法 scheme."""
        from app.workflow.nodes.http_node import HTTPNode

        node = HTTPNode(
            name="fetch",
            config={
                "url": "file:///etc/passwd",
                "method": "GET",
                "mock_enabled": False,
            },
        )
        with pytest.raises(WorkflowValidationError, match="scheme"):
            node.build_runnable().invoke({})

    def test_execution_allows_private_ip_by_default(self):
        """执行期默认允许私网 IP（allow_private_networks=True）."""
        from app.workflow.nodes.http_node import HTTPNode

        def mock_request(method, url, headers=None, json=None, timeout=None, verify=None):
            request = httpx.Request(method, url, headers=headers)
            return httpx.Response(200, json={"data": "ok"}, request=request)

        with patch("app.workflow.nodes.http_node.httpx.request", mock_request):
            node = HTTPNode(
                name="fetch",
                config={
                    "url": "http://192.168.1.1/api",
                    "method": "GET",
                    "mock_enabled": False,
                },
            )
            result = node.build_runnable().invoke({})
            assert result["fetch_result"]["response"] == {"data": "ok"}
            assert result["fetch_result"]["status_code"] == 200
            assert result["fetch_result"]["url"] == "http://192.168.1.1/api"

    def test_execution_blocks_private_ip_when_disabled(self, monkeypatch):
        """执行期 allow_private_networks=False 时拦截私网 IP."""
        from app.workflow.nodes.http_node import HTTPNode

        monkeypatch.setattr(
            "app.workflow.security.socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("192.168.1.1", 0))],
        )
        node = HTTPNode(
            name="fetch",
            config={
                "url": "http://192.168.1.1/api",
                "method": "GET",
                "mock_enabled": False,
                "allow_private_networks": False,
            },
        )
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            node.build_runnable().invoke({})

    def test_execution_allows_public_url(self):
        """执行期允许公网地址（mock httpx 避免真实网络）."""
        from app.workflow.nodes.http_node import HTTPNode

        def mock_request(method, url, headers=None, json=None, timeout=None, verify=None):
            request = httpx.Request(method, url, headers=headers)
            return httpx.Response(200, json={"data": "ok"}, request=request)

        with patch("app.workflow.nodes.http_node.httpx.request", mock_request):
            node = HTTPNode(
                name="fetch",
                config={
                    "url": "https://api.example.com/v1",
                    "method": "GET",
                    "mock_enabled": False,
                },
            )
            result = node.build_runnable().invoke({})
            assert result["fetch_result"]["response"] == {"data": "ok"}
            assert result["fetch_result"]["status_code"] == 200
            assert result["fetch_result"]["url"] == "https://api.example.com/v1"


# ─── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI TestClient（最小化应用，带 admin 鉴权覆盖）。."""
    from fastapi import FastAPI
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from app.workflow import api as workflow_api
    from app.workflow.cli import build_registry
    from app.api.v1.auth import get_current_user
    from unittest.mock import MagicMock

    # Mock save_definition_yaml 写入 tmp_path 而非共享的 user 目录，防止测试污染
    def mock_save_definition(definition):
        import yaml
        path = tmp_path / f"{definition.workflow_id}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(definition.model_dump(exclude={"execution_history"}), f, allow_unicode=True, sort_keys=False)
        return path

    monkeypatch.setattr("app.workflow.api.save_definition_yaml", mock_save_definition)

    # 创建最小化 FastAPI 应用
    app = FastAPI()
    app.state.limiter = workflow_api.limiter
    app.state.workflow_registry = build_registry(tmp_path)
    app.state.workflow_directory = tmp_path
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(workflow_api.router)

    # 覆盖 admin 鉴权依赖
    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.username = "admin_user"
    mock_user.email = "admin@test.com"

    async def mock_get_current_user():
        return mock_user

    app.dependency_overrides[get_current_user] = mock_get_current_user

    with TestClient(app) as test_client:
        yield test_client
