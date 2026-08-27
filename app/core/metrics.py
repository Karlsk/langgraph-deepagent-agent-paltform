"""Prometheus metrics configuration for the application.

This module sets up and configures Prometheus metrics for monitoring the application.
"""

from prometheus_client import Counter, Histogram, Gauge
from starlette_prometheus import metrics, PrometheusMiddleware

# Request metrics
http_requests_total = Counter("http_requests_total", "Total number of HTTP requests", ["method", "endpoint", "status"])

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds", "HTTP request duration in seconds", ["method", "endpoint"]
)

# Database metrics
db_connections = Gauge("db_connections", "Number of active database connections")

# Custom business metrics
orders_processed = Counter("orders_processed_total", "Total number of orders processed")

llm_inference_duration_seconds = Histogram(
    "llm_inference_duration_seconds",
    "Time spent processing LLM inference",
    ["model"],
    buckets=[0.1, 0.3, 0.5, 1.0, 2.0, 5.0],
)


llm_stream_duration_seconds = Histogram(
    "llm_stream_duration_seconds",
    "Time spent processing LLM stream inference",
    ["model"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0],
)

# Agent graph metrics
agent_graph_compile_duration_seconds = Histogram(
    "agent_graph_compile_duration_seconds",
    "Time spent compiling the agent graph",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

agent_graph_cache_hits_total = Counter(
    "agent_graph_cache_hits_total",
    "Total agent graph cache lookups",
    ["result"],  # "hit" | "miss"
)

subagent_task_duration_seconds = Histogram(
    "subagent_task_duration_seconds",
    "Time spent executing subagent tasks",
    ["subagent"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

agent_test_runs_total = Counter(
    "agent_test_runs_total",
    "Total agent test runs executed",
    ["status"],  # "success" | "error"
)

# G3 session-layer metrics (spec-g3-session §4.2)
context_compression_total = Counter(
    "context_compression_total",
    "Total context-window compression events (SummarizationMiddleware)",
    ["app_id", "status"],  # status: "occurred"
)

skill_sync_total = Counter(
    "skill_sync_total",
    "Total skill synchronization operations",
    ["result"],  # "success" | "error"
)

mcp_tools_loaded_total = Counter(
    "mcp_tools_loaded_total",
    "Total MCP tools loaded per server",
    ["server", "status"],  # status: "success" | "error"
)

mcp_client_rebuild_total = Counter(
    "mcp_client_rebuild_total",
    "Total MCP client rebuilds",
    ["reason"],
)

mcp_session_stop_total = Counter(
    "mcp_session_stop_total",
    "Total pooled MCP session worker stops",
    ["outcome"],  # "graceful" | "timeout_cancelled" | "crashed" | "cancelled" | "foreign_loop"
)

# Auth metrics — Phase 1 G1 single-layer auth with refresh tokens
auth_refresh_total = Counter(
    "auth_refresh_total",
    "Total /auth/refresh calls",
    ["status"],  # "success" | "replay_detected" | "invalid" | "expired"
)

auth_refresh_replay_total = Counter(
    "auth_refresh_replay_total",
    "Refresh token replay attempts (alert when > 0)",
)

auth_logout_total = Counter(
    "auth_logout_total",
    "Total /auth/logout calls",
)

refresh_token_active_count = Gauge(
    "refresh_token_active_count",
    "Currently active (non-revoked, non-expired) refresh tokens",
)


def setup_metrics(app):
    """Set up Prometheus metrics middleware and endpoints.

    Args:
        app: FastAPI application instance
    """
    # Add Prometheus middleware
    app.add_middleware(PrometheusMiddleware)

    # Add metrics endpoint
    app.add_route("/metrics", metrics)
