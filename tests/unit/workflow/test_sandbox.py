"""Unit tests for app.workflow.sandbox (CONTRACT §4.14, S22).

Scope of this module: pure functions and the *invocation contract* of
``run_sandboxed`` (asserted with a stubbed ``subprocess.run``), so no child
process is ever spawned here. Behaviour that needs a real process — the wire
protocol, timeout kill, rlimits — lives in
``tests/integration/workflow/test_sandbox_execution.py``.
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.workflow import sandbox, sandbox_worker
from app.workflow.models import PythonNodeError, WorkflowValidationError
from app.workflow.sandbox import SandboxLimits, run_sandboxed, validate_code_ast
from app.workflow.sandbox_worker import SAFE_BUILTINS

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
SDN_TEMPLATE = REPO_ROOT / "app" / "sdn" / "config" / "sdn_alert_inspection.template.yaml"

VALID_CODE = 'total = sum(state["values"])\nreturn {"total": total, "label": state["label"].upper()}'


# ─── §4.14 rule 1: imports ────────────────────────────────────────────


class TestImportAllowlist:
    """Rule 1 (revised 2026-09-14): the *root* module must be allowlisted."""

    @pytest.mark.parametrize(
        "code",
        [
            "import os",
            "import os, sys",
            "from os import path",
            "import socket",
            "import uuid",
            "import typing",
            "import os.path",
            "from os.path import join",
            "x = 1\nimport subprocess\nreturn {}",
        ],
    )
    def test_rejects_module_outside_allowlist(self, code: str) -> None:
        """A root module outside the allowlist raises the no-import rule."""
        with pytest.raises(WorkflowValidationError, match="no-import"):
            validate_code_ast(code)

    @pytest.mark.parametrize("code", ["from . import sibling", "from ..pkg import thing", "from .mod import x"])
    def test_rejects_relative_import(self, code: str) -> None:
        """Relative imports are rejected outright: there is no package context in the sandbox."""
        with pytest.raises(WorkflowValidationError, match="no-import"):
            validate_code_ast(code)

    @pytest.mark.parametrize(
        "code",
        [
            "import json",
            "import re",
            "import random",
            "from datetime import datetime",
            "import collections.abc",
            "import re as _re",
            "from functools import reduce",
            "x = 1\nimport json\nreturn {}",
            "import json\nimport re\nfrom datetime import datetime\nreturn {}",
        ],
    )
    def test_accepts_allowlisted_module(self, code: str) -> None:
        """Allowlisted stdlib passes, including submodule and alias forms."""
        assert validate_code_ast(code) is None

    def test_rejection_names_the_root_module(self) -> None:
        """The message carries the offending root module, not the code body (H6)."""
        with pytest.raises(WorkflowValidationError) as exc_info:
            validate_code_ast("import os.path\nreturn {}")
        message = str(exc_info.value)
        assert "no-import" in message
        assert "os" in message
        assert "import os.path" not in message

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
        """No file, exec or introspection entry point survives."""
        for forbidden in (
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

    def test_import_is_restricted_not_builtin(self) -> None:
        """``__import__`` is present but is the worker's own gate, not the real importer."""
        assert "__import__" in SAFE_BUILTINS
        assert SAFE_BUILTINS["__import__"] is not builtins.__import__

    def test_restricted_import_allows_allowlisted_module(self) -> None:
        """The runtime half of rule 1 lets allowlisted stdlib through."""
        assert SAFE_BUILTINS["__import__"]("json") is json

    @pytest.mark.parametrize("name", ["os", "sys", "socket", "subprocess", "uuid", "typing"])
    def test_restricted_import_rejects_capability_module(self, name: str) -> None:
        """Code that bypasses the AST pre-check still cannot reach a capability module."""
        with pytest.raises(ImportError, match=name):
            SAFE_BUILTINS["__import__"](name)

    def test_restricted_import_rejects_relative(self) -> None:
        """A nonzero level is refused regardless of module name."""
        with pytest.raises(ImportError):
            SAFE_BUILTINS["__import__"]("json", level=1)

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


# ─── S22: the allowlist is stored twice ───────────────────────────────


def _allowed_modules_from_source(path: Path) -> set[str]:
    """Read ``_ALLOWED_MODULES`` out of a file's *text*, never out of its imports.

    The worker must not enter the parent's import graph (it is spawned as a
    child), so the two copies can only be compared by parsing source.
    ``ast.literal_eval`` cannot digest ``frozenset({...})``, so the call node is
    asserted and only its argument evaluated.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(target, "id", None) == "_ALLOWED_MODULES" for target in node.targets):
            continue
        call = node.value
        assert isinstance(call, ast.Call), f"{path.name}: _ALLOWED_MODULES must be built by frozenset(...)"
        assert getattr(call.func, "id", None) == "frozenset", f"{path.name}: _ALLOWED_MODULES must be a frozenset"
        return set(ast.literal_eval(call.args[0]))
    raise AssertionError(f"{path.name} does not define _ALLOWED_MODULES")


class TestAllowedModuleCopies:
    """Two files, one allowlist — a drift between them is a security hole."""

    _SANDBOX = REPO_ROOT / "app" / "workflow" / "sandbox.py"
    _WORKER = REPO_ROOT / "app" / "workflow" / "sandbox_worker.py"

    def test_copies_are_equal(self) -> None:
        """The registration-time pre-check and the runtime gate must agree."""
        assert _allowed_modules_from_source(self._SANDBOX) == _allowed_modules_from_source(self._WORKER)

    def test_copies_are_non_empty(self) -> None:
        """Guards against a parse that silently matched nothing."""
        assert _allowed_modules_from_source(self._SANDBOX)

    def test_allowlist_covers_what_the_sdn_workflow_needs(self) -> None:
        """The motivation for the revision, pinned as data."""
        assert {"json", "re", "random", "datetime"} <= _allowed_modules_from_source(self._SANDBOX)

    def test_parent_never_imports_the_worker(self) -> None:
        """Why the constant is duplicated rather than shared."""
        tree = ast.parse(self._SANDBOX.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        assert not any("sandbox_worker" in name for name in imported), imported


# ─── motivation anchor: the SDN workflow's verbatim code ──────────────


def _sdn_python_nodes() -> list[tuple[str, str]]:
    """Every ``type: python`` node's code body from the git-tracked template."""
    document = yaml.safe_load(SDN_TEMPLATE.read_text(encoding="utf-8"))
    nodes = [(node["name"], node["config"]["code"]) for node in document["nodes"] if node["type"] == "python"]
    assert nodes, "the SDN template must contain python nodes for this card to mean anything"
    return nodes


_SDN_NODES = _sdn_python_nodes()


class TestSdnWorkflowCodePassesThePrecheck:
    """The YAML that motivated the revision must survive registration as-is."""

    @pytest.mark.parametrize(("name", "code"), _SDN_NODES, ids=[node_name for node_name, _ in _SDN_NODES])
    def test_node_code_is_accepted(self, name: str, code: str) -> None:
        """The workflow is not hand-edited to fit the sandbox; the sandbox fits the workflow."""
        assert validate_code_ast(code) is None, f"node {name} was rejected"

    def test_nodes_import_nothing_outside_the_allowlist(self) -> None:
        """Documents the allowlist's lower bound in terms of real usage."""
        roots: set[str] = set()
        for _, code in _SDN_NODES:
            for node in ast.walk(ast.parse(code)):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    roots.add((node.module or "").split(".")[0])
        assert roots == {"json", "re", "random", "datetime"}


# ─── §4.14 rule 1 (revised): a legal import must not reopen old holes ─


class TestEscapeChainsAfterAllowlistedImport:
    """Widening rule 1 hands user code a real module object; rules 2/3 must hold."""

    @pytest.mark.parametrize(
        "tail",
        [
            "json.__loader__",
            "json.__builtins__",
            "json.__spec__",
            "().__class__",
            "''.__class__.__bases__",
        ],
    )
    def test_rejects_dunder_reach(self, tail: str) -> None:
        """Rule 3 fires at registration, before any child is spawned."""
        with pytest.raises(WorkflowValidationError, match="dunder-identifier"):
            validate_code_ast(f"import json\nreturn {{'x': {tail}}}")

    @pytest.mark.parametrize("name", ["getattr", "vars", "dir", "globals", "eval", "exec", "open", "__import__"])
    def test_rejects_introspection_builtin(self, name: str) -> None:
        """Rule 2 is untouched by the allowlist: the escape primitives stay banned."""
        with pytest.raises(WorkflowValidationError, match="forbidden-call"):
            validate_code_ast(f"import json\nreturn {{'x': {name}(json)}}")

    def test_accepts_ordinary_module_use(self) -> None:
        """Positive control: the widening is not so tight that legal calls break."""
        code = 'import json\nimport re\nreturn {"s": json.dumps({"a": 1}), "m": bool(re.match("a", "ab"))}'
        assert validate_code_ast(code) is None
