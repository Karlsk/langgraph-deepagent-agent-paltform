"""HTTP 节点 SSRF 防护测试（spec-20）。.

覆盖：
- validate_http_url() 纯函数：私网/环回/链路本地/元数据/scheme 拦截
- 白名单模式：非空时仅允许名单内 host
- 注册期校验：PUT 含非法 url（mock 关闭）→ 422
- 执行期校验：http 节点请求前二次校验（defense in depth）
- mock_enabled=true 豁免（零网络，S9）
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

class TestPrivateNetworkRejection:
    """私网/环回/链路本地/元数据地址全部拒绝."""

    def test_loopback_ipv4_127(self):
        """127.0.0.0/8 环回段拒绝."""
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            validate_http_url("http://127.0.0.1/api")

    def test_loopback_ipv4_127_other(self):
        """127.x.x.x 其他环回地址拒绝."""
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            validate_http_url("http://127.0.0.2/api")

    def test_localhost_hostname(self):
        """Localhost 主机名拒绝（解析到 127.0.0.1）."""
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            validate_http_url("http://localhost/api")

    def test_private_10_network(self):
        """10.0.0.0/8 私网段拒绝."""
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            validate_http_url("http://10.0.0.1/api")

    def test_private_172_16_network(self):
        """172.16.0.0/12 私网段拒绝."""
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            validate_http_url("http://172.16.0.1/api")

    def test_private_192_168_network(self):
        """192.168.0.0/16 私网段拒绝."""
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            validate_http_url("http://192.168.1.1/api")

    def test_link_local_169_254(self):
        """169.254.0.0/16 链路本地段拒绝（含云元数据）."""
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved|link-local"):
            validate_http_url("http://169.254.169.254/latest/meta-data")

    def test_loopback_ipv6(self):
        """::1 IPv6 环回拒绝."""
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            validate_http_url("http://[::1]/api")

    def test_private_ipv6_fc00(self):
        """fc00::/7 IPv6 私网段拒绝."""
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
            validate_http_url("http://[fc00::1]/api")

    def test_link_local_ipv6_fe80(self):
        """fe80::/10 IPv6 链路本地段拒绝."""
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved|link-local"):
            validate_http_url("http://[fe80::1]/api")


class TestSchemeWhitelist:
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


class TestPublicNetworkAllowed:
    """公网地址通过."""

    def test_https_example_com(self, monkeypatch):
        """https://example.com 公网通过."""
        # Mock DNS 解析返回公网 IP
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        validate_http_url("https://example.com/api")  # 不抛异常即通过

    def test_http_example_com(self, monkeypatch):
        """http://example.com 公网通过."""
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        validate_http_url("http://example.com/api")

    def test_https_subdomain(self, monkeypatch):
        """https://api.example.com 子域名通过."""
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        validate_http_url("https://api.example.com/v1")


class TestHostAllowlist:
    """WORKFLOW_HTTP_ALLOWED_HOSTS 白名单模式."""

    def test_empty_allowlist_allows_public(self, monkeypatch):
        """空白名单（默认）允许所有公网 host."""
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        with patch.object(settings, "WORKFLOW_HTTP_ALLOWED_HOSTS", []):
            validate_http_url("https://example.com/api")  # 不抛异常

    def test_nonempty_allowlist_permits_listed_host(self, monkeypatch):
        """非空白名单允许名单内 host."""
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        with patch.object(settings, "WORKFLOW_HTTP_ALLOWED_HOSTS", ["api.example.com"]):
            validate_http_url("https://api.example.com/v1")  # 不抛异常

    def test_nonempty_allowlist_blocks_unlisted_public(self, monkeypatch):
        """非空白名单拒绝名单外公网 host."""
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *args: [(2, 1, 6, "", ("93.184.216.34", 0))],
        )
        with patch.object(settings, "WORKFLOW_HTTP_ALLOWED_HOSTS", ["api.example.com"]):
            with pytest.raises(WorkflowValidationError, match="not in allowed hosts"):
                validate_http_url("https://other.com/api")

    def test_allowlist_blocks_private_even_if_listed(self):
        """白名单不豁免私网地址（私网始终拒绝）."""
        with patch.object(settings, "WORKFLOW_HTTP_ALLOWED_HOSTS", ["192.168.1.1"]):
            with pytest.raises(WorkflowValidationError, match="private|loopback|reserved"):
                validate_http_url("http://192.168.1.1/api")


# ─── 注册期校验（spec-16 链） ──────────────────────────────────────────

class TestRegistrationTimeValidation:
    """PUT 注册时校验 http 节点 url."""

    def test_put_with_private_url_rejected_422(self, client):
        """PUT 含私网 url（mock 关闭）→ 422."""
        payload = {
            "workflow_id": "ssrf_test",
            "nodes": [
                {
                    "name": "fetch",
                    "type": "http",
                    "config": {
                        "url": "http://169.254.169.254/latest/meta-data",
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
        assert "private|loopback|reserved|link-local" in response.json()["message"].lower() or \
               "ssrf" in response.json()["message"].lower()

    def test_put_with_mock_enabled_true_skips_validation(self, client):
        """PUT 含私网 url 但 mock_enabled=true → 通过（零网络，S9）."""
        payload = {
            "workflow_id": "mock_test",
            "nodes": [
                {
                    "name": "fetch",
                    "type": "http",
                    "config": {
                        "url": "http://169.254.169.254/latest/meta-data",
                        "method": "GET",
                        "mock_enabled": True,
                        "mock_responses": {
                            "GET http://169.254.169.254/latest/meta-data": '{"data": "mocked"}'
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
            with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
                response = client.put("/workflows/public_test", json=payload)
        assert response.status_code == 200


# ─── 执行期校验（defense in depth） ────────────────────────────────────

class TestExecutionTimeValidation:
    """执行期二次校验（即使绕过注册）."""

    def test_execution_blocks_private_url(self):
        """执行期请求前校验拦截私网地址."""
        from app.workflow.nodes.http_node import HTTPNode

        node = HTTPNode(
            name="fetch",
            config={
                "url": "http://169.254.169.254/latest/meta-data",
                "method": "GET",
                "mock_enabled": False,
            },
        )
        with pytest.raises(WorkflowValidationError, match="private|loopback|reserved|link-local"):
            node.build_runnable().invoke({})

    def test_execution_allows_public_url(self, monkeypatch):
        """执行期允许公网地址（mock httpx 避免真实网络）."""
        import socket
        from app.workflow.nodes.http_node import HTTPNode

        # Mock httpx.request 避免真实网络
        def mock_request(method, url, headers=None, json=None, timeout=None):
            # 创建带 request 实例的 Response，使 raise_for_status() 正常工作
            request = httpx.Request(method, url, headers=headers)
            return httpx.Response(200, json={"data": "ok"}, request=request)

        monkeypatch.setattr("app.workflow.nodes.http_node.httpx.request", mock_request)

        # Mock DNS 解析返回公网 IP
        def mock_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            if host == "api.example.com":
                # 返回公网 IP（93.184.216.34 是 example.com 的真实 IP）
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
            raise socket.gaierror(f"cannot resolve {host}")

        monkeypatch.setattr("app.workflow.security.socket.getaddrinfo", mock_getaddrinfo)

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
