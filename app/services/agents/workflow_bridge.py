"""Host-level bridge handing the process WorkflowRegistry to the runtime layer.

``get_runtime`` builds a ``WorkflowAppRuntime`` outside any request/app scope,
so it cannot reach ``app.state.workflow_registry`` directly. The composition
root (``app/main.py``) injects the registry here at startup; the runtime reads
it back. This seam lives in the host layer (``app/services/agents``) on purpose:
the workflow engine (``app/workflow``) must never hold process-global state.
"""

from app.workflow.registry import WorkflowRegistry

_REGISTRY: WorkflowRegistry | None = None


def set_workflow_registry(registry: WorkflowRegistry) -> None:
    """Install the process-wide workflow registry (called once at startup)."""
    global _REGISTRY  # noqa: PLW0603 — module-level bridge seam
    _REGISTRY = registry


def get_workflow_registry() -> WorkflowRegistry:
    """Return the injected registry.

    Raises:
        RuntimeError: When the registry was never injected (startup wiring
            missing) — fail loudly rather than serve a chatflow app with no
            engine behind it.
    """
    if _REGISTRY is None:
        raise RuntimeError("workflow registry not initialized; call set_workflow_registry at startup")
    return _REGISTRY


def reset_workflow_registry() -> None:
    """Drop the injected registry (test isolation / shutdown hook)."""
    global _REGISTRY  # noqa: PLW0603 — module-level bridge seam
    _REGISTRY = None
