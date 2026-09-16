"""Unit tests for the generic ``python`` code node (plugin-registered type).

Covers: config exclusivity (code XOR entry), inline code execution with
state access and local imports, entry loading, non-dict output rejection,
exception propagation, R3 dual-write pipeline, the factory plugin path, and
the S22 sandboxed routing (``sandboxed=true`` -> subprocess sandbox).
Zero network / zero LLM: ``run_sandboxed`` is stubbed, no child is spawned.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from app.workflow.nodes.factory import create_node
from app.workflow.models import NodeDefinition, PythonNodeError
from app.workflow.nodes.python_node import PythonNode, PythonNodeConfig

pytestmark = pytest.mark.unit


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

    def test_sandboxed_defaults_false(self) -> None:
        """Existing trusted-repository YAML keeps the in-process path (S22)."""
        assert PythonNodeConfig(code="return {}").sandboxed is False

    def test_sandboxed_with_code_ok(self) -> None:
        """sandboxed=True is accepted in code mode."""
        assert PythonNodeConfig(code="return {}", sandboxed=True).sandboxed is True

    def test_sandboxed_with_entry_raises(self) -> None:
        """The ``entry`` mode cannot be sandboxed, so the combination is rejected rather than ignored (§2.3)."""
        with pytest.raises(ValidationError, match="cannot be sandboxed"):
            PythonNodeConfig(entry="app.workflow.utils:convert_state_to_dict", sandboxed=True)


class TestInlineCode:
    """Inline ``code`` runs wrapped with a state dict injected."""

    def test_reads_state_and_returns_dict(self) -> None:
        """Inline code reads the injected state dict and returns a dict."""
        node = _node({"code": 'return {"doubled": state["value"] * 2}'})
        assert _run(node, {"value": 21})["py_result"]["doubled"] == 42

    def test_local_imports_work(self) -> None:
        """Function-wrapped code supports local imports."""
        code = "import json\nreturn {'n': len(json.loads(state['payload']))}"
        node = _node({"code": code})
        assert _run(node, {"payload": "[1, 2, 3]"})["py_result"]["n"] == 3

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
        """Entry function receives the state dict; output goes to {node}_result."""
        node = _node({"entry": "app.workflow.utils:convert_state_to_dict"})
        out = _run(node, {"a": 1})
        assert out["py_result"]["a"] == 1
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

    def test_result_write_and_history_increment(self) -> None:
        """Output writes to {node}_result and history (dual_write off by default)."""
        node = _node({"code": 'return {"flag": "on"}'}, name="py_node")
        out = _run(node, {"history": ["h0"]})
        assert out["py_node_result"] == {"flag": "on"}
        assert "flag" not in out
        assert len(out["history"]) == 1 and out["history"][0].startswith("py_node:")

    def test_execution_log_is_summary_only(self) -> None:
        """ExecutionLog.input_data never contains the code body (H6/S15)."""
        secret_code = 'return {"token": "sk-super-secret-value"}'  # noqa: S105 — dummy literal for leak assertion
        node = _node({"code": secret_code})
        _run(node, {})
        log = node.get_execution_history()[0]
        assert log.input_data == {"mode": "code", "code_chars": len(secret_code), "sandboxed": False}
        assert secret_code not in str(log.input_data)
        assert log.error is None

    def test_error_path_records_error(self) -> None:
        """Failed executions record the error before re-raising."""
        node = _node({"code": "raise ValueError('bad')"})
        with pytest.raises(ValueError, match="bad"):
            _run(node, {})
        assert node.get_execution_history()[0].error is not None


class TestSandboxedRouting:
    """S22: ``sandboxed=true`` runs the code in the subprocess sandbox, never in-process."""

    @staticmethod
    def _stub(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> list[tuple[Any, ...]]:
        calls: list[tuple[Any, ...]] = []

        def fake_run_sandboxed(code: str, state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append((code, state))
            return result

        monkeypatch.setattr("app.workflow.nodes.python_node.run_sandboxed", fake_run_sandboxed)
        return calls

    def test_sandboxed_code_routes_to_run_sandboxed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The sandbox call receives the code and a plain state dict (R3 in-pipeline unchanged)."""
        calls = self._stub(monkeypatch, {"upper": "ABC"})
        node = _node({"code": 'return {"upper": state["input"].upper()}', "sandboxed": True}, name="py")
        out = _run(node, {"input": "abc"})
        assert len(calls) == 1
        assert calls[0][0] == 'return {"upper": state["input"].upper()}'
        assert calls[0][1] == {"input": "abc"}
        assert isinstance(calls[0][1], dict)
        assert out["py_result"]["upper"] == "ABC"

    def test_sandboxed_output_writes_result_and_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sandbox results go through the same out-pipeline as the in-process path."""
        self._stub(monkeypatch, {"flag": "on"})
        node = _node({"code": "return {'flag': 'on'}", "sandboxed": True}, name="py_node")
        out = _run(node, {"history": []})
        assert out["py_node_result"] == {"flag": "on"}
        assert "flag" not in out
        assert len(out["history"]) == 1

    def test_unsandboxed_code_never_touches_the_sandbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression protection: the trusted in-process exec path is byte-for-byte unchanged."""
        calls = self._stub(monkeypatch, {"should": "not be used"})
        node = _node({"code": 'return {"doubled": state["value"] * 2}'})
        assert _run(node, {"value": 21})["py_result"]["doubled"] == 42
        assert calls == []

    def test_entry_mode_never_touches_the_sandbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The entry path is unaffected by the sandbox branch (§2.3)."""
        calls = self._stub(monkeypatch, {"should": "not be used"})
        node = _node({"entry": "app.workflow.utils:convert_state_to_dict"})
        assert _run(node, {"a": 1})["py_result"]["a"] == 1
        assert calls == []

    def test_sandbox_failure_is_recorded_and_propagated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A sandbox error is logged then re-raised (H2/R6, no dead except)."""

        def exploding(code: str, state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            raise PythonNodeError("sandboxed code timed out after 10.0s")

        monkeypatch.setattr("app.workflow.nodes.python_node.run_sandboxed", exploding)
        node = _node({"code": "while True: pass", "sandboxed": True})
        with pytest.raises(PythonNodeError, match="timed out"):
            _run(node, {})
        assert node.get_execution_history()[0].error is not None

    def test_sandboxed_log_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The summary carries sandboxed=True and never the code body (H6/S15)."""
        secret_code = 'return {"token": "sk-super-secret-value"}'  # noqa: S105 — dummy literal for leak assertion
        self._stub(monkeypatch, {"token": "redacted"})
        node = _node({"code": secret_code, "sandboxed": True})
        _run(node, {})
        log = node.get_execution_history()[0]
        assert log.input_data == {"mode": "code", "code_chars": len(secret_code), "sandboxed": True}
        assert secret_code not in str(log.input_data)


class TestInputsMode:
    """Dify-style ``inputs`` mapping: var_name → state dot-path, code defines ``def main(...)``."""

    def test_inputs_inline_happy_path(self) -> None:
        """Inputs resolves dot-paths and calls main(**resolved)."""
        code = "def main(token_resp):\n    return {'access_token': token_resp.get('token', '')}"
        node = _node({"code": code, "inputs": {"token_resp": "get_token_result.response"}, "sandboxed": False})
        state = {"get_token_result": {"response": {"token": "abc123"}}}
        out = _run(node, state)
        assert out["py_result"]["access_token"] == "abc123"  # noqa: S105

    def test_inputs_sandboxed_forwards_inputs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """sandboxed=True forwards resolved inputs to run_sandboxed."""
        calls: list[tuple[Any, ...]] = []

        def fake_run_sandboxed(code: str, state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append((code, state, kwargs.get("inputs")))
            return {"doubled": 42}

        monkeypatch.setattr("app.workflow.nodes.python_node.run_sandboxed", fake_run_sandboxed)
        code = "def main(value):\n    return {'doubled': value * 2}"
        node = _node({"code": code, "inputs": {"value": "data.count"}, "sandboxed": True})
        out = _run(node, {"data": {"count": 21}})
        assert out["py_result"]["doubled"] == 42
        assert len(calls) == 1
        assert calls[0][2] == {"value": 21}

    def test_inputs_without_main_raises(self) -> None:
        """Inputs non-empty but code has no def main → ValidationError at config time."""
        with pytest.raises(ValidationError, match="def main"):
            PythonNodeConfig(
                code="return {'x': 1}",
                inputs={"x": "some.path"},
            )

    def test_inputs_param_mismatch_raises(self) -> None:
        """Inputs keys don't match main() params → ValidationError."""
        code = "def main(alpha, beta):\n    return {}"
        with pytest.raises(ValidationError, match="signature mismatch"):
            PythonNodeConfig(
                code=code,
                inputs={"alpha": "a.path", "gamma": "b.path"},
            )

    def test_empty_inputs_preserves_legacy(self) -> None:
        """inputs={} (default) keeps the legacy state.get() path working."""
        node = _node({"code": 'return {"doubled": state["value"] * 2}'})
        assert _run(node, {"value": 21})["py_result"]["doubled"] == 42

    def test_inputs_with_entry_rejected(self) -> None:
        """Inputs + entry is rejected (inputs requires code mode)."""
        with pytest.raises(ValidationError, match="inputs.*requires.*code"):
            PythonNodeConfig(entry="app.workflow.utils:convert_state_to_dict", inputs={"x": "y.z"})

    def test_dot_path_miss_resolves_to_none(self) -> None:
        """A non-existent dot-path resolves to None (not an error)."""
        code = "def main(maybe):\n    return {'got': maybe}"
        node = _node({"code": code, "inputs": {"maybe": "no.such.path"}, "sandboxed": False})
        out = _run(node, {"other": "data"})
        assert out["py_result"]["got"] is None

    def test_inputs_multiple_params(self) -> None:
        """Multiple inputs resolve independently and pass as keyword args."""
        code = "def main(a, b):\n    return {'sum': a + b}"
        node = _node({"code": code, "inputs": {"a": "x.val", "b": "y.val"}, "sandboxed": False})
        out = _run(node, {"x": {"val": 10}, "y": {"val": 32}})
        assert out["py_result"]["sum"] == 42


class TestFactoryPluginPath:
    """create_node resolves type 'python' through the plugin registry (R4)."""

    def test_create_node_python_type(self) -> None:
        """create_node resolves 'python' via the plugin registry (R4)."""
        definition = NodeDefinition(name="step", type="python", config={"code": "return {}"})
        node = create_node(definition)
        assert isinstance(node, PythonNode)
        assert node.build_runnable().invoke({"history": []})["step_result"] == {}
