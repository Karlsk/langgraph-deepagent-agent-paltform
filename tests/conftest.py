"""Shared pytest fixtures for the test suite."""

from collections.abc import Generator

import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture(autouse=True)
def restore_node_registry() -> Generator[None, None, None]:
    """Snapshot and restore the node type registry around each test (D7/AD-08)."""
    from app.workflow.nodes import factory  # noqa: PLC0415 — import inside fixture per AD-08

    snapshot = dict(factory._NODE_REGISTRY)  # noqa: SLF001 — private access per AD-08 spec
    yield
    factory._NODE_REGISTRY.clear()  # noqa: SLF001
    factory._NODE_REGISTRY.update(snapshot)  # noqa: SLF001
