import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx2

MAX_RESPONSE_BYTES = 4096
REQUEST_TIMEOUT_SECONDS = 10.0

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


def validate_endpoint_url(url: str) -> None:
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

    host = hostname.rstrip(".").lower()

    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeEndpointError("Local and internal hostnames are not allowed.")

    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            results = socket.getaddrinfo(
                host,
                port or expected_port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise UnsafeEndpointError("The endpoint hostname could not be resolved.") from exc

        addresses = {ipaddress.ip_address(result[4][0]) for result in results}

    if not addresses or any(not address.is_global for address in addresses):
        raise UnsafeEndpointError("The endpoint must resolve only to public IP addresses.")


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
    validate_endpoint_url(url)
    safe_headers = _prepare_headers(headers)
    if auth_config is not None:
        if auth_config.get("type") != "bearer":
            raise UnsafeEndpointError("Unsupported authentication configuration.")

        token = auth_config.get("token")

        if not isinstance(token, str) or not token.strip():
            raise UnsafeEndpointError("Bearer token is invalid.")

        safe_headers["Authorization"] = f"Bearer {token}"

    with httpx2.Client(
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
