"""Unit tests for JWT / auth-rate-limit configuration (Phase 1 G1, TC-07).

Verifies the documented defaults survive without environment overrides and
that the ``auth_refresh`` / ``auth_logout`` rate-limit entries exist. The
``Settings`` class is a plain env-driven singleton, so default checks
re-instantiate it with the relevant variables cleared (monkeypatch-restore
keeps the process environment intact for other tests).
"""

import pytest

from app.core.config import Settings
from app.core.config import settings as global_settings

pytestmark = pytest.mark.unit


def _fresh_settings(monkeypatch: pytest.MonkeyPatch, *clear: str) -> Settings:
    """Build a Settings instance with the given env vars cleared."""
    for key in clear:
        monkeypatch.delenv(key, raising=False)
    return Settings()


def test_jwt_access_token_expire_days_default_is_7(monkeypatch: pytest.MonkeyPatch) -> None:
    """Access token lifetime defaults to 7 days."""
    fresh = _fresh_settings(monkeypatch, "JWT_ACCESS_TOKEN_EXPIRE_DAYS")
    assert fresh.JWT_ACCESS_TOKEN_EXPIRE_DAYS == 7


def test_jwt_refresh_token_expire_days_default_is_30(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh token lifetime defaults to 30 days."""
    fresh = _fresh_settings(monkeypatch, "JWT_REFRESH_TOKEN_EXPIRE_DAYS")
    assert fresh.JWT_REFRESH_TOKEN_EXPIRE_DAYS == 30


def test_access_token_env_override_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWT_ACCESS_TOKEN_EXPIRE_DAYS env override wins."""
    monkeypatch.setenv("JWT_ACCESS_TOKEN_EXPIRE_DAYS", "14")
    fresh = Settings()
    assert fresh.JWT_ACCESS_TOKEN_EXPIRE_DAYS == 14


def test_auth_refresh_rate_limit_configured() -> None:
    """The auth_refresh rate-limit entry exists."""
    assert global_settings.RATE_LIMIT_ENDPOINTS["auth_refresh"]


def test_auth_refresh_rate_limit_default_is_10_per_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    """The auth_refresh default is 10 per minute."""
    """Code default (env overrides apply afterwards via RATE_LIMIT_* variables)."""
    monkeypatch.delenv("RATE_LIMIT_AUTH_REFRESH", raising=False)
    fresh = Settings()
    assert fresh.RATE_LIMIT_ENDPOINTS["auth_refresh"] == ["10 per minute"]


def test_auth_logout_rate_limit_configured() -> None:
    """The auth_logout rate-limit entry exists."""
    assert global_settings.RATE_LIMIT_ENDPOINTS["auth_logout"]


def test_login_rate_limit_still_configured() -> None:
    """The login rate-limit entry still exists."""
    assert global_settings.RATE_LIMIT_ENDPOINTS["login"]


# ---------------------------------------------------------------------------
# G2 workspace roots (DATA_ROOT + legacy SKILLS_ROOT fallback, spec §2.2/§10.2)
# ---------------------------------------------------------------------------


def test_data_root_default_is_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATA_ROOT defaults to ./data with the legacy SKILLS_ROOT alongside."""
    fresh = _fresh_settings(monkeypatch, "DATA_ROOT", "SKILLS_ROOT")
    assert fresh.DATA_ROOT == "./data"
    assert fresh.SKILLS_ROOT == "./data/skills"


def test_skills_root_env_falls_back_to_parent_for_data_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy SKILLS_ROOT-only deployment maps DATA_ROOT to its parent."""
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.setenv("SKILLS_ROOT", "/srv/custom/skills")
    fresh = Settings()
    assert fresh.SKILLS_ROOT == "/srv/custom/skills"
    assert fresh.DATA_ROOT == "/srv/custom"


def test_data_root_env_overrides_skills_root_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit DATA_ROOT env wins over the SKILLS_ROOT parent fallback."""
    monkeypatch.setenv("SKILLS_ROOT", "/srv/custom/skills")
    monkeypatch.setenv("DATA_ROOT", "/srv/workspace")
    fresh = Settings()
    assert fresh.DATA_ROOT == "/srv/workspace"
    assert fresh.SKILLS_ROOT == "/srv/custom/skills"
