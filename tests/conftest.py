"""Shared pytest fixtures for the test suite."""

from collections.abc import Generator

import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture(autouse=True)
def restore_node_registry() -> Generator[None, None, None]:
    """Placeholder for node registry isolation (D7).

    Completed in spec-03: will snapshot and restore the node type
    registry around each test to keep registrations isolated.
    """
    yield
