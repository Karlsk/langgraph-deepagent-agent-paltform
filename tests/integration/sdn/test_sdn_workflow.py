"""Integration tests: SDN alert inspection workflow end-to-end (zero network / zero LLM).

Loads the committed template YAML, monkeypatches ``httpx.request`` used by the
built-in HTTPNode and the ``ChatOpenAI`` used by the built-in LLMNode, then
runs the registry end-to-end. Covers: two-alert loop (two rounds), zero-alert
short-circuit, check_result aggregation and the assembled report.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage

from app.workflow.models import load_definition_from_yaml
from app.workflow.registry import WorkflowRegistry

pytestmark = pytest.mark.integration

TEMPLATE = Path(__file__).resolve().parents[3] / "app" / "sdn" / "config" / "sdn_alert_inspection.template.yaml"

_LLM_BODY = "## 2. 详细检测结果\n\n| 节点名称 | 设备厂商 |\n\n## 3. 处理建议\n\n1. 检查光模块"

_ALERTS = [
    {"source": "NJ-SCT-R02", "component": "GigabitEthernet0/4/9", "time": "2026-07-24 04:08:35"},
    {"source": "NJ-SCT-R03", "component": "XGE3/2/20.1", "time": "2026-07-24 04:10:00"},
]

_RAW_BY_DEVICE = {
    "NJ-SCT-R02": "GE0/4/9  up  up  10.0.0.1  --  to_hw104",
    "NJ-SCT-R03": "XGE3/2/20.1  *down  down  --  --  ",
}


class _FakeChat:
    """Stand-in for ChatOpenAI: records invoked messages and returns a fixed reply."""

    invocations: list[Any] = []

    def __init__(self, **kwargs: Any) -> None:
        """Accept and ignore all provider kwargs."""
        self.kwargs = kwargs

    def invoke(self, messages: Any) -> AIMessage:
        """Record the message list and return the canned report body."""
        _FakeChat.invocations.append(messages)
        return AIMessage(content=_LLM_BODY)


@pytest.fixture()
def fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dummy LLM credentials so the lazy env resolution succeeds (no network)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy-test-only")  # noqa: S105
    monkeypatch.setattr("app.workflow.nodes.llm_node.ChatOpenAI", _FakeChat)
    _FakeChat.invocations = []


def _patch_http(monkeypatch: pytest.MonkeyPatch, alerts: list[dict[str, Any]]) -> dict[str, int]:
    """Route the HTTPNode's httpx.request to an in-memory dispatcher (zero network)."""
    counts = {"token": 0, "alerts": 0, "pe_info": 0, "command_exec": 0}

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        body = kwargs.get("json") or {}
        # request instance required so raise_for_status works on the fake response
        request = httpx.Request(method, url)
        if url.endswith("/oauth/token"):
            counts["token"] += 1
            return httpx.Response(200, json={"access_token": "tok-abc"}, request=request)
        if url.endswith("/monitor/v2/alert/page"):
            counts["alerts"] += 1
            return httpx.Response(200, json={"data": alerts}, request=request)
        if "terra-pe:peInfos" in url:
            counts["pe_info"] += 1
            return httpx.Response(200, json={"content": [{"vendor-id": "H3C"}]}, request=request)
        if url.endswith("/device-conf/command-result"):
            counts["command_exec"] += 1
            return httpx.Response(200, json={"result": _RAW_BY_DEVICE[body["device_name"]]}, request=request)
        msg = f"unexpected url in test dispatcher: {url}"
        raise AssertionError(msg)

    monkeypatch.setattr("app.workflow.nodes.http_node.httpx.request", fake_request)
    return counts


def _registry() -> WorkflowRegistry:
    registry = WorkflowRegistry()
    registry.register_workflow(load_definition_from_yaml(TEMPLATE))
    return registry


def test_two_alerts_loop_twice_and_report(fake_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two alerts drive two loop rounds; report assembles header + LLM body."""
    counts = _patch_http(monkeypatch, _ALERTS)
    result = _registry().execute_workflow("sdn_alert_inspection", {})
    output = result.output

    # 循环两圈：厂商查询与命令下发各 2 次
    assert counts == {"token": 1, "alerts": 1, "pe_info": 2, "command_exec": 2}
    # check_result 聚合两条摘要（reducer add）
    assert len(output["check_result"]) == 2
    assert "NJ-SCT-R02" in output["check_result"][0] and "Physical: up" in output["check_result"][0]
    assert "NJ-SCT-R03" in output["check_result"][1] and "Physical: *down" in output["check_result"][1]
    # 报告：固定头 + 故障判定 + LLM 正文
    report = output["report"]
    assert "# 网络智能巡检报告" in report
    assert "⚠️ 异常检测 (Fault Detected)" in report
    assert _LLM_BODY in report
    # LLM 收到编号后的检查上下文（system_prompt + user context）
    (messages,) = _FakeChat.invocations
    context = str(messages[-1])
    assert "1." in context and "2." in context


def test_zero_alerts_short_circuits(fake_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """No alerts skip the device-check loop entirely (== '' short-circuit edge)."""
    counts = _patch_http(monkeypatch, [])
    result = _registry().execute_workflow("sdn_alert_inspection", {})
    output = result.output

    assert counts == {"token": 1, "alerts": 1, "pe_info": 0, "command_exec": 0}
    assert output["check_result"] == []
    assert "✅ 正常 (Normal)" in output["report"]
    # 兜底文案进入 LLM 上下文
    (messages,) = _FakeChat.invocations
    assert "No interface inspection results available." in str(messages[-1])
