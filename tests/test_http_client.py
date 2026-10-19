import pytest

from relayflow.core import http_client
from relayflow.core.http_client import (
    UnsafeEndpointError,
    execute_http_request,
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


def test_bearer_auth_is_added_to_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    class FakeResponse:
        status_code = 200
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def iter_bytes(self):
            return iter([b"authorized"])

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def stream(self, method, url, *, headers, json):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = dict(headers)
            return FakeResponse()

    monkeypatch.setattr(
        http_client,
        "validate_endpoint_url",
        lambda url: None,
    )
    monkeypatch.setattr(http_client.httpx2, "Client", FakeClient)

    status_code, excerpt = execute_http_request(
        url="https://example.com/",
        method="GET",
        headers={},
        body=None,
        auth_config={
            "type": "bearer",
            "token": "unit-test-secret",
        },
    )

    assert status_code == 200
    assert excerpt == "authorized"
    assert captured["headers"]["Authorization"] == ("Bearer unit-test-secret")
    assert captured["client_options"]["follow_redirects"] is False
    assert captured["client_options"]["trust_env"] is False
