"""Unit tests for app.workflow.nodes.http_node (spec-05, CONTRACT §4.8, AD-02/03/05, H2/H6, S8/S9/S14/S15).

All tests run with zero real network: HTTP traffic is served by
``httpx.MockTransport`` or a monkeypatched ``httpx.request``.
"""

from typing import Any

import httpx
import pytest
import tenacity
from pydantic import ValidationError

from app.workflow.models import NodeType
from app.workflow.nodes.http_node import HTTPNode, HTTPNodeConfig

STATE_MARK = "__full_state_marker__"
SECRET_VALUE = "Bearer super-secret-token-spec05"  # noqa: S105 — dummy sentinel for H6 leak tests


def make_node(config: dict[str, Any] | HTTPNodeConfig | None = None, **kwargs: Any) -> HTTPNode:
    """Build an HTTPNode with a safe default config."""
    return HTTPNode(name=kwargs.pop("name", "http1"), config=config or {"url": "https://api.example.com/v1"}, **kwargs)


def counting_transport(handler: Any) -> tuple[httpx.MockTransport, dict[str, int]]:
    """Wrap a MockTransport handler with a call counter (zero-network assertions)."""
    counter = {"calls": 0}

    def wrapped(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return handler(request)

    return httpx.MockTransport(wrapped), counter


def patch_httpx(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    """Route http_node's httpx.request through a MockTransport-backed client."""

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        kwargs.pop("timeout", None)
        with httpx.Client(transport=transport) as client:
            return client.request(method, url, **kwargs)

    monkeypatch.setattr("app.workflow.nodes.http_node.httpx.request", fake_request)


def patch_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Monkeypatch tenacity.nap.sleep, returning the recorded delay sequence (AD-03)."""
    delays: list[float] = []
    monkeypatch.setattr(tenacity.nap, "sleep", lambda seconds: delays.append(seconds))
    return delays


# ---------------------------------------------------------------------------
# TC1: HTTPNodeConfig, render_template, _extract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_render_simple_and_nested() -> None:
    """{key} placeholders render; one-level nested dicts flatten to {parent[child]} (TC1)."""
    node = make_node()
    context = {"name": "alice", "a": {"b": 42}}
    assert node.render_template("hello {name}, value={a[b]}", context) == "hello alice, value=42"


@pytest.mark.unit
def test_render_unknown_placeholder_kept() -> None:
    """Unknown placeholders are kept verbatim (spec-05 TC1)."""
    node = make_node()
    assert node.render_template("keep {missing} here", {"name": "x"}) == "keep {missing} here"


@pytest.mark.unit
def test_render_json_braces_survive() -> None:
    """JSON object braces are not valid placeholders and must survive rendering."""
    node = make_node()
    rendered = node.render_template('{"user": "{name}"}', {"name": "alice"})
    assert rendered == '{"user": "alice"}'


@pytest.mark.unit
def test_extract_simple_path() -> None:
    """Dot paths walk nested dicts layer by layer (TC1)."""
    node = make_node()
    data = {"data": {"result": [1, 2, 3]}}
    assert node._extract(data, "data.result") == [1, 2, 3]  # noqa: SLF001 — frozen contract method


@pytest.mark.unit
def test_extract_missing_yields_none() -> None:
    """A missing path segment yields None without raising."""
    node = make_node()
    assert node._extract({"data": {}}, "data.result.deep") is None  # noqa: SLF001


@pytest.mark.unit
def test_extract_none_path_returns_whole() -> None:
    """path=None returns the whole data payload."""
    node = make_node()
    data = {"a": 1}
    assert node._extract(data, None) is data  # noqa: SLF001


@pytest.mark.unit
def test_config_extra_forbid_rejects_unknown() -> None:
    """Unknown config fields are rejected (S14, extra='forbid')."""
    with pytest.raises(ValidationError):
        HTTPNodeConfig(url="https://x", unknown_field="x")  # type: ignore[call-arg]


@pytest.mark.unit
def test_config_defaults_per_contract() -> None:
    """CONTRACT §4.8 defaults: POST, timeout 30, max_retries 0, mock off (S8/S9)."""
    cfg = HTTPNodeConfig(url="https://x")
    assert cfg.method == "POST"
    assert cfg.timeout == 30.0
    assert cfg.max_retries == 0
    assert cfg.retry_base_delay == 1.0
    assert cfg.retry_on_status == [429, 500, 502, 503, 504]
    assert cfg.mock_enabled is False
    assert cfg.mock_responses is None
    assert cfg.response_path is None


@pytest.mark.unit
def test_validate_config_empty_url() -> None:
    """Empty url is invalid and raises ValueError (TC1)."""
    node = make_node({"url": ""})
    with pytest.raises(ValueError, match="url"):
        node.validate_config()


@pytest.mark.unit
def test_validate_config_mock_without_responses_invalid() -> None:
    """mock_enabled=True without mock_responses is invalid (spec-05 TC1)."""
    node = make_node({"url": "https://x", "mock_enabled": True})
    with pytest.raises(ValueError, match="mock_responses"):
        node.validate_config()

    node = make_node({"url": "https://x", "mock_enabled": True, "mock_responses": {}})
    with pytest.raises(ValueError, match="mock_responses"):
        node.validate_config()


@pytest.mark.unit
def test_validate_config_valid_returns_true() -> None:
    """A valid config passes validation."""
    assert make_node().validate_config() is True


@pytest.mark.unit
def test_init_dict_config_converted() -> None:
    """Dict input is auto-converted to HTTPNodeConfig; BaseNode fields set per contract."""
    node = make_node({"url": "https://api.example.com/run", "method": "GET"})
    assert isinstance(node._node_config, HTTPNodeConfig)  # noqa: SLF001
    assert node._node_config.url == "https://api.example.com/run"  # noqa: SLF001
    assert node.node_type == NodeType.HTTP
    assert node.config == node._node_config.model_dump()  # noqa: SLF001


@pytest.mark.unit
def test_init_accepts_config_instance() -> None:
    """An HTTPNodeConfig instance is accepted as-is."""
    cfg = HTTPNodeConfig(url="https://api.example.com/v2")
    node = make_node(cfg)
    assert node._node_config is cfg  # noqa: SLF001
