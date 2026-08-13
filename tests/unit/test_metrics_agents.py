"""Unit tests for agent/MCP Prometheus metrics definitions (T11)."""

import pytest
from prometheus_client import Counter, Histogram

from app.core import metrics


@pytest.mark.unit
def test_agent_metrics_importable() -> None:
    """All new agent/MCP metrics are defined and importable from app.core.metrics."""
    assert isinstance(metrics.agent_graph_compile_duration_seconds, Histogram)
    assert isinstance(metrics.agent_graph_cache_hits_total, Counter)
    assert isinstance(metrics.subagent_task_duration_seconds, Histogram)
    assert isinstance(metrics.agent_test_runs_total, Counter)
    assert isinstance(metrics.skill_sync_total, Counter)
    assert isinstance(metrics.mcp_tools_loaded_total, Counter)
    assert isinstance(metrics.mcp_client_rebuild_total, Counter)


@pytest.mark.unit
def test_agent_metrics_operations_do_not_raise() -> None:
    """Labelled metrics accept their declared labels and observe/inc without error."""
    metrics.agent_graph_compile_duration_seconds.observe(0.05)
    metrics.agent_graph_cache_hits_total.labels(result="hit").inc()
    metrics.agent_graph_cache_hits_total.labels(result="miss").inc()
    metrics.subagent_task_duration_seconds.labels(subagent="researcher").observe(1.2)
    metrics.agent_test_runs_total.labels(status="success").inc()
    metrics.skill_sync_total.labels(result="success").inc()
    metrics.mcp_tools_loaded_total.labels(server="filesystem", status="success").inc()
    metrics.mcp_client_rebuild_total.labels(reason="connection_lost").inc()
