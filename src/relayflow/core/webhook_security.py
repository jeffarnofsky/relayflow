import hashlib
import hmac


def verify_webhook_signature(
    raw_body: bytes,
    signature: str | None,
    secret: str,
) -> bool:
    if not signature or not secret:
        return False

    prefix = "sha256="
    if not signature.startswith(prefix):
        return False

    supplied_digest = signature[len(prefix) :]

    expected_digest = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_digest, supplied_digest)
