"""Example-directory smoke tests (spec-09 TC3).

The three shipped example YAMLs must stay parseable, compilable and runnable.
LLM calls are faked via monkeypatched ChatOpenAI (existing unit-test pattern),
the HTTP node runs its real mock branch (S9), so the suite performs zero
network and zero real LLM calls while exercising the full parse -> build ->
execute chain against the real example files, including the "LLM -> condition
-> HTTP" combination path of condition_branch_demo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.workflow.cli import build_registry
from app.workflow.models import ConditionNotMatchedError

pytestmark = pytest.mark.integration

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "app" / "workflow" / "config" / "examples"

_LLM_REPLY: str = "Hello!"


class _FakeChat:
    """Stand-in for ChatOpenAI returning the module-level canned reply (zero network)."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def invoke(self, messages: Any) -> AIMessage:
        return AIMessage(content=_LLM_REPLY)


@pytest.fixture()
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap ChatOpenAI inside llm_node and provide the dummy key it resolves (H6: dummy only)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy-test-only")  # noqa: S105 — test-only dummy
    monkeypatch.setattr("app.workflow.nodes.llm_node.ChatOpenAI", _FakeChat)


@pytest.fixture()
def examples_registry(fake_llm: None):
    """Registry built from the shipped example YAMLs."""
    return build_registry(EXAMPLES_DIR)


def test_examples_directory_registers_all_three(examples_registry) -> None:
    """All three examples (minimal / http_demo / condition_branch) parse and compile (spec-09 TC3)."""
    assert sorted(examples_registry.list_workflows()) == [
        "condition_branch_demo",
        "demo_http",
        "demo_minimal",
    ]


def test_demo_minimal_runs_with_fake_llm(examples_registry) -> None:
    """LLM single-node example executes with messages input and exposes greet_result."""
    result = examples_registry.execute_workflow("demo_minimal", {"messages": [{"role": "user", "content": "hi"}]})
    assert result.output["greet_result"]["response"] == "Hello!"
    assert result.run_id


def test_demo_http_runs_on_mock_branch(examples_registry) -> None:
    """HTTP example runs through the real mock branch (S9), zero network."""
    result = examples_registry.execute_workflow("demo_http", {"input": "hi"})
    fetch_result = result.output["fetch_result"]
    assert fetch_result["status_code"] == 200
    assert fetch_result["response"] == {"items": ["alpha", "beta"]}


def test_condition_branch_ok_routes_to_http_notify(examples_registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM -> condition -> HTTP combination: reply 'OK' routes to the mock notify node."""
    monkeypatch.setattr(__name__ + "._LLM_REPLY", "OK")
    result = examples_registry.execute_workflow(
        "condition_branch_demo", {"input": "hi", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert result.output["notify_result"]["status_code"] == 200
    assert "summarize_result" not in result.output


def test_condition_branch_review_routes_to_summarize(examples_registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reply 'NEED_REVIEW' routes to the summarize LLM branch instead of HTTP."""
    monkeypatch.setattr(__name__ + "._LLM_REPLY", "NEED_REVIEW")
    result = examples_registry.execute_workflow(
        "condition_branch_demo", {"input": "hi", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert result.output["summarize_result"]["response"] == "NEED_REVIEW"
    assert "notify_result" not in result.output


def test_condition_branch_unmatched_reply_raises(examples_registry, monkeypatch: pytest.MonkeyPatch) -> None:
    """S6 raise policy: a reply outside the marker set hits no condition edge."""
    monkeypatch.setattr(__name__ + "._LLM_REPLY", "MAYBE")
    with pytest.raises(ConditionNotMatchedError):
        examples_registry.execute_workflow(
            "condition_branch_demo", {"input": "hi", "messages": [{"role": "user", "content": "hi"}]}
        )
