"""State conversion and output mapping helpers (spec-03, CONTRACT §4.6, C7 trimmed).

Only two public functions live here. Inputs are never mutated (S5).

Dependency red-line 3: no LLM / HTTP client library imports.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def resolve_dot_path(data: dict[str, Any], path: str) -> Any:
    """Walk a dot-separated path into nested dicts; returns None on any miss."""
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


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
    dual_write: bool = False,
    history_increment: bool = True,
    state_keys: list[str] | None = None,
) -> dict[str, Any]:
    """{name}_result 写入 + 可选双写 + history 增量 + 可选 state_keys 直通（CONTRACT §4.6, S3/S4/C4）."""
    if state is None:
        state = {}
    result: dict[str, Any] = {}
    # 双写 S4
    result[f"{node_name}_result"] = node_output
    if dual_write:
        result.update(node_output)
    # state_keys 直通：指定的 key 同时写入顶层 state channel（用于 reducer / 条件路由）
    for key in state_keys or []:
        if key in node_output:
            result[key] = node_output[key]
    # history 增量 C4/S3（仅返回增量 [entry]）
    if history_increment and isinstance(state.get("history"), list) and "history" not in node_output:
        entry = f"{node_name}: {str(node_output)[:100]}..."
        result["history"] = [entry]
    return result
