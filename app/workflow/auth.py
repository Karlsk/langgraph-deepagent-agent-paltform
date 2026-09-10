"""Auth dependency stub for workflow API endpoints (spec-19 placeholder).

spec-19 will replace the body with real admin verification; the signature
(``require_admin() -> None``) is frozen so the PUT handler's ``Depends``
wiring does not change.
"""

from __future__ import annotations


async def require_admin() -> None:
    """No-op pass-through; spec-19 replaces with real auth."""
    return None
