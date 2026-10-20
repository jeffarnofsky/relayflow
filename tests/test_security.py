import pytest
from fastapi import HTTPException

from relayflow.core import security


def test_production_rejects_development_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(security.settings, "app_env", "production")
    monkeypatch.setattr(
        security.settings,
        "admin_api_key",
        security.DEVELOPMENT_ADMIN_API_KEY,
    )

    with pytest.raises(HTTPException) as error:
        security.require_admin_api_key(security.DEVELOPMENT_ADMIN_API_KEY)

    assert error.value.status_code == 503


def test_production_accepts_configured_admin_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_key = "unit-test-admin-key-" + ("x" * 40)
    monkeypatch.setattr(security.settings, "app_env", "production")
    monkeypatch.setattr(security.settings, "admin_api_key", configured_key)

    assert security.require_admin_api_key(configured_key) is None
