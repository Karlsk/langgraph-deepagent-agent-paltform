"""Unit tests for app.workflow.nodes.http_node (spec-05, CONTRACT §4.8, AD-02/03/05, H2/H6, S8/S9/S14/S15).

All tests run with zero real network: HTTP traffic is served by
``httpx.MockTransport`` or a monkeypatched ``httpx.request``.
"""

import json
from typing import Any

import httpx
import pytest
import tenacity
from pydantic import ValidationError

from app.workflow.models import HTTPNodeError, NodeType
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


# ---------------------------------------------------------------------------
# TC2: build_runnable pipeline, real branch, explicit mock branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_success_extracts_response_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """200 + response_path extracts the nested value and dual-writes output (S4/S5)."""
    transport, counter = counting_transport(lambda request: httpx.Response(200, json={"data": {"result": "ok"}}))
    patch_httpx(monkeypatch, transport)
    node = make_node({"url": "https://api.example.com/v1", "response_path": "data.result"})
    state = {"token": "abc"}
    result = node.build_runnable().invoke(state)
    assert counter["calls"] == 1
    assert result["status_code"] == 200
    assert result["url"] == "https://api.example.com/v1"
    assert result["response"] == "ok"
    assert result["http1_result"] == {
        "status_code": 200,
        "url": "https://api.example.com/v1",
        "response": "ok",
    }
    # R3/S5: the input state must never be mutated
    assert state == {"token": "abc"}


@pytest.mark.unit
def test_no_response_path_returns_whole_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """response_path=None returns the whole JSON body."""
    transport, _ = counting_transport(lambda request: httpx.Response(200, json={"a": 1, "b": 2}))
    patch_httpx(monkeypatch, transport)
    node = make_node()
    result = node.build_runnable().invoke({})
    assert result["response"] == {"a": 1, "b": 2}


@pytest.mark.unit
def test_response_path_missing_yields_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing response_path yields response=None without crashing (spec-05 §7)."""
    transport, _ = counting_transport(lambda request: httpx.Response(200, json={"other": 1}))
    patch_httpx(monkeypatch, transport)
    node = make_node({"url": "https://api.example.com/v1", "response_path": "data.result"})
    result = node.build_runnable().invoke({})
    assert result["response"] is None
    assert result["status_code"] == 200


@pytest.mark.unit
def test_url_rendered_from_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """URL placeholders render from state, including one-level nested keys."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={})

    transport, _ = counting_transport(handler)
    patch_httpx(monkeypatch, transport)
    node = make_node({"url": "https://api.example.com/{user[tenant]}/run", "method": "GET"})
    node.build_runnable().invoke({"user": {"tenant": "t1"}})
    assert seen["url"] == "https://api.example.com/t1/run"


@pytest.mark.unit
def test_headers_and_body_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Headers/body placeholders render and the body is sent as JSON (spec-05 §7)."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["json"] = request.read()
        return httpx.Response(200, json={})

    transport, _ = counting_transport(handler)
    patch_httpx(monkeypatch, transport)
    node = make_node(
        {
            "url": "https://api.example.com/v1",
            "headers": {"Authorization": "Bearer {token}"},
            "body_template": '{"who": "{name}"}',
        }
    )
    node.build_runnable().invoke({"token": "t-123", "name": "alice"})
    assert seen["auth"] == "Bearer t-123"
    assert json.loads(seen["json"]) == {"who": "alice"}


@pytest.mark.unit
def test_invalid_body_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rendered body that is not valid JSON raises ValueError naming the node."""
    transport, counter = counting_transport(lambda request: httpx.Response(200, json={}))
    patch_httpx(monkeypatch, transport)
    node = make_node({"url": "https://api.example.com/v1", "body_template": "{oops}"})
    with pytest.raises(ValueError, match="http1"):
        node.build_runnable().invoke({})
    assert counter["calls"] == 0  # parse fails before any request


@pytest.mark.unit
def test_mock_enabled_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock_enabled + matching key returns mock data with status_code=200, zero network (S9)."""
    transport, counter = counting_transport(lambda request: httpx.Response(200, json={"real": True}))
    patch_httpx(monkeypatch, transport)
    node = make_node(
        {
            "url": "https://api.example.com/v1",
            "mock_enabled": True,
            "mock_responses": {"POST https://api.example.com/v1": '{"data": {"result": "mocked"}}'},
            "response_path": "data.result",
        }
    )
    result = node.build_runnable().invoke({})
    assert counter["calls"] == 0
    assert result["status_code"] == 200
    assert result["response"] == "mocked"


@pytest.mark.unit
def test_mock_enabled_hit_fallback_url_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bare url key is used when no '{METHOD} {url}' entry exists (S9)."""
    transport, counter = counting_transport(lambda request: httpx.Response(200, json={}))
    patch_httpx(monkeypatch, transport)
    node = make_node(
        {
            "url": "https://api.example.com/v1",
            "mock_enabled": True,
            "mock_responses": {"https://api.example.com/v1": '{"ok": true}'},
        }
    )
    result = node.build_runnable().invoke({})
    assert counter["calls"] == 0
    assert result["response"] == {"ok": True}


@pytest.mark.unit
def test_mock_enabled_miss_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock_enabled + miss raises HTTPNodeError and never falls back to a real call (S9, H2/H6)."""
    transport, counter = counting_transport(lambda request: httpx.Response(200, json={"real": True}))
    patch_httpx(monkeypatch, transport)
    node = make_node(
        {
            "url": "https://api.example.com/v1",
            "mock_enabled": True,
            "mock_responses": {"GET https://other.example.com": "{}"},
        }
    )
    with pytest.raises(HTTPNodeError, match="mock"):
        node.build_runnable().invoke({})
    assert counter["calls"] == 0


@pytest.mark.unit
def test_mock_disabled_ignores_mock_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    """mock_enabled=False goes through the real branch even with mock_responses set (S9)."""
    transport, counter = counting_transport(lambda request: httpx.Response(200, json={"real": True}))
    patch_httpx(monkeypatch, transport)
    node = make_node(
        {
            "url": "https://api.example.com/v1",
            "mock_enabled": False,
            "mock_responses": {"POST https://api.example.com/v1": '{"mocked": true}'},
        }
    )
    result = node.build_runnable().invoke({})
    assert counter["calls"] == 1
    assert result["response"] == {"real": True}


@pytest.mark.unit
def test_runnable_tags() -> None:
    """build_runnable() output carries tags=[name] (K4)."""
    node = make_node()
    runnable = node.build_runnable()
    assert node.name in runnable.config["tags"]  # type: ignore[attr-defined]


@pytest.mark.unit
def test_success_execution_log_summary_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful run logs once with summary input_data (method + url only, S15/H6)."""
    transport, _ = counting_transport(lambda request: httpx.Response(200, json={}))
    patch_httpx(monkeypatch, transport)
    node = make_node({"url": "https://api.example.com/v1", "headers": {"Authorization": SECRET_VALUE}})
    node.build_runnable().invoke({})
    history = node.get_execution_history()
    assert len(history) == 1
    log = history[0]
    assert log.input_data == {"method": "POST", "url": "https://api.example.com/v1"}
    assert log.execution_time_ms >= 0
    assert log.error is None
