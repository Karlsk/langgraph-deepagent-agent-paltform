"""Unit tests for the generic ``python`` code node (plugin-registered type).

Covers: config exclusivity (code XOR entry), inline code execution with
state access and local imports, entry loading, non-dict output rejection,
exception propagation, R3 dual-write pipeline, and the factory plugin path.
Zero network / zero LLM.
"""

import pytest

from app.workflow.nodes.factory import create_node
from app.workflow.models import NodeDefinition, PythonNodeError
from app.workflow.nodes.python_node import PythonNode, PythonNodeConfig


def _node(config: dict, name: str = "py") -> PythonNode:
    return PythonNode(name=name, config=config)


def _run(node: PythonNode, state: dict) -> dict:
    return node.build_runnable().invoke(state)


class TestConfigExclusivity:
    """PythonNodeConfig requires exactly one of code / entry (S14 forbid extras)."""

    def test_neither_raises(self) -> None:
        """Empty config is rejected with 'exactly one' message."""
        with pytest.raises(ValueError, match="exactly one"):
            PythonNodeConfig()

    def test_both_raises(self) -> None:
        """Providing both code and entry is rejected."""
        with pytest.raises(ValueError, match="exactly one"):
            PythonNodeConfig(code="return {}", entry="mod:fn")

    def test_code_only_ok(self) -> None:
        """Code-only config passes validation."""
        assert PythonNodeConfig(code="return {}").code == "return {}"

    def test_entry_only_ok(self) -> None:
        """Entry-only config passes validation."""
        assert PythonNodeConfig(entry="app.workflow.utils:convert_state_to_dict").entry

    def test_extra_field_forbidden(self) -> None:
        """Unknown config keys are rejected (extra='forbid', S14)."""
        with pytest.raises(ValueError):
            PythonNodeConfig(code="return {}", unknown_key=1)  # pyright: ignore[reportCallIssue]


class TestInlineCode:
    """Inline ``code`` runs wrapped with a state dict injected."""

    def test_reads_state_and_returns_dict(self) -> None:
        """Inline code reads the injected state dict and returns a dict."""
        node = _node({"code": 'return {"doubled": state["value"] * 2}'})
        assert _run(node, {"value": 21})["doubled"] == 42

    def test_local_imports_work(self) -> None:
        """Function-wrapped code supports local imports."""
        code = "import json\nreturn {'n': len(json.loads(state['payload']))}"
        node = _node({"code": code})
        assert _run(node, {"payload": "[1, 2, 3]"})["n"] == 3

    def test_non_dict_output_raises(self) -> None:
        """Non-dict return values are rejected."""
        node = _node({"code": "return 42"})
        with pytest.raises(PythonNodeError, match="dict"):
            _run(node, {})

    def test_missing_return_raises(self) -> None:
        """Code without a return statement (None output) is rejected."""
        node = _node({"code": "x = 1"})
        with pytest.raises(PythonNodeError, match="return"):
            _run(node, {})

    def test_code_exception_propagates(self) -> None:
        """User exceptions propagate unchanged (H2/R6)."""
        node = _node({"code": "raise RuntimeError('boom')"})
        with pytest.raises(RuntimeError, match="boom"):
            _run(node, {})

    def test_syntax_error_raises_python_node_error(self) -> None:
        """Un-compilable code raises PythonNodeError."""
        node = _node({"code": "return {"})
        with pytest.raises(PythonNodeError, match="compile"):
            _run(node, {})


class TestEntry:
    """``entry`` loads a repository function as module:function."""

    def test_entry_function_invoked_with_state(self) -> None:
        """Entry function receives the state dict; output dual-writes."""
        node = _node({"entry": "app.workflow.utils:convert_state_to_dict"})
        out = _run(node, {"a": 1})
        assert out["a"] == 1
        assert out["py_result"] == {"a": 1}

    def test_entry_missing_colon_raises(self) -> None:
        """Entry without ':' separator is rejected."""
        node = _node({"entry": "no_colon_here"})
        with pytest.raises(PythonNodeError, match="module:function"):
            _run(node, {})

    def test_entry_unknown_module_raises(self) -> None:
        """Unknown module raises PythonNodeError mentioning import."""
        node = _node({"entry": "no.such.module:fn"})
        with pytest.raises(PythonNodeError, match="import"):
            _run(node, {})

    def test_entry_unknown_attr_raises(self) -> None:
        """Unknown attribute raises PythonNodeError naming the attribute."""
        node = _node({"entry": "app.workflow.utils:no_such_function"})
        with pytest.raises(PythonNodeError, match="no_such_function"):
            _run(node, {})


class TestPipelineAndLogging:
    """R3 out-pipeline: dual write + history increment; summary-only logs."""

    def test_dual_write_and_history_increment(self) -> None:
        """Output dual-writes flat fields plus {node}_result and history entry."""
        node = _node({"code": 'return {"flag": "on"}'}, name="py_node")
        out = _run(node, {"history": ["h0"]})
        assert out["flag"] == "on"
        assert out["py_node_result"] == {"flag": "on"}
        assert len(out["history"]) == 1 and out["history"][0].startswith("py_node:")

    def test_execution_log_is_summary_only(self) -> None:
        """ExecutionLog.input_data never contains the code body (H6/S15)."""
        secret_code = 'return {"token": "sk-super-secret-value"}'  # noqa: S105 — dummy literal for leak assertion
        node = _node({"code": secret_code})
        _run(node, {})
        log = node.get_execution_history()[0]
        assert log.input_data == {"mode": "code", "code_chars": len(secret_code)}
        assert secret_code not in str(log.input_data)
        assert log.error is None

    def test_error_path_records_error(self) -> None:
        """Failed executions record the error before re-raising."""
        node = _node({"code": "raise ValueError('bad')"})
        with pytest.raises(ValueError, match="bad"):
            _run(node, {})
        assert node.get_execution_history()[0].error is not None


class TestFactoryPluginPath:
    """create_node resolves type 'python' through the plugin registry (R4)."""

    def test_create_node_python_type(self) -> None:
        """create_node resolves 'python' via the plugin registry (R4)."""
        definition = NodeDefinition(name="step", type="python", config={"code": "return {}"})
        node = create_node(definition)
        assert isinstance(node, PythonNode)
        assert node.build_runnable().invoke({"history": []})["step_result"] == {}
