from uuid import uuid4

from relayflow import tasks


def test_worker_passes_decrypted_credentials_to_http_client(
    monkeypatch,
) -> None:
    run_id = uuid4()
    secret_config = {
        "type": "bearer",
        "token": "unit-test-secret",
    }

    request_data = {
        "url": "https://example.com/",
        "method": "GET",
        "headers": {},
        "body": None,
        "auth_config_encrypted": "encrypted-test-value",
    }

    captured = {}

    monkeypatch.setattr(
        tasks,
        "_claim_run",
        lambda requested_id: (
            True,
            "running",
            request_data,
            None,
        ),
    )

    monkeypatch.setattr(
        tasks,
        "decrypt_auth_config",
        lambda encrypted: secret_config,
    )

    def fake_execute_http_request(**kwargs):
        captured.update(kwargs)
        return 200, "test response"

    monkeypatch.setattr(
        tasks,
        "execute_http_request",
        fake_execute_http_request,
    )

    monkeypatch.setattr(
        tasks,
        "_update_run_if_running",
        lambda requested_id, **values: values.get("status", "running"),
    )

    result = tasks.execute_integration_run.run(str(run_id))

    assert result["status"] == "succeeded"
    assert captured["auth_config"] == secret_config
    assert captured["url"] == "https://example.com/"
