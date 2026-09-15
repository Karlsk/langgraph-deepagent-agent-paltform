"""Child-process entry point for sandboxed ``python`` nodes (S22).

This module is **never imported by the engine**: ``sandbox.run_sandboxed``
launches it as ``[sys.executable, "-I", <this file>]`` so the child runs in an
isolated interpreter with no access to the repository, site-packages or the
host environment.

Wire protocol (frozen in CONTRACT §6 "S22 细则"):

- stdin: exactly one JSON document ``{"code", "state", "limits"}``
- stdout: exactly one JSON document ``{"ok", "output"|"error", "limits"}``
- exit: ``0`` normal (including ``ok=false``), ``2`` the code failed to
  compile, ``3`` the worker itself broke down

Resource limits are applied **here**, after interpreter startup and before any
user code runs. Each ``setrlimit`` is tolerated independently: darwin refuses
``RLIMIT_AS`` outright, and a refusal must not take the whole run down with it.
"""

from __future__ import annotations

import builtins
import json
import math
import os
import resource
import sys
import textwrap
from typing import Any

_SAFE_NAMES = frozenset(
    {
        # pure computation
        "len",
        "range",
        "sorted",
        "min",
        "max",
        "sum",
        "abs",
        "round",
        "divmod",
        "pow",
        # containers and scalars
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "frozenset",
        "bytes",
        # iteration and inspection, none capability-bearing
        "enumerate",
        "zip",
        "map",
        "filter",
        "reversed",
        "isinstance",
        "issubclass",
        "print",
        # formatting
        "chr",
        "ord",
        "hex",
        "oct",
        "bin",
        "format",
        "repr",
        # exceptions user code may catch
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "ZeroDivisionError",
        "ArithmeticError",
        "LookupError",
        "RuntimeError",
        "StopIteration",
        "NotImplementedError",
    }
)

_ALLOWED_MODULES = frozenset(
    {
        "json",
        "re",
        "math",
        "statistics",
        "decimal",
        "fractions",
        "random",
        "datetime",
        "itertools",
        "bisect",
        "heapq",
        "copy",
        "string",
        "textwrap",
        "base64",
        "hashlib",
        "collections",
        "functools",
    }
)
"""Stdlib an ``import`` may name, by *root* module — a verbatim copy.

``sandbox.py`` holds the other copy: the worker is spawned as a child and must
never join the parent's import graph, so the constant cannot be shared by
import, and sending it over stdin would change a frozen protocol row.
``tests/unit/workflow/test_sandbox.py`` pins the two equal by parsing source.
"""


def _restricted_import(
    name: str,
    globals: Any = None,
    locals: Any = None,
    fromlist: Any = (),
    level: int = 0,
) -> Any:
    """Rule 1's runtime half: re-check the allowlist, then delegate.

    The signature mirrors CPython's ``__import__``. An ``import`` statement
    compiles to a lookup of ``__import__`` in the frame's builtins, so an entry
    is required for allowlisted modules to resolve at all. This is the worker's
    own gate — never ``builtins.__import__`` — and it exists because a caller may
    bypass the registration-time AST pre-check.
    """
    if level > 0:
        raise ImportError("relative imports are not allowed in the sandbox")
    root = name.split(".")[0]
    if root not in _ALLOWED_MODULES:
        raise ImportError(f"import of {root} is not allowed in the sandbox")
    return builtins.__import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS: dict[str, Any] = {
    **{name: getattr(builtins, name) for name in sorted(_SAFE_NAMES)},
    "__import__": _restricted_import,
}
"""The sandbox's entire ``__builtins__``.

Deliberately absent: ``open``/``exec``/``eval``/``compile``/``globals``/
``locals``/``vars``/``dir``/``getattr``/``setattr``/``delattr``/``type``/
``input``/``breakpoint``/``exit``/``quit``/``help``. ``__import__`` is present
but is ``_restricted_import``, so even code that slips past the AST pre-check
cannot reach ``socket``, ``urllib`` or the filesystem. ``True``/``False``/
``None`` are keywords, not builtins, so they need no entry here.
"""

_EXIT_NORMAL = 0
_EXIT_SYNTAX = 2
_EXIT_WORKER_FAILURE = 3

_ENTRY_FUNCTION = "_sandbox_entry"


class _Discard:
    """Swallows user ``print`` so the protocol stream stays exactly one document."""

    def write(self, *_: Any) -> int:
        """Drop the write and report zero characters consumed."""
        return 0

    def flush(self) -> None:
        """No-op: nothing is buffered."""


def _apply_limits(limits: dict[str, Any]) -> dict[str, list[str]]:
    """Apply each rlimit independently and report what the platform refused."""
    memory_bytes = int(limits.get("max_memory_mb", 256)) * 1024 * 1024
    cpu_seconds = math.ceil(float(limits.get("timeout_s", 10.0)))
    plan: list[tuple[str, int, tuple[int, int]]] = [
        ("RLIMIT_AS", resource.RLIMIT_AS, (memory_bytes, memory_bytes)),
        ("RLIMIT_CPU", resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds)),
        ("RLIMIT_FSIZE", resource.RLIMIT_FSIZE, (0, 0)),
    ]
    nproc = getattr(resource, "RLIMIT_NPROC", None)
    if nproc is not None:
        plan.append(("RLIMIT_NPROC", nproc, (0, 0)))

    applied: list[str] = []
    refused: list[str] = []
    for name, which, value in plan:
        try:
            resource.setrlimit(which, value)
        except (ValueError, OSError) as exc:
            refused.append(f"{name}:{type(exc).__name__}")
        else:
            applied.append(name)
    return {"applied": applied, "refused": refused}


def _emit(document: dict[str, Any]) -> None:
    """Write the single response document straight to fd 1, bypassing sys.stdout."""
    os.write(1, json.dumps(document).encode("utf-8"))


def _execute(code: str, state: dict[str, Any]) -> Any:
    """Run user code inside a function wrapper so a bare ``return`` works."""
    wrapped = f"def {_ENTRY_FUNCTION}(state):\n" + textwrap.indent(code, "    ")
    namespace: dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
    exec(wrapped, namespace)  # noqa: S102 — the whole point of this module, gated by AST + builtins
    return namespace[_ENTRY_FUNCTION](state)


def _fail(error: str, report: dict[str, list[str]], exit_code: int) -> int:
    """Emit an ``ok=false`` document and return the matching exit code."""
    _emit({"ok": False, "error": error, "limits": report})
    return exit_code


def main() -> int:
    """Read one request document, run it under the limits, write one response."""
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        _emit({"ok": False, "error": "worker received a malformed stdin document", "limits": {}})
        return _EXIT_WORKER_FAILURE

    report = _apply_limits(request.get("limits") or {})
    sys.stdout = _Discard()  # type: ignore[assignment]
    try:
        result = _execute(request.get("code") or "", request.get("state") or {})
    except SyntaxError as exc:
        return _fail(f"sandboxed code failed to compile: {exc.msg} (line {exc.lineno})", report, _EXIT_SYNTAX)
    except Exception as exc:  # noqa: BLE001 — every user-code failure becomes ok=false
        return _fail(f"{type(exc).__name__}: {exc}", report, _EXIT_NORMAL)

    if not isinstance(result, dict):
        hint = f"sandboxed code must return a dict, got {type(result).__name__}"
        return _fail(hint, report, _EXIT_NORMAL)
    try:
        _emit({"ok": True, "output": result, "limits": report})
    except TypeError:
        return _fail("sandboxed code returned a value that is not JSON-serializable", report, _EXIT_NORMAL)
    return _EXIT_NORMAL


if __name__ == "__main__":
    sys.exit(main())
