"""Unit tests for app.workflow.sandbox (CONTRACT §4.14, S22).

Scope of this module: pure functions and the *invocation contract* of
``run_sandboxed`` (asserted with a stubbed ``subprocess.run``), so no child
process is ever spawned here. Behaviour that needs a real process — the wire
protocol, timeout kill, rlimits — lives in
``tests/integration/workflow/test_sandbox_execution.py``.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from typing import Any

import pytest

from app.workflow import sandbox, sandbox_worker
from app.workflow.models import PythonNodeError, WorkflowValidationError
from app.workflow.sandbox import SandboxLimits, run_sandboxed, validate_code_ast
from app.workflow.sandbox_worker import SAFE_BUILTINS

pytestmark = pytest.mark.unit

VALID_CODE = 'total = sum(state["values"])\nreturn {"total": total, "label": state["label"].upper()}'


# ─── §4.14 rule 1: imports ────────────────────────────────────────────


class TestNoImport:
    """Every import form is rejected; there is no allowed-module whitelist."""

    @pytest.mark.parametrize(
        "code",
        [
            "import os",
            "import os, sys",
            "from os import path",
            "from . import sibling",
            "import socket",
            "x = 1\nimport json\nreturn {}",
        ],
    )
    def test_rejects_import(self, code: str) -> None:
        """Import statements raise the no-import rule."""
        with pytest.raises(WorkflowValidationError, match="no-import"):
            validate_code_ast(code)

    def test_accepts_code_without_import(self) -> None:
        """Pure computation passes the pre-check."""
        assert validate_code_ast(VALID_CODE) is None


# ─── §4.14 rule 2: forbidden calls ────────────────────────────────────


class TestForbiddenCalls:
    """Capability-bearing builtins are rejected by name."""

    @pytest.mark.parametrize(
        "name",
        [
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
        ],
    )
    def test_rejects_forbidden_call(self, name: str) -> None:
        """Calling a forbidden builtin by name raises the forbidden-call rule."""
        with pytest.raises(WorkflowValidationError, match="forbidden-call"):
            validate_code_ast(f"result = {name}('x')\nreturn {{}}")

    def test_accepts_pure_builtins(self) -> None:
        """len/sorted/min/str and friends are allowed."""
        code = 'items = sorted(values, key=len)\nreturn {"items": items, "n": len(items), "s": str(items)}'
        assert validate_code_ast(code) is None

    def test_accepts_method_calls(self) -> None:
        """Rule 2 only inspects bare-name callees, so attribute calls stay legal."""
        code = 'items = list(values)\nitems.append(1)\nreturn {"items": items}'
        assert validate_code_ast(code) is None


# ─── §4.14 rule 3: dunder identifiers ─────────────────────────────────


class TestDunderIdentifiers:
    """Introspection escape chains are cut at the identifier level."""

    @pytest.mark.parametrize(
        "code",
        [
            "return {'x': ().__class__}",
            "return {'x': ''.__class__.__bases__}",
            "return {'x': __builtins__}",
            "return {'x': globals()}",  # also rule 2; either rule may fire
        ],
    )
    def test_rejects_dunder(self, code: str) -> None:
        """Any dunder attribute or name is rejected."""
        with pytest.raises(WorkflowValidationError, match="dunder-identifier|forbidden-call"):
            validate_code_ast(code)

    def test_accepts_plain_attribute(self) -> None:
        """Non-dunder attribute access is fine."""
        assert validate_code_ast('return {"u": label.upper()}') is None


# ─── §4.14 rule 4: size cap ───────────────────────────────────────────


class TestCodeSize:
    """64 KiB hard cap on the code body."""

    def test_max_code_bytes_is_64kib(self) -> None:
        """The frozen cap is 64 KiB."""
        assert sandbox._MAX_CODE_BYTES == 65536

    def test_rejects_oversize_code(self) -> None:
        """One byte over the cap is rejected."""
        code = "x = '" + "a" * sandbox._MAX_CODE_BYTES + "'\nreturn {}"
        with pytest.raises(WorkflowValidationError, match="code-too-large"):
            validate_code_ast(code)

    def test_accepts_code_at_cap(self) -> None:
        """Exactly at the cap is still accepted."""
        code = "# " + "a" * (sandbox._MAX_CODE_BYTES - 4)
        assert len(code.encode()) <= sandbox._MAX_CODE_BYTES
        assert validate_code_ast(code) is None


# ─── §4.14 error message shape (H6) ───────────────────────────────────


class TestRejectionMessage:
    """Messages carry line + rule name and never the code body."""

    def test_message_carries_line_and_rule(self) -> None:
        """The offending line number and the rule name are both present."""
        code = 'value = 1\nimport os\nreturn {"v": value}'
        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_code_ast(code)
        message = str(exc_info.value)
        assert "no-import" in message
        assert "line 2" in message

    def test_message_never_echoes_code_body(self) -> None:
        """No source line of the rejected code appears in the message (H6/S15)."""
        code = 'secret_marker = "hunter2"\nopen(secret_marker)\nreturn {}'
        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_code_ast(code)
        message = str(exc_info.value)
        assert "import os" not in message
        for line in code.splitlines():
            assert line.strip() not in message

    def test_syntax_error_is_rejected_without_echo(self) -> None:
        """Unparseable code is a validation failure, not a SyntaxError leak."""
        with pytest.raises(WorkflowValidationError, match="syntax-error") as exc_info:
            validate_code_ast("def broken(:\n  return")
        assert "def broken" not in str(exc_info.value)


# ─── §4.14 SandboxLimits ──────────────────────────────────────────────


class TestSandboxLimits:
    """Frozen resource caps with the contract defaults."""

    def test_defaults(self) -> None:
        """Defaults are 10s / 256 MB / 1 MB."""
        limits = SandboxLimits()
        assert limits.timeout_s == 10.0
        assert limits.max_memory_mb == 256
        assert limits.max_output_bytes == 1_000_000

    def test_is_frozen(self) -> None:
        """The dataclass is immutable so a shared default cannot be mutated."""
        limits = SandboxLimits()
        with pytest.raises(dataclasses.FrozenInstanceError):
            limits.timeout_s = 999.0  # type: ignore[misc]


# ─── S22: run_sandboxed invocation contract ───────────────────────────


class _Recorder:
    """Captures the subprocess.run call and replays a canned CompletedProcess."""

    def __init__(self, *, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self._stdout = stdout
        self._returncode = returncode
        self._stderr = stderr

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], self._returncode, self._stdout, self._stderr)

    @property
    def kwargs(self) -> dict[str, Any]:
        """Keyword arguments of the most recent recorded call."""
        return self.calls[-1][1]

    @property
    def argv(self) -> list[str]:
        """Command vector of the most recent recorded call."""
        return list(self.calls[-1][0][0])


_ALL_LIMITS = ("RLIMIT_AS", "RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_NPROC")


def _ok(output: dict[str, Any], *, refused: list[str] | None = None) -> str:
    """A well-formed worker success document, including the rlimit report."""
    refused = refused or []
    applied = [name for name in _ALL_LIMITS if not any(item.startswith(name) for item in refused)]
    return json.dumps(
        {
            "ok": True,
            "output": output,
            "limits": {"applied": applied, "refused": refused},
        }
    )


def _payload(recorder: _Recorder) -> dict[str, Any]:
    """Decoded stdin document of the recorded call."""
    raw = recorder.kwargs["input"]
    return json.loads(raw.decode() if isinstance(raw, bytes) else raw)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Stub subprocess.run so these tests never spawn a child."""
    rec = _Recorder(stdout=_ok({"echo": 1}))
    monkeypatch.setattr(sandbox.subprocess, "run", rec)
    return rec


class TestRunSandboxedInvocation:
    """How the child is launched is a frozen security property (S22)."""

    def test_launches_worker_isolated(self, recorder: _Recorder) -> None:
        """The command vector is [sys.executable, '-I', <sandbox_worker.py>]."""
        run_sandboxed(VALID_CODE, {})
        argv = recorder.argv
        assert argv[0] == sys.executable
        assert argv[1] == "-I"
        assert argv[2].endswith("sandbox_worker.py")

    def test_does_not_inherit_host_env(self, recorder: _Recorder) -> None:
        """The child env is exactly a minimal PATH: no API keys, no DB DSN (H6/R5)."""
        run_sandboxed(VALID_CODE, {})
        assert recorder.kwargs["env"] == {"PATH": "/usr/bin:/bin"}

    def test_passes_timeout(self, recorder: _Recorder) -> None:
        """subprocess.run gets the limit so the stdlib kills a hung child."""
        run_sandboxed(VALID_CODE, {})
        assert recorder.kwargs["timeout"] == 10.0
        run_sandboxed(VALID_CODE, {}, SandboxLimits(timeout_s=0.25))
        assert recorder.kwargs["timeout"] == 0.25

    def test_captures_pipes(self, recorder: _Recorder) -> None:
        """stdout/stderr are captured and the payload rides on input.

        No explicit ``stdin`` kwarg: ``input=`` already implies a pipe, and
        passing both makes ``subprocess.run`` raise ValueError.
        """
        run_sandboxed(VALID_CODE, {})
        assert recorder.kwargs["capture_output"] is True
        assert recorder.kwargs["input"]
        assert "stdin" not in recorder.kwargs

    def test_never_passes_preexec_fn(self, recorder: _Recorder) -> None:
        """The parent installs no rlimits itself (S22, amended 2026-09-14).

        On darwin setrlimit(RLIMIT_AS) raises, and a raising preexec_fn aborts
        subprocess.run outright -- every sandbox call would fail. preexec_fn is
        also documented as unsafe in a multithreaded host, which FastAPI is.
        """
        run_sandboxed(VALID_CODE, {})
        assert "preexec_fn" not in recorder.kwargs

    def test_sends_limits_for_worker_self_application(self, recorder: _Recorder) -> None:
        """The worker needs the caps to apply them itself, so they ride on stdin."""
        run_sandboxed(VALID_CODE, {}, SandboxLimits(timeout_s=3.0, max_memory_mb=64, max_output_bytes=4096))
        assert _payload(recorder)["limits"] == {"timeout_s": 3.0, "max_memory_mb": 64, "max_output_bytes": 4096}

    def test_sends_single_json_document(self, recorder: _Recorder) -> None:
        """The stdin document carries exactly {code, state, limits}."""
        run_sandboxed(VALID_CODE, {"values": [1, 2], "label": "x"})
        assert _payload(recorder) == {
            "code": VALID_CODE,
            "state": {"values": [1, 2], "label": "x"},
            "limits": {"timeout_s": 10.0, "max_memory_mb": 256, "max_output_bytes": 1_000_000},
        }


class _LogRecorder:
    """Captures log calls made on the sandbox module logger."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        """Record a warning event and its kwargs."""
        self.events.append((event, kwargs))

    def debug(self, event: str, **kwargs: Any) -> None:
        """Record a debug summary event and its kwargs."""
        self.events.append((event, kwargs))

    def names(self) -> list[str]:
        """Event names of every recorded call, in order."""
        return [event for event, _ in self.events]

    def kwargs_of(self, event: str) -> dict[str, Any]:
        """Keyword arguments of the first recorded call for ``event``."""
        return next(kwargs for name, kwargs in self.events if name == event)


@pytest.fixture
def log_recorder(monkeypatch: pytest.MonkeyPatch) -> _LogRecorder:
    """Swap the module logger so warning calls are inspectable."""
    rec = _LogRecorder()
    monkeypatch.setattr(sandbox, "logger", rec)
    return rec


class TestRlimitReport:
    """The worker reports which limits it could apply; the parent surfaces refusals."""

    def test_warns_on_refused_limits(self, monkeypatch: pytest.MonkeyPatch, log_recorder: _LogRecorder) -> None:
        """A refusal (e.g. RLIMIT_AS on darwin) warns but does not fail the run."""
        monkeypatch.setattr(
            sandbox.subprocess,
            "run",
            _Recorder(stdout=_ok({"n": 1}, refused=["RLIMIT_AS:ValueError"])),
        )
        assert run_sandboxed(VALID_CODE, {}) == {"n": 1}

        assert log_recorder.names().count("sandbox_rlimit_partial") == 1
        kwargs = log_recorder.kwargs_of("sandbox_rlimit_partial")
        assert kwargs["refused"] == ["RLIMIT_AS:ValueError"]
        assert kwargs["platform"] == sys.platform

    def test_warning_never_carries_code_body(
        self, monkeypatch: pytest.MonkeyPatch, log_recorder: _LogRecorder
    ) -> None:
        """Log summary only -- no code, no state (H6/S15)."""
        monkeypatch.setattr(
            sandbox.subprocess,
            "run",
            _Recorder(stdout=_ok({"n": 1}, refused=["RLIMIT_NPROC:ValueError"])),
        )
        run_sandboxed(VALID_CODE, {"values": [1, 2], "label": "x"})
        rendered = repr(log_recorder.events)
        assert VALID_CODE not in rendered
        assert "label" not in rendered

    def test_silent_when_all_limits_applied(self, recorder: _Recorder, log_recorder: _LogRecorder) -> None:
        """No refusals, no warning."""
        assert run_sandboxed(VALID_CODE, {}) == {"echo": 1}
        assert "sandbox_rlimit_partial" not in log_recorder.names()


class TestRunSandboxedAstRecheck:
    """run_sandboxed re-runs the pre-check: a caller cannot skip it (S22)."""

    def test_rejects_before_spawning(self, recorder: _Recorder) -> None:
        """Illegal code raises WorkflowValidationError and never launches a child."""
        with pytest.raises(WorkflowValidationError, match="no-import"):
            run_sandboxed("import os\nreturn {}", {})
        assert recorder.calls == []


class TestRunSandboxedFailureMapping:
    """Every failure mode is wrapped in PythonNodeError (§5)."""

    def test_returns_worker_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A well-formed ok document yields its output dict."""
        monkeypatch.setattr(sandbox.subprocess, "run", _Recorder(stdout=_ok({"total": 3})))
        assert run_sandboxed(VALID_CODE, {}) == {"total": 3}

    def test_worker_error_document(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ok=false is a business failure carrying the worker's message."""
        document = json.dumps({"ok": False, "error": "boom"})
        monkeypatch.setattr(sandbox.subprocess, "run", _Recorder(stdout=document))
        with pytest.raises(PythonNodeError, match="boom"):
            run_sandboxed(VALID_CODE, {})

    def test_nonzero_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A crashed worker is reported with its exit code."""
        monkeypatch.setattr(sandbox.subprocess, "run", _Recorder(stdout="", returncode=3, stderr="Traceback"))
        with pytest.raises(PythonNodeError, match="exit code 3"):
            run_sandboxed(VALID_CODE, {})

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TimeoutExpired becomes PythonNodeError; the stdlib already killed the child."""

        def _raise(*args: Any, **kwargs: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=args[0] if args else "worker", timeout=0.1)

        monkeypatch.setattr(sandbox.subprocess, "run", _raise)
        with pytest.raises(PythonNodeError, match="timed out"):
            run_sandboxed(VALID_CODE, {})

    def test_unparseable_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A worker that pollutes the protocol stream is a failure, not garbage-in-garbage-out."""
        monkeypatch.setattr(sandbox.subprocess, "run", _Recorder(stdout="not json at all"))
        with pytest.raises(PythonNodeError, match="protocol"):
            run_sandboxed(VALID_CODE, {})

    def test_non_dict_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The node output contract is a dict (R3)."""
        document = json.dumps({"ok": True, "output": [1, 2, 3]})
        monkeypatch.setattr(sandbox.subprocess, "run", _Recorder(stdout=document))
        with pytest.raises(PythonNodeError, match="dict"):
            run_sandboxed(VALID_CODE, {})

    def test_output_over_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Captured stdout longer than max_output_bytes is rejected."""
        big = _ok({"blob": "a" * 500})
        monkeypatch.setattr(sandbox.subprocess, "run", _Recorder(stdout=big))
        with pytest.raises(PythonNodeError, match="output"):
            run_sandboxed(VALID_CODE, {}, SandboxLimits(max_output_bytes=64))

    def test_unserializable_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """State that cannot cross the process boundary is a node failure, not a bare TypeError."""
        monkeypatch.setattr(sandbox.subprocess, "run", _Recorder(stdout=_ok({})))
        with pytest.raises(PythonNodeError, match="JSON"):
            run_sandboxed(VALID_CODE, {"obj": object()}, SandboxLimits())


class TestWorkerBuiltins:
    """The worker's SAFE_BUILTINS whitelist is the runtime half of the AST rules."""

    def test_worker_is_import_safe(self) -> None:
        """Importing the worker module has no side effect (main guard present)."""
        assert hasattr(sandbox_worker, "main")

    def test_excludes_capability_builtins(self) -> None:
        """No import, file, exec or introspection entry point survives."""
        for forbidden in (
            "__import__",
            "open",
            "exec",
            "eval",
            "compile",
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
        ):
            assert forbidden not in SAFE_BUILTINS, f"{forbidden} must not be reachable in the sandbox"

    def test_includes_pure_computation_builtins(self) -> None:
        """Data-shaping code still has what it needs."""
        for allowed in (
            "len",
            "range",
            "sorted",
            "min",
            "max",
            "sum",
            "abs",
            "round",
            "enumerate",
            "zip",
            "map",
            "filter",
            "reversed",
            "isinstance",
            "str",
            "int",
            "float",
            "bool",
            "list",
            "dict",
            "set",
            "tuple",
            "Exception",
            "ValueError",
            "TypeError",
            "KeyError",
        ):
            assert allowed in SAFE_BUILTINS, f"{allowed} is required for ordinary data shaping"
