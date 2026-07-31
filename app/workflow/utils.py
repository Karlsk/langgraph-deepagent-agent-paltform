"""State conversion and output mapping helpers (spec-03, CONTRACT §4.6, C7 trimmed).

Only two public functions live here. Inputs are never mutated (S5).

Dependency red-line 3: no LLM / HTTP client library imports.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def convert_state_to_dict(state: Any) -> dict[str, Any]:
    """pydantic → model_dump()；dict 直通；其它 → {}（CONTRACT §4.6, S5）."""  # noqa: D403 — 'pydantic' is a library name, kept lowercase per CONTRACT §4.6
    if isinstance(state, dict):
        return state
    if isinstance(state, BaseModel):
        return state.model_dump()
    return {}


def map_output_to_state(
    node_name: str,
    node_output: dict[str, Any],
    state: dict[str, Any] | None = None,
    *,
    dual_write: bool = True,
    history_increment: bool = True,
) -> dict[str, Any]:
    """双写 + history 增量（CONTRACT §4.6, S3/S4/C4）."""
    if state is None:
        state = {}
    result: dict[str, Any] = {}
    # 双写 S4
    result[f"{node_name}_result"] = node_output
    if dual_write:
        result.update(node_output)
    # history 增量 C4/S3（仅返回增量 [entry]）
    if history_increment and isinstance(state.get("history"), list) and "history" not in node_output:
        entry = f"{node_name}: {str(node_output)[:100]}..."
        result["history"] = [entry]
    return result
