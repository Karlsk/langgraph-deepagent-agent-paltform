"""Unit tests for nested execution guards and inner log merging (S23/S24).

Split out of ``test_registry.py`` (already 427 lines) so the nesting semantics
stay readable together. Uses the shared ``echo`` plugin node from conftest for
inner workflows — zero network, zero LLM, no real ``subworkflow`` HTTP surface.

Covers: prefixed inner-log merging and its natural multi-layer composition,
cycle detection (indirect and self), the depth limit's frozen semantics
(``max_nesting_depth`` = workflows simultaneously on the run stack, outermost
included), ContextVar reset on the failure path, and the runtime
``WorkflowNotFoundError`` for a dangling reference.
"""

from __future__ import annotations

import pytest

from app.workflow.models import (
    EdgeDefinition,
    NodeDefinition,
    StateFieldSchema,
    WorkflowDefinition,
)
from app.workflow.registry import _RUN_STACK, WorkflowRegistry  # noqa: SLF001 — asserting ContextVar hygiene per S23

pytestmark = pytest.mark.unit


def _echo_wf(workflow_id: str, *node_names: str) -> WorkflowDefinition:
    """A linear workflow of echo nodes: entry -> ... -> END."""
    names = list(node_names)
    nodes = [NodeDefinition(name=n, type="echo", config={"output": {"v": n}}) for n in names]
    edges = [EdgeDefinition(source=names[i], target=names[i + 1]) for i in range(len(names) - 1)]
    edges.append(EdgeDefinition(source=names[-1], target="END"))
    return WorkflowDefinition(
        workflow_id=workflow_id,
        entry_point=names[0],
        nodes=nodes,
        edges=edges,
        state_schema={"input": StateFieldSchema(type="str")},
    )


def _nested_wf(workflow_id: str, inner_id: str, *, sub_node: str = "sub_1", **config: object) -> WorkflowDefinition:
    """A one-node workflow whose only node calls ``inner_id``."""
    return WorkflowDefinition(
        workflow_id=workflow_id,
        entry_point=sub_node,
        nodes=[NodeDefinition(name=sub_node, type="subworkflow", config={"workflow_id": inner_id, **config})],
        edges=[EdgeDefinition(source=sub_node, target="END")],
        state_schema={"input": StateFieldSchema(type="str")},
    )


def _registry_with(*definitions: WorkflowDefinition, **kwargs: object) -> WorkflowRegistry:
    registry = WorkflowRegistry(**kwargs)  # pyright: ignore[reportArgumentType] — kwargs are the frozen init params
    for definition in definitions:
        registry.register_workflow(definition)
    return registry


def _log_names(result: object) -> list[str]:
    return [log.node_name for log in result.execution_logs]  # pyright: ignore[reportAttributeAccessIssue]


class TestInnerLogMerging:
    """S24: inner logs are prefixed with the calling node's name and merged outward."""

    def test_inner_logs_appear_prefixed_in_the_outer_trace(self) -> None:
        """The outer trace reads sub_1/i1, sub_1/i2 alongside the subworkflow node's own entry."""
        registry = _registry_with(_echo_wf("wf_inner", "i1", "i2"), _nested_wf("wf_outer", "wf_inner"))
        result = registry.execute_workflow("wf_outer", {"input": "x"})
        names = _log_names(result)
        assert "sub_1/i1" in names
        assert "sub_1/i2" in names
        assert "sub_1" in names
        # inner logs must not also appear unprefixed in the outer trace
        assert "i1" not in names

    def test_inner_state_reachable_under_node_result_only(self) -> None:
        """dual_write=False (§4.15): inner data lands at {node}_result, not in same-named outer channels."""
        registry = _registry_with(_echo_wf("wf_inner", "i1"), _nested_wf("wf_outer", "wf_inner"))
        output = registry.execute_workflow("wf_outer", {"input": "outer-real"}).output
        assert "i1_result" in output["sub_1_result"]
        assert output["input"] == "outer-real"

    def test_subworkflow_node_own_log_is_summary_only(self) -> None:
        """The calling node's entry carries the four summary fields, not the inner payload."""
        registry = _registry_with(_echo_wf("wf_inner", "i1"), _nested_wf("wf_outer", "wf_inner"))
        result = registry.execute_workflow("wf_outer", {"input": "x"})
        own = next(log for log in result.execution_logs if log.node_name == "sub_1")
        assert sorted(own.output_data) == ["duration_ms", "inner_log_count", "output_keys", "run_id"]
        assert own.output_data["inner_log_count"] == 1

    def test_prefixes_compose_across_three_layers(self) -> None:
        """No per-layer special-casing: C's log surfaces in A's trace as sub_b/sub_c/c1."""
        registry = _registry_with(
            _echo_wf("wf_c", "c1"),
            _nested_wf("wf_b", "wf_c", sub_node="sub_c"),
            _nested_wf("wf_a", "wf_b", sub_node="sub_b"),
        )
        names = _log_names(registry.execute_workflow("wf_a", {"input": "x"}))
        assert "sub_b/sub_c/c1" in names
        assert "sub_b/sub_c" in names
        assert "sub_b" in names


class TestCycleDetection:
    """S23: a workflow already on the run stack can never be entered again."""

    def test_indirect_cycle_is_rejected_with_the_full_stack(self) -> None:
        """An indirect cycle reports the whole path in the failed result's error_message."""
        registry = _registry_with(
            _nested_wf("wf_a", "wf_b", sub_node="sub_b"),
            _nested_wf("wf_b", "wf_a", sub_node="sub_a"),
        )
        result = registry.execute_workflow("wf_a", {"input": "x"})
        assert result.status == "failed"
        assert "wf_a -> wf_b -> wf_a" in (result.error_message or "")

    def test_self_reference_is_rejected(self) -> None:
        """A node pointing at its own workflow is caught by the cycle guard, not by RLock re-entry."""
        registry = _registry_with(_nested_wf("wf_self", "wf_self"))
        result = registry.execute_workflow("wf_self", {"input": "x"})
        assert result.status == "failed"
        assert "wf_self -> wf_self" in (result.error_message or "")


class TestDepthLimit:
    """S23: max_nesting_depth counts workflows on the stack, outermost included."""

    def test_three_layer_chain_runs_at_the_default_depth(self) -> None:
        """Default 3 means a -> b -> c exactly fills the budget and succeeds."""
        registry = _registry_with(
            _echo_wf("wf_c", "c1"),
            _nested_wf("wf_b", "wf_c", sub_node="sub_c"),
            _nested_wf("wf_a", "wf_b", sub_node="sub_b"),
        )
        assert "sub_b/sub_c/c1" in _log_names(registry.execute_workflow("wf_a", {"input": "x"}))

    def test_fourth_layer_is_rejected_at_the_default_depth(self) -> None:
        """The rejection lands on the fourth workflow, not the third (frozen off-by-one)."""
        registry = _registry_with(
            _echo_wf("wf_d", "d1"),
            _nested_wf("wf_c", "wf_d", sub_node="sub_d"),
            _nested_wf("wf_b", "wf_c", sub_node="sub_c"),
            _nested_wf("wf_a", "wf_b", sub_node="sub_b"),
        )
        result = registry.execute_workflow("wf_a", {"input": "x"})
        assert result.status == "failed"
        assert "wf_a -> wf_b -> wf_c -> wf_d" in (result.error_message or "")

    def test_depth_one_forbids_any_nesting(self) -> None:
        """max_nesting_depth=1 leaves no room for an inner workflow at all."""
        registry = _registry_with(
            _echo_wf("wf_inner", "i1"),
            _nested_wf("wf_outer", "wf_inner"),
            max_nesting_depth=1,
        )
        result = registry.execute_workflow("wf_outer", {"input": "x"})
        assert result.status == "failed"


class TestRunStackHygiene:
    """S23/S11: the ContextVar is paired set/reset, so it never leaks past a run."""

    def test_stack_is_empty_before_any_run(self) -> None:
        """Baseline: nothing leaks in from other tests."""
        assert _RUN_STACK.get() == ()

    def test_stack_is_reset_after_a_successful_run(self) -> None:
        """The happy path resets too, so a later unrelated run starts clean."""
        registry = _registry_with(_echo_wf("wf_inner", "i1"), _nested_wf("wf_outer", "wf_inner"))
        registry.execute_workflow("wf_outer", {"input": "x"})
        assert _RUN_STACK.get() == ()

    def test_stack_is_reset_after_a_failed_run(self) -> None:
        """The run fails, but the finally still resets — otherwise the next run inherits a phantom stack."""
        registry = _registry_with(_nested_wf("wf_self", "wf_self"))
        result = registry.execute_workflow("wf_self", {"input": "x"})
        assert result.status == "failed"
        assert _RUN_STACK.get() == ()

    def test_a_clean_run_succeeds_after_a_failed_one(self) -> None:
        """End-to-end consequence of the reset: no sticky state between runs."""
        registry = _registry_with(
            _nested_wf("wf_self", "wf_self"),
            _echo_wf("wf_inner", "i1"),
            _nested_wf("wf_outer", "wf_inner"),
        )
        failed = registry.execute_workflow("wf_self", {"input": "x"})
        assert failed.status == "failed"
        assert "sub_1/i1" in _log_names(registry.execute_workflow("wf_outer", {"input": "x"}))


class TestDanglingReference:
    """S18: referential existence is a runtime check, so it reuses the existing exception."""

    def test_unregistered_inner_returns_failed_result(self) -> None:
        """Saving a dangling reference is allowed; executing it records the unknown id in error_message."""
        registry = _registry_with(_nested_wf("wf_outer", "wf_missing"))
        result = registry.execute_workflow("wf_outer", {"input": "x"})
        assert result.status == "failed"
        assert "wf_missing" in (result.error_message or "")
