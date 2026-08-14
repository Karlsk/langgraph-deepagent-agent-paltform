"""Shared pytest fixtures for the test suite."""

import time
from collections.abc import Generator
from typing import Any, override

import pytest
from dotenv import load_dotenv
from langchain_core.runnables import Runnable

from app.workflow.models import ExecutionLog
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type
from app.workflow.utils import convert_state_to_dict, map_output_to_state

load_dotenv()


def unwrap(response: Any, *, expected_code: int | None = None) -> Any:
    """Unwrap the ApiResponse envelope of an httpx/TestClient response.

    Asserts the body is a well-formed envelope (``code``/``message``/``data``
    all present) and returns its ``data`` payload, so tests keep reading the
    endpoint's original projection. ``expected_code`` optionally pins the
    envelope code (which mirrors the HTTP status by design).

    Args:
        response: A TestClient/httpx response whose body is an ApiResponse.
        expected_code: When given, the envelope ``code`` must equal it.

    Returns:
        The envelope's ``data`` field (None for DELETE/void endpoints).
    """
    body = response.json()
    assert isinstance(body, dict), f"envelope must be a JSON object, got: {body!r}"
    for key in ("code", "message", "data"):
        assert key in body, f"envelope missing '{key}' key: {body!r}"
    if expected_code is not None:
        assert body["code"] == expected_code, f"envelope code {body['code']} != {expected_code}"
    return body["data"]


class EchoNode(BaseNode):
    """Shared test-only node: merges config['output'] into state (zero network, zero LLM; AD-08)."""

    @override
    def build_runnable(self) -> Runnable:
        def func(state: Any) -> dict[str, Any]:
            started = time.perf_counter()
            state_dict = convert_state_to_dict(state)
            output = dict(self.config.get("output", {}))
            result = map_output_to_state(self.name, output, state_dict)
            self.log_execution(
                ExecutionLog(
                    node_name=self.name,
                    node_type=str(self.node_type),
                    input_data={},
                    output_data=output,
                    execution_time_ms=(time.perf_counter() - started) * 1000.0,
                )
            )
            return result

        return self.wrap_runnable(func)

    @override
    def validate_config(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def restore_node_registry() -> Generator[None, None, None]:
    """Snapshot and restore the node type registry around each test (D7/AD-08)."""
    from app.workflow.nodes import factory  # noqa: PLC0415 — import inside fixture per AD-08

    snapshot = dict(factory._NODE_REGISTRY)  # noqa: SLF001 — private access per AD-08 spec
    yield
    factory._NODE_REGISTRY.clear()  # noqa: SLF001
    factory._NODE_REGISTRY.update(snapshot)  # noqa: SLF001


@pytest.fixture(autouse=True)
def register_echo_node() -> Generator[None, None, None]:
    """Register the shared EchoNode for every test; cleanup relies on restore_node_registry (D7)."""
    register_node_type("echo", EchoNode)
    yield
