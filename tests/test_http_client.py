import pytest

from relayflow.core.http_client import (
    UnsafeEndpointError,
    validate_endpoint_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost",
        "http://127.0.0.1",
        "http://169.254.169.254",
        "http://[::1]",
        "http://service.internal",
        "https://user:password@example.com",
        "https://example.com:8443",
    ],
)
def test_rejects_unsafe_endpoint_urls(url: str) -> None:
    with pytest.raises(UnsafeEndpointError):
        validate_endpoint_url(url)
