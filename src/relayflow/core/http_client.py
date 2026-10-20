import ipaddress
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpcore2
import httpx2

MAX_RESPONSE_BYTES = 4096
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_ENDPOINT_ADDRESSES = 16

SENSITIVE_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
}

FORBIDDEN_HEADERS = {
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
    "proxy-connection",
    "keep-alive",
    "te",
    "trailer",
    "upgrade",
}


class UnsafeEndpointError(ValueError):
    """Raised when an integration endpoint is not permitted."""


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class _ValidatedEndpoint:
    hostname: str
    port: int
    addresses: tuple[IPAddress, ...]


def _normalize_hostname(hostname: str) -> str:
    host = hostname.rstrip(".")
    if not host:
        raise UnsafeEndpointError("The endpoint must have a hostname.")

    try:
        return ipaddress.ip_address(host).compressed.lower()
    except ValueError:
        try:
            return host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise UnsafeEndpointError("The endpoint hostname is invalid.") from exc


def validate_endpoint_url(url: str) -> _ValidatedEndpoint:
    """Validate a URL and return the public IP addresses resolved for it."""
    if not url or "\\" in url or any(ord(char) < 32 for char in url):
        raise UnsafeEndpointError("The endpoint URL is invalid.")

    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeEndpointError("The endpoint URL is invalid.") from exc

    if scheme not in {"http", "https"}:
        raise UnsafeEndpointError("Only HTTP and HTTPS endpoints are allowed.")

    if not hostname:
        raise UnsafeEndpointError("The endpoint must have a hostname.")

    if parsed.username is not None or parsed.password is not None:
        raise UnsafeEndpointError("Credentials must not be embedded in the endpoint URL.")

    expected_port = 443 if scheme == "https" else 80
    if port is not None and port != expected_port:
        raise UnsafeEndpointError("Only the standard HTTP and HTTPS ports are allowed.")

    host = _normalize_hostname(hostname)

    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeEndpointError("Local and internal hostnames are not allowed.")

    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        try:
            results = socket.getaddrinfo(
                host,
                expected_port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise UnsafeEndpointError("The endpoint hostname could not be resolved.") from exc

        addresses_list: list[IPAddress] = []
        try:
            for result in results:
                address = ipaddress.ip_address(result[4][0])
                if address not in addresses_list:
                    addresses_list.append(address)
        except (IndexError, TypeError, ValueError) as exc:
            raise UnsafeEndpointError("The endpoint hostname returned an invalid address.") from exc
    else:
        addresses_list = [literal_address]

    if not addresses_list:
        raise UnsafeEndpointError("The endpoint hostname did not resolve to an IP address.")

    if len(addresses_list) > MAX_ENDPOINT_ADDRESSES:
        raise UnsafeEndpointError("The endpoint returned too many IP addresses.")

    # Reject the entire DNS response if even one result is not globally routable.
    if any(not address.is_global for address in addresses_list):
        raise UnsafeEndpointError("The endpoint must resolve only to public IP addresses.")

    return _ValidatedEndpoint(
        hostname=host,
        port=port or expected_port,
        addresses=tuple(addresses_list),
    )


class _PinnedNetworkBackend(httpcore2.NetworkBackend):
    """Connect only to the IP addresses verified during endpoint validation."""

    def __init__(self, endpoint: _ValidatedEndpoint) -> None:
        self._hostname = endpoint.hostname
        self._port = endpoint.port
        self._addresses = endpoint.addresses
        self._backend = httpcore2.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        try:
            requested_host = _normalize_hostname(host)
        except UnsafeEndpointError as exc:
            raise httpcore2.ConnectError("Unexpected destination hostname.") from exc

        if requested_host != self._hostname or port != self._port:
            raise httpcore2.ConnectError("The HTTP transport requested an unvalidated destination.")

        deadline = None if timeout is None else time.monotonic() + timeout
        last_error: Exception | None = None

        for address in self._addresses:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                break

            try:
                # Supplying an IP literal prevents another DNS lookup of the target hostname.
                return self._backend.connect_tcp(
                    host=address.compressed,
                    port=port,
                    timeout=remaining,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as exc:
                last_error = exc

        if last_error is not None:
            raise last_error

        raise httpcore2.ConnectTimeout("The TCP connection timed out.")


def _create_pinned_transport(endpoint: _ValidatedEndpoint) -> httpx2.HTTPTransport:
    # HTTPTransport does not expose network_backend in its public constructor.
    # Verify the internal API before using it, and pin dependency versions in pyproject.toml.
    transport = httpx2.HTTPTransport(
        trust_env=False,
        proxy=None,
        uds=None,
        retries=0,
    )

    pool = getattr(transport, "_pool", None)
    if not isinstance(pool, httpcore2.ConnectionPool) or not hasattr(pool, "_network_backend"):
        transport.close()
        raise RuntimeError("The installed HTTP transport does not support pinned networking.")

    pool._network_backend = _PinnedNetworkBackend(endpoint)
    return transport


def _prepare_headers(headers: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}

    for name, value in headers.items():
        normalized_name = name.lower()

        if normalized_name in SENSITIVE_HEADERS:
            raise UnsafeEndpointError("Sensitive credentials are not supported in request_headers.")

        if normalized_name in FORBIDDEN_HEADERS:
            raise UnsafeEndpointError("This HTTP header is managed by the HTTP client.")

        result[name] = value

    return result


def execute_http_request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    body: dict[str, Any] | list[Any] | None,
    auth_config: dict[str, Any] | None = None,
) -> tuple[int, str]:
    endpoint = validate_endpoint_url(url)
    safe_headers = _prepare_headers(headers)

    if auth_config is not None:
        if auth_config.get("type") != "bearer":
            raise UnsafeEndpointError("Unsupported authentication configuration.")

        token = auth_config.get("token")
        if not isinstance(token, str) or not token.strip():
            raise UnsafeEndpointError("Bearer token is invalid.")

        safe_headers["Authorization"] = f"Bearer {token}"

    transport = _create_pinned_transport(endpoint)

    with httpx2.Client(
        transport=transport,
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with client.stream(
            method,
            url,
            headers=safe_headers,
            json=body,
        ) as response:
            chunks: list[bytes] = []
            total_bytes = 0

            for chunk in response.iter_bytes():
                remaining = MAX_RESPONSE_BYTES - total_bytes
                if remaining <= 0:
                    break

                limited_chunk = chunk[:remaining]
                chunks.append(limited_chunk)
                total_bytes += len(limited_chunk)

                if total_bytes >= MAX_RESPONSE_BYTES:
                    break

            content = b"".join(chunks)
            excerpt = content.decode(
                response.encoding or "utf-8",
                errors="replace",
            )

            return response.status_code, excerpt
