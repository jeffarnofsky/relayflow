import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from relayflow.core.config import settings


class CredentialConfigurationError(RuntimeError):
    """Raised when credential encryption is not configured correctly."""


class CredentialDecryptionError(RuntimeError):
    """Raised when encrypted credential data cannot be read."""


def _get_fernet() -> Fernet:
    key = settings.fernet_key

    if not key:
        raise CredentialConfigurationError("FERNET_KEY is not configured.")

    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeError, TypeError) as exc:
        raise CredentialConfigurationError("FERNET_KEY is invalid.") from exc


def encrypt_auth_config(
    auth_config: dict[str, Any] | None,
) -> str | None:
    if auth_config is None:
        return None

    if (
        auth_config.get("type") != "bearer"
        or not isinstance(auth_config.get("token"), str)
        or not auth_config["token"].strip()
    ):
        raise ValueError("Invalid bearer authentication configuration.")

    payload = json.dumps(
        auth_config,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    return _get_fernet().encrypt(payload).decode("ascii")


def decrypt_auth_config(
    encrypted: str | None,
) -> dict[str, Any] | None:
    if encrypted is None:
        return None

    try:
        decrypted = _get_fernet().decrypt(encrypted.encode("ascii"))
        value = json.loads(decrypted.decode("utf-8"))
    except CredentialConfigurationError:
        raise
    except (InvalidToken, UnicodeError, ValueError, TypeError) as exc:
        raise CredentialDecryptionError(
            "Stored authentication configuration cannot be decrypted."
        ) from exc

    if (
        not isinstance(value, dict)
        or value.get("type") != "bearer"
        or not isinstance(value.get("token"), str)
        or not value["token"].strip()
    ):
        raise CredentialDecryptionError("Stored authentication configuration is invalid.")

    return value
