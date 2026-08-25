"""DEPRECATED — Phase 1 G1 retired the chatbot session-token runtime.

The chatbot endpoints (``POST /chatbot/chat``, ``POST /chatbot/chat/stream``,
``GET /chatbot/messages``, ``DELETE /chatbot/messages``) were removed in
Phase 1 G1. ``app/api/v1/chatbot.py`` is shipped as a stub (no routes), and
``app/api/v1/auth.py`` no longer exposes ``get_current_session``.

This test file is preserved as a skip-only placeholder. The original test
implementations are in git history; Phase 2/3 will redesign the chat runtime
on top of the new single-layer user token and write fresh tests then.

See ``docs/authentication.md`` and
``docs/changelog/agentapp-three-layer-refactor/spec-g1-auth.md``.
"""

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skip(reason="chatbot session runtime retired in Phase 1 G1; see docs/authentication.md"),
]
