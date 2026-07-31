"""Characterization tests for the langchain-core 1.0.4 Runnable contract.

These lock in the observed behaviour behind EXP-C1..C3 and EXP-X1 of
``api-exploration-1x.md`` (spec-00 TC5). They are offline only (no network,
no real LLM calls) and serve as regression guards for the engine's reliance on:

* :class:`~langchain_core.runnables.RunnableLambda` function-signature rules
  (config injection keyed on a parameter literally named ``config``);
* ``with_config(tags=[...])`` tag propagation into the runtime config;
* the :class:`~langchain_core.runnables.RunnableConfig` field set;
* the minimal import surface of the engine (langgraph + langchain-core +
  provider packages), independent of the top-level ``langchain`` package.

Evidence sources (``.venv`` at langchain-core 1.0.4):

* ``langchain_core/runnables/utils.py:92`` ``accepts_config`` — name-based check.
* ``langchain_core/runnables/config.py:399`` ``call_func_with_variable_args``.
* ``langchain_core/runnables/config.py:49`` ``RunnableConfig`` TypedDict.
"""

import subprocess
import sys
import textwrap

import pytest
from langchain_core.runnables import (
    RunnableConfig,
    RunnableLambda,
)
from langchain_core.runnables.config import CONFIG_KEYS


@pytest.mark.integration
def test_c1_single_param_func_invocable() -> None:
    """EXP-C1: a single-argument ``func(state)`` runs unchanged."""
    result = RunnableLambda(lambda state: {"out": state["in"] + 1}).invoke({"in": 1})
    assert result == {"out": 2}


@pytest.mark.integration
def test_c1_param_named_config_receives_runtime_config() -> None:
    """EXP-C1: a second parameter named ``config`` is injected a dict config."""
    captured: dict[str, object] = {}

    def node(state: dict, config: RunnableConfig) -> dict:
        """Record the injected config shape and echo a marker."""
        captured["type"] = type(config)
        captured["keys"] = set(config.keys())
        return {"ok": True}

    result = RunnableLambda(node).invoke({"in": 1})

    assert result == {"ok": True}
    # Injection yields a plain dict pre-populated by ``ensure_config`` defaults.
    assert captured["type"] is dict
    assert {"tags", "metadata", "callbacks", "configurable"}.issubset(captured["keys"])


@pytest.mark.integration
def test_c1_second_param_not_named_config_is_not_injected() -> None:
    """EXP-C1: injection is name-based; a differently named 2nd param is skipped."""
    captured: dict[str, object] = {}

    def node(state: dict, cfg: object = None) -> dict:
        """Second parameter is named ``cfg`` (not ``config``) with a default."""
        captured["cfg"] = cfg
        return {"ok": True}

    result = RunnableLambda(node).invoke({"in": 1})

    assert result == {"ok": True}
    # No config injected: ``cfg`` keeps its default because the name is not "config".
    assert captured["cfg"] is None


@pytest.mark.integration
def test_c2_with_config_tags_visible_in_func() -> None:
    """EXP-C2: ``with_config(tags=[...])`` propagates into the func's config."""
    captured: dict[str, object] = {}

    def node(state: dict, config: RunnableConfig) -> dict:
        """Capture the tags observed at runtime."""
        captured["tags"] = config.get("tags")
        return {"done": True}

    RunnableLambda(node).with_config(tags=["my_node"]).invoke({"x": 1})

    assert captured["tags"] == ["my_node"]


@pytest.mark.integration
def test_c2_bound_tags_merge_with_invoke_tags() -> None:
    """EXP-C2: bound tags and invoke-time tags merge into a sorted union."""
    captured: dict[str, object] = {}

    def node(state: dict, config: RunnableConfig) -> dict:
        """Capture the merged tags observed at runtime."""
        captured["tags"] = config.get("tags")
        return {"done": True}

    RunnableLambda(node).with_config(tags=["a"]).invoke({"x": 1}, config={"tags": ["b"]})

    assert captured["tags"] == ["a", "b"]


@pytest.mark.integration
def test_c3_runnable_config_field_set() -> None:
    """EXP-C3: ``RunnableConfig`` exposes the documented field set incl. callbacks."""
    annotations = set(RunnableConfig.__annotations__)
    expected = {
        "tags",
        "metadata",
        "callbacks",
        "run_name",
        "max_concurrency",
        "recursion_limit",
        "configurable",
        "run_id",
    }
    assert annotations == expected
    # ``CONFIG_KEYS`` mirrors the same recognised keys used during merging.
    assert set(CONFIG_KEYS) == expected


@pytest.mark.integration
def test_x1_engine_imports_do_not_require_top_level_langchain() -> None:
    """EXP-X1: the planned engine import surface avoids the top-level ``langchain``.

    Runs in a clean-room subprocess so the check reflects only what the engine's
    dependency surface (langgraph + langchain-core + provider packages) pulls in
    transitively, rather than modules cached by the pytest session. Offline only.
    """
    program = textwrap.dedent(
        """
        import sys

        from langchain_anthropic import ChatAnthropic  # noqa: F401
        from langchain_core.runnables import RunnableConfig, RunnableLambda  # noqa: F401
        from langchain_openai import ChatOpenAI  # noqa: F401
        from langgraph.graph import END, START, StateGraph  # noqa: F401
        from langgraph.graph.state import CompiledStateGraph  # noqa: F401

        assert "langchain" not in sys.modules, sorted(
            m for m in sys.modules if m == "langchain"
        )
        print("OK")
        """
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "OK"
