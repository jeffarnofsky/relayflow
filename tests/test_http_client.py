import ipaddress
import socket

import httpcore2
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


def _endpoint(
    hostname: str = "example.com",
    port: int = 443,
    addresses: tuple[str, ...] = ("93.184.216.34",),
) -> http_client._ValidatedEndpoint:
    return http_client._ValidatedEndpoint(
        hostname=hostname,
        port=port,
        addresses=tuple(ipaddress.ip_address(address) for address in addresses),
    )


def _addrinfo(address: str, port: int) -> tuple:
    if ":" in address:
        family = socket.AF_INET6
        sockaddr = (address, port, 0, 0)
    else:
        family = socket.AF_INET
        sockaddr = (address, port)

    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)


def test_validation_returns_resolved_public_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []

    def fake_getaddrinfo(host: str, port: int, *, type: int) -> list[tuple]:
        calls.append((host, port))
        return [_addrinfo("93.184.216.34", port)]

    monkeypatch.setattr(http_client.socket, "getaddrinfo", fake_getaddrinfo)

    endpoint = validate_endpoint_url("https://example.com/")

    assert endpoint.hostname == "example.com"
    assert endpoint.port == 443
    assert endpoint.addresses == (ipaddress.ip_address("93.184.216.34"),)
    assert calls == [("example.com", 443)]


def test_rejects_dns_response_containing_private_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(host: str, port: int, *, type: int) -> list[tuple]:
        return [
            _addrinfo("93.184.216.34", port),
            _addrinfo("127.0.0.1", port),
        ]

    monkeypatch.setattr(http_client.socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(UnsafeEndpointError, match="public IP"):
        validate_endpoint_url("https://example.com/")


def test_pinned_backend_connects_to_validated_ip_without_resolving_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dns_calls: list[tuple[str, int]] = []

    def fake_getaddrinfo(host: str, port: int, *, type: int) -> list[tuple]:
        dns_calls.append((host, port))
        return [_addrinfo("93.184.216.34", port)]

    monkeypatch.setattr(http_client.socket, "getaddrinfo", fake_getaddrinfo)

    endpoint = validate_endpoint_url("https://example.com/")
    fake_stream = object()
    connections: list[tuple[str, int]] = []

    class FakeBackend:
        def connect_tcp(
            self,
            host: str,
            port: int,
            timeout=None,
            local_address=None,
            socket_options=None,
        ):
            connections.append((host, port))
            return fake_stream

    monkeypatch.setattr(http_client.httpcore2, "SyncBackend", FakeBackend)

    backend = http_client._PinnedNetworkBackend(endpoint)
    stream = backend.connect_tcp("example.com", 443, timeout=2.0)

    assert stream is fake_stream
    assert dns_calls == [("example.com", 443)]
    assert connections == [("93.184.216.34", 443)]


def test_pinned_backend_rejects_unexpected_destination() -> None:
    backend = http_client._PinnedNetworkBackend(_endpoint())

    with pytest.raises(httpcore2.ConnectError, match="unvalidated destination"):
        backend.connect_tcp("internal.example", 443)


def test_transport_installs_pinned_backend() -> None:
    transport = http_client._create_pinned_transport(_endpoint())

    try:
        assert isinstance(transport._pool, httpcore2.ConnectionPool)
        assert isinstance(
            transport._pool._network_backend,
            http_client._PinnedNetworkBackend,
        )
    finally:
        transport.close()


def test_bearer_auth_is_added_to_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    fake_transport = object()

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
        lambda url: _endpoint(),
    )
    monkeypatch.setattr(
        http_client,
        "_create_pinned_transport",
        lambda endpoint: fake_transport,
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
    assert captured["headers"]["Authorization"] == "Bearer unit-test-secret"
    assert captured["client_options"]["follow_redirects"] is False
    assert captured["client_options"]["trust_env"] is False
    assert captured["client_options"]["transport"] is fake_transport
