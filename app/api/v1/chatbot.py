"""Chatbot API endpoints for handling chat interactions.

RETIRED — Phase 1 G1 single-layer auth retired this module.

Phase 1 G1 collapses the auth model to a single user token + refresh token
pair (see ``docs/authentication.md``). The chat runtime will be redesigned
in Phase 2/3 on top of the new auth surface; until then, no client should
exercise ``/chatbot/*`` endpoints.

History (pre-Phase 1): this module hosted ``POST /chatbot/chat``,
``POST /chatbot/chat/stream``, ``GET /chatbot/messages``, and
``DELETE /chatbot/messages``. Each was auth-scoped to a chat session via
``Depends(get_current_session)`` (now removed from ``app/api/v1/auth.py``).
See git history for the original implementations.

The router is exported as an empty ``APIRouter`` so that
``from app.api.v1.chatbot import router`` keeps resolving at import time for
any lingering reference (e.g. workflow edges or test fixtures) until they
are cleaned up in Phase 2. ``app/api/v1/api.py`` no longer registers this
router, so ``/chatbot/*`` returns 404 at runtime.
"""

from fastapi import APIRouter

router = APIRouter()
