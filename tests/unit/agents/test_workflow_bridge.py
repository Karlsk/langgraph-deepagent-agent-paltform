"""Unit tests for the workflow registry bridge (spec-01 Phase 2).

The bridge is a host-level seam (``app/services/agents/workflow_bridge.py``)
that hands the process-wide ``WorkflowRegistry`` to ``WorkflowAppRuntime``,
which is built inside ``get_runtime`` with no request/app context. The
engine layer must never hold this global; the host layer owns it.
"""

import pytest

from app.services.agents import workflow_bridge
from app.workflow.registry import WorkflowRegistry

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_bridge() -> object:
    """Isolate each test: clear the bridge before and after."""
    workflow_bridge.reset_workflow_registry()
    yield
    workflow_bridge.reset_workflow_registry()


def test_get_registry_unset_raises() -> None:
    """Before injection, get_workflow_registry raises RuntimeError."""
    with pytest.raises(RuntimeError):
        workflow_bridge.get_workflow_registry()


def test_set_then_get_returns_same_instance() -> None:
    """After injection, the same registry instance is returned."""
    registry = WorkflowRegistry()
    workflow_bridge.set_workflow_registry(registry)
    assert workflow_bridge.get_workflow_registry() is registry


def test_reset_clears_registry() -> None:
    """reset_workflow_registry drops the instance (test isolation)."""
    workflow_bridge.set_workflow_registry(WorkflowRegistry())
    workflow_bridge.reset_workflow_registry()
    with pytest.raises(RuntimeError):
        workflow_bridge.get_workflow_registry()
