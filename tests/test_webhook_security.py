import hashlib
import hmac

from relayflow.core.webhook_security import verify_webhook_signature


def test_valid_signature_is_accepted() -> None:
    body = b'{"type":"invoice.created","id":"evt-001"}'
    secret = "test-webhook-secret"

    digest = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    assert verify_webhook_signature(
        body,
        f"sha256={digest}",
        secret,
    )


def test_invalid_signature_is_rejected() -> None:
    body = b'{"type":"invoice.created","id":"evt-001"}'

    assert not verify_webhook_signature(
        body,
        "sha256=invalid",
        "test-webhook-secret",
    )


def test_missing_signature_is_rejected() -> None:
    assert not verify_webhook_signature(
        b'{"id":"evt-001"}',
        None,
        "test-webhook-secret",
    )


def test_modified_body_does_not_match_signature() -> None:
    original_body = b'{"id":"evt-001"}'
    modified_body = b'{"id":"evt-002"}'
    secret = "test-webhook-secret"

    digest = hmac.new(
        secret.encode(),
        original_body,
        hashlib.sha256,
    ).hexdigest()

    assert not verify_webhook_signature(
        modified_body,
        f"sha256={digest}",
        secret,
    )
