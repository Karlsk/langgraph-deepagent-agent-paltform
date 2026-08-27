# Observability

## Overview

```mermaid
graph LR
    App["FastAPI App"]

    App -->|"LLM traces\n(every call)"| Langfuse
    App -->|"HTTP metrics\n(/metrics)"| Prometheus
    Prometheus --> Grafana
    App -->|"structured logs\n(stdout)"| Logs["Log aggregator\n(or stdout)"]
    App -->|"slow request profiles\n(DEBUG only)"| Profiles["JSON files\n(PROFILING_DIR)"]
```

---

## Langfuse — LLM tracing

Every LLM call is traced via the LangChain `CallbackHandler`. Traces include:

- Input messages and output
- Token usage and cost
- Latency per call and per session
- Model name, temperature, and other parameters

**Setup:**

```bash
LANGFUSE_TRACING_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted URL
```

**Disable for local dev:**

```bash
LANGFUSE_TRACING_ENABLED=false
```

Traces are also used as the data source for the [evaluation framework](evaluation.md).

---

## Structured logging

All logs use [structlog](https://www.structlog.org/) in a consistent format:

- **Development**: coloured console output
- **Production**: JSON (pipe to your log aggregator)

Every log line automatically carries `request_id`, `session_id`, and `user_id` when available — bound by `LoggingContextMiddleware`.

### Log format conventions

```python
# Good
logger.info("chat_request_received", session_id=session.id, message_count=5)

# Never
logger.info(f"chat request received for {session.id}")  # no f-strings
logger.error("something failed", error=str(e))          # use logger.exception for exceptions
```

Rules:

- Event names are `lowercase_with_underscores`
- Variables are keyword arguments, never interpolated into the event string
- Use `logger.exception()` (not `.error()`) when inside an `except` block — preserves the full traceback

### Log levels by environment

| Environment | Level |
| --- | --- |
| development | DEBUG |
| staging | INFO |
| production | WARNING |

---

## Prometheus metrics

Metrics are exposed at `GET /metrics` and scraped by Prometheus.

| Metric | Type | Description |
| --- | --- | --- |
| `http_requests_total` | Counter | Request count by method, endpoint, status |
| `http_request_duration_seconds` | Histogram | Request latency by method, endpoint |
| `llm_inference_duration_seconds` | Histogram | LLM call latency by model |
| `llm_stream_duration_seconds` | Histogram | Streaming call latency by model |
| `db_connections` | Gauge | Active database connections |

Grafana dashboards are pre-configured in `grafana/`. Start the full stack with `make stack-up ENV=development` to access them at [http://localhost:3000](http://localhost:3000) (admin/admin).

---

## Session debug export (G3)

Chat sessions are stored in three layers: **L0** metadata rows in PostgreSQL
(`sessions` table), **L1** LangGraph checkpoints (thread id == session id),
and **L2** product-level JSONL transcripts under
`{DATA_ROOT}/agents/<app_id>/users/<user_id>/sessions/<session_id>.jsonl`.

To inspect what a session actually recorded, use the export endpoint (the
project's first file-download endpoint — it bypasses the ApiResponse envelope
and streams with `Content-Disposition: attachment`):

```bash
# JSON: metadata header (session_id / name / agent_app_id / created_at /
# exported_at / message_count) + the message rows
curl -OJ -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/sessions/<session_id>/export?format=json"

# JSONL: one message row per line (application/x-ndjson)
curl -OJ -H "Authorization: Bearer $TOKEN" \
  "$BASE/api/v1/sessions/<session_id>/export?format=jsonl"
```

Semantics worth knowing when debugging:

- **L2 first, L1 fallback**: if the JSONL file is missing (e.g. the hook was
  skipped before G3), the export rebuilds rows from the L1 checkpoint and
  writes the file back (self-heal). If the bound AgentApp was deleted, only
  the surviving L2 content is returned — the export never 500s on orphans.
- **Ownership is 404, never 403** (anti-enumeration), same as every other
  `/sessions` endpoint.
- Empty transcripts export as a valid empty payload, not a 404.

Related structured log events: `session_created` / `session_renamed` /
`session_deleted` (the delete event carries `checkpoint_cleaned` and
`jsonl_cleaned` booleans describing which cascade layers succeeded), plus
`app_delete_checkpoint_cleanup_failed` for best-effort cleanup failures when
an AgentApp is hard-deleted. Compression activity is tracked by the
`context_compression_total` counter (see `app/core/metrics.py`).

---

## Request profiling (debug only)

When `DEBUG=true`, `ProfilingMiddleware` profiles every request using [pyinstrument](https://github.com/joerick/pyinstrument). When a request exceeds `PROFILING_THRESHOLD_SECONDS`, a JSON report is saved to `PROFILING_DIR`.

Each report file is named `{request_id}.json` and contains:

```json
{
  "request_id": "...",
  "endpoint": "POST /api/v1/chatbot/chat",
  "wall_time_ms": 1842,
  "cpu_time_ms": 145,
  "io_wait_ms": 1697,
  "memory_peak_kb": 4820,
  "top_memory_allocators": [...],
  "call_tree": {...}
}
```

Set `PROFILING_THRESHOLD_SECONDS=0` to profile every request.

The `request_id` in the filename matches the `X-Request-ID` response header, so you can correlate profiles with specific log lines.

---

## Request ID propagation

Every request gets a unique `X-Request-ID` header via [`asgi-correlation-id`](https://github.com/snok/asgi-correlation-id). This ID is:

- Returned in the response headers
- Bound to every log line for that request
- Used as the filename for profile reports

Use the `X-Request-ID` from a response to grep logs, find profiles, and look up Langfuse traces for that exact request.
