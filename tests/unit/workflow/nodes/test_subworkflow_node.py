"""Unit tests for the ``subworkflow`` nesting node (S23/S24, CONTRACT §4.15).

Covers: config validation, the missing-runner ConfigError (raised at
construction so S6 build-time-first applies), inner-input assembly from
``inherit_input`` + ``input_map``, the frozen three-key result envelope
(only ``output`` reaches the outer state), the summary-only ExecutionLog, and
the K5 plugin registration path through ``create_node``.

Zero network / zero LLM: the runner is a recording fake, no registry and no
child workflow is ever executed here — ``test_registry.py`` covers the real
``_run_nested`` guards.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.workflow.models import ConfigError, NestedWorkflowError, NodeDefinition
from app.workflow.nodes.factory import create_node, list_node_types
from app.workflow.nodes.subworkflow_node import SubWorkflowNode, SubWorkflowNodeConfig

pytestmark = pytest.mark.unit


class FakeRunner:
    """Records every call and returns a canned result envelope (§4.15)."""

    def __init__(self, output: dict[str, Any] | None = None) -> None:
        """Remember the canned inner output; calls are recorded for assertions."""
        self.calls: list[tuple[str, dict[str, Any], str]] = []
        self.output = output if output is not None else {"greeting": "hi"}

    def __call__(self, workflow_id: str, input_data: dict[str, Any], caller_label: str) -> dict[str, Any]:
        self.calls.append((workflow_id, input_data, caller_label))
        return {"output": dict(self.output), "run_id": "inner-run-1", "inner_log_count": 2}

    @property
    def last_input(self) -> dict[str, Any]:
        return self.calls[-1][1]


def _node(config: dict[str, Any], runner: Any = None, name: str = "sub_1") -> SubWorkflowNode:
    return SubWorkflowNode(name=name, config=config, workflow_runner=runner or FakeRunner())


def _run(node: SubWorkflowNode, state: dict[str, Any]) -> dict[str, Any]:
    return node.build_runnable().invoke(state)


class TestConfig:
    """SubWorkflowNodeConfig is ``extra='forbid'`` with a mandatory non-blank id (S14)."""

    def test_workflow_id_is_required(self) -> None:
        """Omitting workflow_id is a validation error, not a silent default."""
        with pytest.raises(ValidationError):
            SubWorkflowNodeConfig()

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
    def test_blank_workflow_id_rejected(self, blank: str) -> None:
        """A blank id would only fail much later at run time, so it is rejected here."""
        with pytest.raises(ValidationError, match="workflow_id"):
            SubWorkflowNodeConfig(workflow_id=blank)

    def test_defaults(self) -> None:
        """input_map defaults to empty and inherit_input to False (opt-in inheritance)."""
        cfg = SubWorkflowNodeConfig(workflow_id="inner")
        assert cfg.input_map == {}
        assert cfg.inherit_input is False

    def test_extra_field_forbidden(self) -> None:
        """Unknown config keys are rejected (S14 forbid extras)."""
        with pytest.raises(ValidationError):
            SubWorkflowNodeConfig(workflow_id="inner", bogus=1)  # pyright: ignore[reportCallIssue]

    def test_validate_config_is_satisfied_at_construction(self) -> None:
        """K4: the BaseNode hook stays, but the real validation already ran in __init__."""
        assert _node({"workflow_id": "inner"}).validate_config() is True


class TestMissingRunner:
    """A node without a runner is an assembly error, not a runtime surprise."""

    def test_none_runner_raises_config_error_at_construction(self) -> None:
        """Raised in __init__ so it surfaces as a 422 build-time error (S6), not a 500 at run time."""
        with pytest.raises(ConfigError, match="workflow_runner"):
            SubWorkflowNode(name="sub_1", config={"workflow_id": "inner"}, workflow_runner=None)

    def test_error_names_the_node(self) -> None:
        """The message must identify which node is mis-assembled."""
        with pytest.raises(ConfigError, match="sub_1"):
            SubWorkflowNode(name="sub_1", config={"workflow_id": "inner"})


class TestInnerInputAssembly:
    """inherit_input copies the whole outer state; input_map overrides it (§4.15)."""

    def test_input_map_only(self) -> None:
        """Without inheritance the inner workflow sees exactly the mapped keys."""
        runner = FakeRunner()
        node = _node({"workflow_id": "inner", "input_map": {"query": "input"}}, runner)
        _run(node, {"input": "hello", "unrelated": 1})
        assert runner.last_input == {"query": "hello"}

    def test_inherit_input_passes_full_state(self) -> None:
        """inherit_input=True forwards the outer state as-is."""
        runner = FakeRunner()
        node = _node({"workflow_id": "inner", "inherit_input": True}, runner)
        _run(node, {"input": "hello", "count": 3})
        assert runner.last_input == {"input": "hello", "count": 3}

    def test_input_map_wins_over_inherited_state(self) -> None:
        """On conflict the explicit mapping takes precedence over the inherited key."""
        runner = FakeRunner()
        node = _node({"workflow_id": "inner", "inherit_input": True, "input_map": {"input": "other"}}, runner)
        _run(node, {"input": "outer", "other": "mapped"})
        assert runner.last_input == {"input": "mapped", "other": "mapped"}

    def test_dotted_path_resolves_nested_state(self) -> None:
        """input_map values use S7 dot paths, so nested outer state is reachable."""
        runner = FakeRunner()
        node = _node({"workflow_id": "inner", "input_map": {"q": "payload.text"}}, runner)
        _run(node, {"payload": {"text": "deep"}})
        assert runner.last_input == {"q": "deep"}

    def test_missing_path_is_skipped_not_raised(self) -> None:
        """A dangling path leaves the key out so the inner workflow falls back to its S14 default."""
        runner = FakeRunner()
        node = _node({"workflow_id": "inner", "input_map": {"a": "nope", "b": "input"}}, runner)
        _run(node, {"input": "here"})
        assert runner.last_input == {"b": "here"}

    def test_path_through_a_non_dict_is_skipped_not_raised(self) -> None:
        """Walking into a scalar mid-path resolves to None (S7), so it is skipped like a dangling path."""
        runner = FakeRunner()
        node = _node({"workflow_id": "inner", "input_map": {"a": "input.deep"}}, runner)
        _run(node, {"input": "a-plain-string"})
        assert runner.last_input == {}

    def test_outer_state_is_not_mutated(self) -> None:
        """R3/S5: assembling the inner input must not write into the outer state."""
        outer = {"input": "hello"}
        node = _node({"workflow_id": "inner", "input_map": {"q": "input"}}, FakeRunner())
        _run(node, outer)
        assert outer == {"input": "hello"}

    def test_runner_receives_workflow_id_and_node_name_as_caller_label(self) -> None:
        """The caller label is the node name, which is what S24 prefixes inner logs with."""
        runner = FakeRunner()
        node = _node({"workflow_id": "inner_wf"}, runner, name="sub_7")
        _run(node, {})
        workflow_id, _, caller_label = runner.calls[0]
        assert workflow_id == "inner_wf"
        assert caller_label == "sub_7"


class TestResultEnvelopeAndOutput:
    """Only envelope["output"] reaches the outer state; the node logs a summary (S24)."""

    def test_inner_state_lands_under_node_result_only(self) -> None:
        """dual_write=False (CONTRACT §4.15): the inner state is not flattened into outer channels."""
        runner = FakeRunner(output={"answer": 42})
        node = _node({"workflow_id": "inner"}, runner, name="sub_1")
        out = _run(node, {})
        assert out["sub_1_result"] == {"answer": 42}
        assert "answer" not in out

    def test_inner_state_cannot_clobber_outer_input(self) -> None:
        """The inner state must not be able to clobber the outer workflow's own input.

        This is the hazard dual_write=False exists for: every workflow declares
        `input`, so flattening the inner state would silently replace the outer's
        real value with the inner's. map_output_to_state returns the write-delta,
        so the protection shows up as
        `input` being *absent* from it — with dual_write=True it would be present
        here and langgraph would write it straight over the outer channel.
        """
        runner = FakeRunner(output={"input": "inner-default", "answer": 42})
        node = _node({"workflow_id": "inner"}, runner, name="sub_1")
        out = _run(node, {"input": "outer-real"})
        assert "input" not in out
        assert out["sub_1_result"] == {"input": "inner-default", "answer": 42}

    def test_envelope_meta_keys_do_not_leak_into_state(self) -> None:
        """run_id / inner_log_count are for the summary only, never outer state channels."""
        runner = FakeRunner(output={"answer": 42})
        node = _node({"workflow_id": "inner"}, runner)
        out = _run(node, {})
        assert "run_id" not in out
        assert "inner_log_count" not in out
        assert "output" not in out

    def test_execution_log_is_summary_only(self) -> None:
        """S24: output_data carries the four summary fields, not the inner payload or logs."""
        runner = FakeRunner(output={"answer": 42, "label": "x"})
        node = _node({"workflow_id": "inner"}, runner, name="sub_1")
        _run(node, {})
        summary = node.get_execution_history()[0].output_data
        assert sorted(summary) == ["duration_ms", "inner_log_count", "output_keys", "run_id"]
        assert summary["output_keys"] == ["answer", "label"]
        assert summary["run_id"] == "inner-run-1"
        assert summary["inner_log_count"] == 2
        assert isinstance(summary["duration_ms"], float)
        assert summary["duration_ms"] >= 0.0
        # the inner payload itself must not be embedded anywhere in the summary
        assert {"answer": 42, "label": "x"} not in summary.values()

    def test_runner_failure_propagates_and_is_recorded(self) -> None:
        """H2/R6: a nested failure is logged then re-raised unchanged, never swallowed."""

        def exploding(workflow_id: str, input_data: dict[str, Any], caller_label: str) -> dict[str, Any]:
            raise NestedWorkflowError("cycle detected: a -> b -> a")

        node = SubWorkflowNode(name="sub_1", config={"workflow_id": "b"}, workflow_runner=exploding)
        with pytest.raises(NestedWorkflowError, match="a -> b -> a"):
            _run(node, {})
        assert node.get_execution_history()[0].error is not None


class TestPluginRegistration:
    """create_node resolves 'subworkflow' through the plugin registry (R4, §7.1 probing)."""

    def test_create_node_passes_runner_by_signature_probe(self) -> None:
        """The factory detects the workflow_runner parameter and supplies it."""
        runner = FakeRunner(output={"ok": True})
        definition = NodeDefinition(name="sub_1", type="subworkflow", config={"workflow_id": "inner"})
        node = create_node(definition, workflow_runner=runner)
        assert isinstance(node, SubWorkflowNode)
        assert node.build_runnable().invoke({})["sub_1_result"] == {"ok": True}
        assert runner.calls[0][0] == "inner"

    def test_self_registered_for_visibility(self) -> None:
        """Module-bottom self-registration keeps the type discoverable (K5)."""
        assert "subworkflow" in list_node_types()
