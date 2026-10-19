import pytest
from cryptography.fernet import Fernet

from relayflow.core import credentials
from relayflow.core.credentials import (
    CredentialConfigurationError,
    CredentialDecryptionError,
    decrypt_auth_config,
    encrypt_auth_config,
)
from relayflow.schemas.integration import IntegrationRead


def test_auth_config_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(credentials.settings, "fernet_key", key)

    original = {
        "type": "bearer",
        "token": "test-secret-token",
    }

    encrypted = encrypt_auth_config(original)

    assert encrypted is not None
    assert "test-secret-token" not in encrypted
    assert decrypt_auth_config(encrypted) == original


def test_encryption_requires_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(credentials.settings, "fernet_key", None)

    with pytest.raises(CredentialConfigurationError):
        encrypt_auth_config({"type": "bearer", "token": "test-secret-token"})


def test_invalid_ciphertext_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(credentials.settings, "fernet_key", key)

    with pytest.raises(CredentialDecryptionError):
        decrypt_auth_config("not-a-valid-fernet-token")


def test_read_schema_exposes_no_credentials() -> None:
    assert "auth_config" not in IntegrationRead.model_fields
    assert "auth_config_encrypted" not in IntegrationRead.model_fields
