"""Sandboxed execution for ``python`` workflow nodes (S22, CONTRACT §4.14).

Two halves:

- ``validate_code_ast`` — a static pre-check shared by registration (S18) and
  execution, so illegal code is rejected before a child is ever spawned.
- ``run_sandboxed`` — launches ``sandbox_worker.py`` as an isolated child and
  translates every failure mode into the frozen exception family (§5).

Resource limits are applied by the *worker*, not by this module: on darwin
``setrlimit(RLIMIT_AS)`` raises, and a raising ``preexec_fn`` aborts
``subprocess.run`` outright, so the caps ride on stdin and the worker reports
back which ones it could honour.

Dependency red-line (AD-02): stdlib + ``app.workflow.models`` only.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import structlog

from app.workflow.models import PythonNodeError, WorkflowValidationError

logger = structlog.get_logger(__name__)

_MAX_CODE_BYTES = 65536
"""Hard cap on the code body (rule 4)."""

_FORBIDDEN_CALLS = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "open",
        "__import__",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "type",
        "input",
        "breakpoint",
        "exit",
        "quit",
        "help",
    }
)
"""Capability-bearing builtins rejected by name (rule 2)."""

_WORKER_PATH = Path(__file__).with_name("sandbox_worker.py")

_CHILD_ENV = {"PATH": "/usr/bin:/bin"}
"""The child inherits nothing else: no API keys, no DB DSN (H6/R5)."""


@dataclass(frozen=True)
class SandboxLimits:
    """沙箱资源上限（由 worker 逐项自我施加，失败项经 applied/refused 回报，见 S22 细则）。."""

    timeout_s: float = 10.0
    max_memory_mb: int = 256
    max_output_bytes: int = 1_000_000


def validate_code_ast(code: str) -> None:
    """静态拒绝不可沙箱化的代码。通过返回 None，否则抛 WorkflowValidationError。.

    规则（命中即拒；消息含行号 + 规则名，绝不含代码正文，H6）：
      1. import / from ... import（ast.Import / ast.ImportFrom）——全禁，无白名单模块
      2. 危险调用名（ast.Call.func 为 ast.Name 且 id ∈ _FORBIDDEN_CALLS）
      3. dunder 标识符：任何 ast.Name.id 或 ast.Attribute.attr 含 "__"
      4. 体积 > _MAX_CODE_BYTES（64 KiB）

    Args:
        code: The node's inline code body.

    Raises:
        WorkflowValidationError: Any rule fired, or the code does not parse.
    """
    if len(code.encode("utf-8")) > _MAX_CODE_BYTES:
        raise _reject("code-too-large", 0, f"{_MAX_CODE_BYTES} bytes max")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise _reject("syntax-error", exc.lineno or 0, exc.msg) from exc
    for node in ast.walk(tree):
        _check_node(node)


def run_sandboxed(code: str, state: dict[str, Any], limits: SandboxLimits = SandboxLimits()) -> dict[str, Any]:
    """在子进程沙箱中执行 code，返回其 dict 结果。.

    执行前复跑 validate_code_ast（纵深防御：调用方可能绕过注册期校验）。
    超时 / 非零退出 / worker 报 ok=false / 输出非 dict → 一律抛 PythonNodeError。

    Args:
        code: The node's inline code body.
        state: Plain-dict snapshot of the workflow state handed to the child.
        limits: Resource caps forwarded to the worker.

    Returns:
        The dict the sandboxed code produced.

    Raises:
        WorkflowValidationError: The code failed the AST pre-check.
        PythonNodeError: Any execution, protocol or timeout failure.
    """
    validate_code_ast(code)
    started = time.perf_counter()
    completed = _spawn(code, state, limits)
    duration_ms = (time.perf_counter() - started) * 1000
    document = _read_document(completed, limits)
    _report_limits(document.get("limits"))
    logger.debug(
        "sandbox_execution_completed",
        sandboxed=True,
        code_chars=len(code),
        exit_code=completed.returncode,
        duration_ms=round(duration_ms, 2),
    )
    return _extract_output(document)


def _reject(rule: str, lineno: int, detail: str) -> WorkflowValidationError:
    """Build a rejection carrying line + rule name and never the code body (H6)."""
    return WorkflowValidationError(f"sandbox code rejected [{rule}] at line {lineno}: {detail}")


def _check_node(node: ast.AST) -> None:
    """Apply rules 1-3 to a single AST node."""
    if isinstance(node, ast.Import | ast.ImportFrom):
        raise _reject("no-import", node.lineno, "imports are not allowed in sandboxed code")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in _FORBIDDEN_CALLS:
            raise _reject("forbidden-call", node.func.lineno, f"{node.func.id}() is not allowed")
    if isinstance(node, ast.Name) and "__" in node.id:
        raise _reject("dunder-identifier", node.lineno, "dunder names are not allowed")
    if isinstance(node, ast.Attribute) and "__" in node.attr:
        raise _reject("dunder-identifier", node.lineno, "dunder attributes are not allowed")


def _spawn(code: str, state: dict[str, Any], limits: SandboxLimits) -> subprocess.CompletedProcess[str]:
    """Launch the isolated worker with exactly one JSON document on stdin.

    No ``preexec_fn``: the worker applies its own rlimits (S22). No explicit
    ``stdin=PIPE`` either — ``input=`` already implies it, and passing both makes
    ``subprocess.run`` raise.
    """
    try:
        payload = json.dumps({"code": code, "state": state, "limits": asdict(limits)})
    except TypeError as exc:
        msg = f"sandbox state is not JSON-serializable: {exc}"
        raise PythonNodeError(msg) from exc
    argv = [sys.executable, "-I", str(_WORKER_PATH)]
    try:
        return subprocess.run(  # noqa: S603 — fixed argv, empty env, no shell
            argv,
            input=payload,
            capture_output=True,
            text=True,
            timeout=limits.timeout_s,
            env=_CHILD_ENV,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"sandboxed code timed out after {limits.timeout_s}s"
        raise PythonNodeError(msg) from exc


def _read_document(completed: subprocess.CompletedProcess[str], limits: SandboxLimits) -> dict[str, Any]:
    """Turn the child's stdout into the single JSON document the protocol promises.

    A parseable document wins over the exit code: the worker exits 2 for a
    SyntaxError but still reports the actionable message, and stderr is never
    surfaced because a traceback would echo the code body (H6).
    """
    stdout = completed.stdout or ""
    if len(stdout.encode("utf-8")) > limits.max_output_bytes:
        msg = f"sandbox output exceeds {limits.max_output_bytes} bytes"
        raise PythonNodeError(msg)
    try:
        document = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise _no_document_error(completed) from exc
    if not isinstance(document, dict):
        msg = "sandbox worker broke the protocol: document is not a JSON object"
        raise PythonNodeError(msg)
    return document


def _no_document_error(completed: subprocess.CompletedProcess[str]) -> PythonNodeError:
    """Classify a child that produced no usable document."""
    if completed.returncode != 0:
        return PythonNodeError(f"sandbox worker failed with exit code {completed.returncode}")
    return PythonNodeError("sandbox worker broke the protocol: stdout is not a JSON document")


def _report_limits(report: Any) -> None:
    """Surface the worker's rlimit report; a refusal warns but never fails the run."""
    if not isinstance(report, dict):
        return
    refused = report.get("refused") or []
    if refused:
        logger.warning("sandbox_rlimit_partial", refused=list(refused), platform=sys.platform)


def _extract_output(document: dict[str, Any]) -> dict[str, Any]:
    """Validate the ``ok``/``output`` half of the document (R3: output is a dict)."""
    if not document.get("ok"):
        msg = f"sandboxed code failed: {document.get('error') or 'unknown error'}"
        raise PythonNodeError(msg)
    output = document.get("output")
    if not isinstance(output, dict):
        msg = "sandboxed code must produce a dict output"
        raise PythonNodeError(msg)
    return output
