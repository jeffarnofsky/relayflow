import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from relayflow.core.config import settings

api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
)


DEVELOPMENT_ADMIN_API_KEY = "development-only-change-me"


def require_admin_api_key(
    api_key: str | None = Security(api_key_header),
) -> None:
    expected_key = settings.admin_api_key

    if settings.app_env.strip().casefold() == "production" and hmac.compare_digest(
        expected_key, DEVELOPMENT_ADMIN_API_KEY
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Administrative access is not configured for production.",
        )

    if not api_key or not expected_key or not hmac.compare_digest(api_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )
