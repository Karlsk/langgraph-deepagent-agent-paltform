"""This file contains the prompts for the agent."""

import os
from datetime import datetime
from typing import Optional

from app.core.config import settings

_PROMPTS_DIR = os.path.dirname(__file__)

# Read templates once at module load — no file I/O per request
with open(os.path.join(_PROMPTS_DIR, "system.md"), "r") as _f:
    _SYSTEM_PROMPT_TEMPLATE = _f.read()

# Session auto-naming prompt (G4 §8.2, restored) — static, formatted per call
with open(os.path.join(_PROMPTS_DIR, "session_title.md"), "r") as _f:
    SESSION_TITLE_PROMPT = _f.read()

# Lines carrying per-request dynamic segments (user context, long-term memory,
# current date/time). They are stripped from the static template persisted in
# the AgentApp row and re-injected per model call by assembly.MemoryMiddleware.
_DYNAMIC_LINES = frozenset(
    {
        "{user_context}",
        "# What you know about the user",
        "{long_term_memory}",
        "# Current date and time",
        "{current_date_and_time}",
    }
)

_STATIC_SYSTEM_PROMPT = (
    "\n".join(line for line in _SYSTEM_PROMPT_TEMPLATE.splitlines() if line.strip() not in _DYNAMIC_LINES)
    .format(agent_name=settings.PROJECT_NAME + " Agent")
    .rstrip()
    + "\n"
)


def load_static_system_prompt() -> str:
    """Return the static base system prompt without per-request dynamic segments.

    The AgentApp default row persists this template; username context,
    long-term memory and the current date/time are appended on every model
    call by ``app.services.agents.assembly.MemoryMiddleware`` so the stored
    prompt never freezes request-time values.
    """
    return _STATIC_SYSTEM_PROMPT


def load_system_prompt(username: Optional[str] = None, **kwargs):
    """Load the system prompt from the cached template."""
    user_context = f"# User\nYou are talking to {username}.\n" if username else ""
    return _SYSTEM_PROMPT_TEMPLATE.format(
        agent_name=settings.PROJECT_NAME + " Agent",
        current_date_and_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_context=user_context,
        **kwargs,
    )
