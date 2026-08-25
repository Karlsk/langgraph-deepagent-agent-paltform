"""DEPRECATED — Phase 1 G1 retired ``POST /auth/session`` and the broader ChatSession concept.

The ``POST /auth/session`` endpoint was removed in Phase 1 G1 together with
the session-token auth path. ``app/api/v1/auth.py`` no longer exposes
``create_session``, ``update_session_name``, ``delete_session``, or
``get_user_sessions``.

This test file is preserved as a skip-only placeholder. The original test
implementations are in git history.

See ``docs/authentication.md`` and
``docs/changelog/agentapp-three-layer-refactor/spec-g1-auth.md``.
"""

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skip(reason="POST /auth/session retired in Phase 1 G1; see docs/authentication.md"),
]
