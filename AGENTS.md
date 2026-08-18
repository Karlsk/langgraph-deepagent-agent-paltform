# AI Agent Development Guide

This document provides essential guidelines for AI agents working on this LangGraph FastAPI Agent project.

## Quick Commands

```bash
make install              # Install deps (uv sync) + pre-commit hooks
make dev                  # Dev server with hot reload (port 8000)
make lint                 # ruff check .
make format               # ruff format .
make typecheck            # uv run pyright (static type check)
make check                # lint + typecheck
make eval                 # Run LLM evals (interactive)
make eval-quick           # Run LLM evals (default settings)
make migrate              # Run DB migrations to latest (Alembic)
make docker-up            # Docker: API + DB (ENV=development by default)
make stack-up ENV=development  # Full stack: API + DB + Prometheus + Grafana
```

> All server/DB/Docker targets accept `ENV=development|staging|production|test`.
> Run `make help` for the full list of targets.

## Project Structure

```
app/
  api/v1/          # Route handlers (auth.py, chatbot.py, api.py)
  core/
    config.py      # Pydantic Settings config
    database.py    # Async DB setup
    langgraph/     # LangGraph agent graph + tools
    logging.py     # structlog setup
    llm.py         # LLM service with retry logic
    limiter.py     # Rate limiting (slowapi)
    metrics.py     # Prometheus metrics
    middleware.py  # ASGI middleware
    prompts/       # System prompts
  models/          # SQLModel ORM models
  schemas/         # Pydantic request/response schemas + graph state
  services/        # Business logic services
  utils/           # Shared utilities
  workflow/        # Declarative workflow engine (YAML -> LangGraph), under construction
agent-web/         # Vue 3 + TS + Vite frontend (skeleton, see docs/frontend-spec.md)
evals/             # LLM evaluation framework (Langfuse-based)
scripts/           # Environment setup, Docker build scripts
docs/workflow-reimpl-plan/  # Workflow engine reimplementation plan + specs (contract-driven)
```

## Project Overview

This is a production-ready AI agent application built with:
- **LangGraph** for stateful, multi-step AI agent workflows
- **FastAPI** for high-performance async REST API endpoints
- **Langfuse** for LLM observability and tracing
- **PostgreSQL + pgvector** for long-term memory storage (mem0ai)
- **JWT authentication** with session management
- **Prometheus + Grafana** for monitoring

## Quick Reference: Critical Rules

### Import Rules
- **All imports MUST be at the top of the file** - never add imports inside functions or classes

### Logging Rules
- Use **structlog** for all logging
- Log messages must be **lowercase_with_underscores** (e.g., `"user_login_successful"`)
- **NO f-strings in structlog events** - pass variables as kwargs
- Use `logger.exception()` instead of `logger.error()` to preserve tracebacks
- Example: `logger.info("chat_request_received", session_id=session.id, message_count=len(messages))`

### Retry Rules
- **Always use tenacity library** for retry logic
- Configure with exponential backoff
- Example: `@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))`

### Output Rules
- **Always enable rich library** for formatted console outputs
- Use rich for progress bars, tables, panels, and formatted text

### Caching Rules
- **Only cache successful responses**, never cache errors
- Use appropriate cache TTL based on data volatility

### FastAPI Rules
- All routes must have rate limiting decorators
- Use dependency injection for services, database connections, and auth
- All database operations must be async

## Code Style Conventions

### Python/FastAPI
- Use `async def` for asynchronous operations
- Use type hints for all function signatures
- Prefer Pydantic models over raw dictionaries
- Use functional, declarative programming; avoid classes except for services and agents
- File naming: lowercase with underscores (e.g., `user_routes.py`)
- Use the RORO pattern (Receive an Object, Return an Object)

### Error Handling
- Handle errors at the beginning of functions
- Use early returns for error conditions
- Place the happy path last in the function
- Use guard clauses for preconditions
- Use `HTTPException` for expected errors with appropriate status codes

## LangGraph & LangChain Patterns

### Graph Structure
- Use `StateGraph` for building AI agent workflows
- Define clear state schemas using Pydantic models (see `app/schemas/graph.py`)
- Use `CompiledStateGraph` for production workflows
- Implement `AsyncPostgresSaver` for checkpointing and persistence
- Use `Command` for controlling graph flow between nodes

### Tracing
- Use LangChain's `CallbackHandler` from Langfuse for tracing all LLM calls
- All LLM operations must have Langfuse tracing enabled

### Memory (mem0ai)
- Use `AsyncMemory` for semantic memory storage
- Store memories per user_id for personalized experiences
- Use async methods: `add()`, `get()`, `search()`, `delete()`

## Authentication & Security

- Use JWT tokens for authentication
- Implement session-based user management (see `app/api/v1/auth.py`)
- Use `get_current_session` dependency for protected endpoints
- Store sensitive data in environment variables
- Validate all user inputs with Pydantic models

## Database Operations

- Use SQLModel for ORM models (combines SQLAlchemy + Pydantic)
- Define models in `app/models/` directory
- Use async database operations with asyncpg
- Use LangGraph's AsyncPostgresSaver for agent checkpointing

## Performance Guidelines

- Minimize blocking I/O operations
- Use async for all database and external API calls
- Implement caching for frequently accessed data
- Use connection pooling for database connections
- Optimize LLM calls with streaming responses

## Observability

- Integrate Langfuse for LLM tracing on all agent operations
- Export Prometheus metrics for API performance
- Use structured logging with context binding (request_id, session_id, user_id)
- Track LLM inference duration, token usage, and costs

## Testing & Evaluation

- Implement metric-based evaluations for LLM outputs (see `evals/` directory)
- Create custom evaluation metrics as markdown files in `evals/metrics/prompts/`
- Use Langfuse traces for evaluation data sources
- Generate JSON reports with success rates

## Configuration Management

- Use environment-specific configuration files (`.env.development`, `.env.staging`, `.env.production`)
- Use Pydantic Settings for type-safe configuration (see `app/core/config.py`)
- Never hardcode secrets or API keys

## Key Dependencies

- **FastAPI** - Web framework
- **LangGraph** - Agent workflow orchestration
- **LangChain** - LLM abstraction and tools
- **Langfuse** - LLM observability and tracing
- **Pydantic v2** - Data validation and settings
- **structlog** - Structured logging
- **mem0ai** - Long-term memory management
- **PostgreSQL + pgvector** - Database and vector storage
- **SQLModel** - ORM for database models
- **tenacity** - Retry logic
- **rich** - Terminal formatting
- **slowapi** - Rate limiting
- **prometheus-client** - Metrics collection

## 10 Commandments for This Project

1. All routes must have rate limiting decorators
2. All LLM operations must have Langfuse tracing
3. All async operations must have proper error handling
4. All logs must follow structured logging format with lowercase_underscore event names
5. All retries must use tenacity library
6. All console outputs should use rich formatting
7. All caching should only store successful responses
8. All imports must be at the top of files
9. All database operations must be async
10. All endpoints must have proper type hints and Pydantic models
11. All code must pass `make typecheck` (pyright standard mode)

## Common Pitfalls to Avoid

- ❌ Using f-strings in structlog events
- ❌ Adding imports inside functions
- ❌ Forgetting rate limiting decorators on routes
- ❌ Missing Langfuse tracing on LLM calls
- ❌ Caching error responses
- ❌ Using `logger.error()` instead of `logger.exception()` for exceptions
- ❌ Blocking I/O operations without async
- ❌ Hardcoding secrets or API keys
- ❌ Missing type hints on function signatures

## When Making Changes

Before modifying code:
1. Read the existing implementation first
2. Check for related patterns in the codebase
3. Ensure consistency with existing code style
4. Add appropriate logging with structured format
5. Include error handling with early returns
6. Add type hints and Pydantic models
7. Verify Langfuse tracing is enabled for LLM calls

## Workflow Engine Reimplementation (`docs/workflow-reimpl-plan/`)

The project includes a contract-driven plan to reimplement a declarative workflow engine
(YAML DSL -> LangGraph compiled graph + process-level registry runtime) in `app/workflow/`.
This scope covers only `BaseNode` (abstract), `LLMNode`, and `HTTPNode`.

### Authority Hierarchy (on conflict, higher wins)

```
spec/CONTRACT.md  >  spec/spec-00..09  >  planning docs (00-03)  >  original code conventions
```

### Document Map

| Document | Purpose |
|---|---|
| `docs/workflow-reimpl-plan/README.md` | Navigation, scope, K/C/H/R quick-reference tables |
| `00-架构总览.md` | Architecture: 3 layers (DSL models -> graph compile -> registry runtime), K1..K10 |
| `01-分阶段开发计划.md` | Phase 0..9 task lists, TDD steps, DoD per phase |
| `02-开发规范.md` | Red lines R1..R10, code/test/logging/commit conventions |
| `03-隐患修复方案.md` | Hazard archive H1..H7 and cleanup items C1..C9 (root cause -> fix -> verify) |
| `spec/CONTRACT.md` | **Coding contract (highest authority)**: frozen interface signatures (§4), exception family (§5), behavior semantics S1-S16 (§6), exploration gate R-EXP (§7), red-line machine gates (§10), adaptation decisions AD-01..12 (§9, single source) |
| `spec/README.md` | Spec overview: effort table, milestone chain M0-M8, spec DAG, AI delegation prompt template |
| `spec/api-exploration-1x.md` | LangGraph/LangChain 1.x API exploration tasks (EXP-G/C/L/X); closing them gates coding work |
| `spec/spec-00..09` | Per-phase executable task cards (TC), TDD points, DoD, acceptance commands |

### Numbering System (never invent synonyms; always cite by number)

- **K1..K10** keep-items (proven designs to preserve) · **C1..C9** cleanups · **H1..H7** hazards · **R1..R10** red lines · **Phase 0..9** stages
- **AD-01..12** adaptation decisions — defined ONLY in `CONTRACT.md` §9; specs reference by number
- **S1..S16** behavior semantics · **EXP-G/C/L/X** exploration items · **M0..M8** milestones

### Hard Rules When Implementing Workflow Specs

1. **Read first**: `CONTRACT.md` in full + the target `spec-0N` + the EXP records gating that spec. R-EXP: coding must not start before the gating EXP items are closed in `api-exploration-1x.md`.
2. **Frozen signatures**: public interfaces must match CONTRACT §4 verbatim. On any contract conflict: stop and ask — never improvise a compromise.
3. **Scope discipline (R1)**: implement only the TC cards of the current spec; no new node types, no hardcoded business field names (R2), no hardcoded secrets (R5).
4. **Strict TDD (R7)**: RED -> GREEN -> REFACTOR; coverage >= 80%; zero real network / real LLM calls in tests.
5. **Dependency direction**: `app/workflow/` modules depend only on themselves + third-party libs; they must NOT import `app.core.*` (entry/integration points in spec-08 are the sole exception, per AD-02 composition-root rules).
6. **Per TC card**: run `uv run pytest -m unit`, `make lint`, `make typecheck`; commit with conventional commits.
7. Tests live in `tests/unit/workflow/` and `tests/integration/workflow/` (AD-08).

### Milestones

```
M0(scaffold+EXP) -> M1(models) -> M2(state) -> M3(node infra) -> M4(LLMNode ∥ HTTPNode)
  -> M5(graph builder) -> M6(registry/runtime) -> M7(entry/CLI) -> M8(hardening)
```

EXP closure is the precondition for M1. Each milestone exits only when its spec's DoD is fully green.

## Frontend (agent-web)

`agent-web/` is the Vue 3 + TypeScript + Vite frontend skeleton (Element Plus, blue-white card layout) for this agent console; it currently contains only scaffolding, routing and placeholder views. See `docs/frontend-spec.md` for the full spec.

Skeleton-stage red lines:

- No business logic yet — views are placeholders; do not implement features ahead of the plan.
- Do NOT introduce state management (Pinia etc.), SSR frameworks (Nuxt), or monorepo tooling on your own.
- JSON config files (`tsconfig*.json`, `package.json`) must stay strict JSON — no comments allowed (pre-commit `check-json`).
- All HTTP requests go through `agent-web/src/utils/request.ts` (baseURL `/api/v1`, proxied via `BACKEND_URL`); if backend `API_V1_STR` changes, sync the Vite proxy rule and `request.ts` baseURL together.
- Design conventions (design tokens / page skeleton / button rules), code conventions, the five base building blocks (`WebAgentTable` / `WebAgentFormDialog` / `useConfirm` / `useRequest` / `notify`) and the unified request-layer contract are documented in `agent-web/README.md` — read it before adding frontend features.

## References

- LangGraph Documentation: https://langchain-ai.github.io/langgraph/
- LangChain Documentation: https://python.langchain.com/docs/
- FastAPI Documentation: https://fastapi.tiangolo.com/
- Langfuse Documentation: https://langfuse.com/docs
