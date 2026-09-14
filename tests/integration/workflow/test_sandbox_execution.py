"""Integration tests for the sandbox child process (S22).

These spawn a real ``sandbox_worker.py`` — the unit suite stubs ``subprocess``
and therefore cannot observe the wire protocol, the worker's self-applied
rlimits, or the timeout kill. Zero network, zero LLM.

rlimit *enforcement* is asserted only through the worker's own applied/refused
report: whether a given limit bites is platform-dependent (darwin refuses
``RLIMIT_AS`` outright), so a behavioural assertion here would be flaky.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from app.workflow import sandbox_worker
from app.workflow.models import PythonNodeError
from app.workflow.sandbox import SandboxLimits, run_sandboxed

pytestmark = pytest.mark.integration

_WORKER = Path(sandbox_worker.__file__)
_CHILD_ENV = {"PATH": "/usr/bin:/bin"}


def _invoke_worker(
    code: str,
    state: dict[str, Any] | None = None,
    *,
    timeout_s: float = 10.0,
) -> subprocess.CompletedProcess[str]:
    """Run the worker directly, bypassing the parent's AST pre-check."""
    payload = json.dumps(
        {
            "code": code,
            "state": state or {},
            "limits": {"timeout_s": timeout_s, "max_memory_mb": 256, "max_output_bytes": 1_000_000},
        }
    )
    return subprocess.run(  # noqa: S603 — fixed argv, minimal env, no shell
        [sys.executable, "-I", str(_WORKER)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout_s + 5,
        env=_CHILD_ENV,
        check=False,
    )


def _invoke_raw(raw: str) -> subprocess.CompletedProcess[str]:
    """Send an arbitrary string to the worker instead of a JSON document."""
    return subprocess.run(  # noqa: S603 — fixed argv, minimal env, no shell
        [sys.executable, "-I", str(_WORKER)],
        input=raw,
        capture_output=True,
        text=True,
        timeout=15,
        env=_CHILD_ENV,
        check=False,
    )


def _document(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    """Decode the worker's single response document."""
    document: dict[str, Any] = json.loads(completed.stdout)
    return document


class TestRealExecution:
    """The happy path through a genuine child process."""

    def test_returns_computed_dict(self) -> None:
        """State crosses the boundary and the result comes back."""
        code = 'total = sum(state["values"])\nreturn {"total": total, "label": state["label"].upper()}'
        assert run_sandboxed(code, {"values": [1, 2, 3], "label": "ok"}) == {"total": 6, "label": "OK"}

    def test_print_does_not_pollute_protocol(self) -> None:
        """User print() is discarded, so stdout stays exactly one document."""
        code = 'print("noise")\nreturn {"n": len(state["items"])}'
        assert run_sandboxed(code, {"items": [1, 2]}) == {"n": 2}

    def test_non_dict_return_is_a_node_error(self) -> None:
        """R3: the node output contract is a dict."""
        with pytest.raises(PythonNodeError, match="dict"):
            run_sandboxed("return [1, 2, 3]", {})

    def test_missing_return_is_a_node_error(self) -> None:
        """Code that never returns yields None, which is not a dict."""
        with pytest.raises(PythonNodeError, match="dict"):
            run_sandboxed("x = 1", {})

    def test_unserializable_result_is_reported(self) -> None:
        """A dict whose values cannot cross back is a clean failure, not a crashed worker."""
        completed = _invoke_worker("return {'f': sum}")
        assert completed.returncode == 0
        document = _document(completed)
        assert document["ok"] is False
        assert "JSON-serializable" in document["error"]


class TestWorkerRlimits:
    """The worker applies its own limits and reports what the platform refused."""

    def test_refusal_is_reported_and_does_not_fail_the_run(self) -> None:
        """Every limit is accounted for, and a refusal still yields output.

        This is the exact darwin condition that broke the preexec_fn design:
        setrlimit(RLIMIT_AS) raises there, and raising inside preexec_fn aborted
        subprocess.run outright instead of degrading to a warning.
        """
        completed = _invoke_worker('return {"n": 1}')
        assert completed.returncode == 0, completed.stderr

        document = _document(completed)
        assert document["ok"] is True
        assert document["output"] == {"n": 1}

        report = document["limits"]
        names = {entry.split(":")[0] for entry in [*report["applied"], *report["refused"]]}
        assert names == {"RLIMIT_AS", "RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_NPROC"}


class TestSecondDefenseLine:
    """Even with the AST pre-check bypassed, the sandbox has no import capability."""

    def test_import_os_fails_at_runtime(self) -> None:
        """__import__ is absent from SAFE_BUILTINS, so the statement cannot resolve."""
        document = _document(_invoke_worker('import os\nreturn {"pid": os.getpid()}'))
        assert document["ok"] is False
        assert "ImportError" in document["error"]

    def test_open_is_undefined(self) -> None:
        """No file handle is reachable from user code."""
        document = _document(_invoke_worker('return {"f": open("/etc/passwd")}'))
        assert document["ok"] is False
        assert "NameError" in document["error"]

    def test_getattr_is_undefined(self) -> None:
        """The introspection builtin the dunder rule relies on is also gone at runtime."""
        document = _document(_invoke_worker('return {"x": getattr((), "join")()}'))
        assert document["ok"] is False
        assert "NameError" in document["error"]


class TestFailureExitCodes:
    """Exit codes 0/2/3 are the frozen protocol (S22)."""

    def test_syntax_error_exits_2_with_a_message(self) -> None:
        """Uncompilable code exits 2 and still reports a usable, code-free message."""
        completed = _invoke_worker("def broken(:\n  return")
        assert completed.returncode == 2
        document = _document(completed)
        assert document["ok"] is False
        assert "compile" in document["error"]
        assert "def broken" not in document["error"]

    def test_user_exception_exits_0(self) -> None:
        """A runtime failure is a well-formed business response, not a crash."""
        completed = _invoke_worker('raise ValueError("boom")')
        assert completed.returncode == 0
        document = _document(completed)
        assert document["ok"] is False
        assert "boom" in document["error"]

    def test_malformed_stdin_exits_3(self) -> None:
        """A request that is not JSON is a worker-level breakdown."""
        completed = _invoke_raw("not json")
        assert completed.returncode == 3
        assert _document(completed)["ok"] is False


class TestTimeout:
    """A hung child is killed rather than waited on forever."""

    def test_infinite_loop_times_out(self) -> None:
        """subprocess.run kills the child at timeout_s and the parent wraps it."""
        started = time.perf_counter()
        with pytest.raises(PythonNodeError, match="timed out"):
            run_sandboxed("while True:\n    pass", {}, SandboxLimits(timeout_s=1.0))
        assert time.perf_counter() - started < 8.0
