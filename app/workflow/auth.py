"""Workflow admin authorization (spec-19).

H6: Admin allowlist from env/settings only, no hardcoded usernames.
Empty allowlist = no one can write (safe default).
"""

from __future__ import annotations

from fastapi import Depends, HTTPException

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.models.user import User


async def require_workflow_admin(
    user: User = Depends(get_current_user),
) -> User:
    """Require the current user to be in the workflow admin allowlist.

    Raises:
        HTTPException: 403 if user is not in WORKFLOW_ADMIN_USERNAMES.
    """
    if user.username not in settings.WORKFLOW_ADMIN_USERNAMES:
        raise HTTPException(
            status_code=403,
            detail="workflow write requires admin",
        )
    return user
