from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest

from relayflow import tasks
from relayflow.core.http_client import UnsafeEndpointError
from relayflow.models.integration import Integration
from relayflow.models.run import Run


def setup_fake_task_db(monkeypatch: pytest.MonkeyPatch):
    run_id = uuid4()
    integration_id = uuid4()

    run = SimpleNamespace(
        id=run_id,
        integration_id=integration_id,
        status="queued",
        attempts=0,
        started_at=None,
        completed_at=None,
        http_status_code=None,
        response_excerpt=None,
        error_message=None,
        duration_ms=None,
    )

    def fake_claim(requested_run_id):
        assert requested_run_id == run_id

        if run.status != "queued":
            return False, run.status, None, None

        run.status = "running"
        run.attempts += 1
        run.started_at = datetime.now(UTC)

        return (
            True,
            "running",
            {
                "url": "https://example.com/",
                "method": "GET",
                "headers": {},
                "body": None,
            },
            None,
        )

    def fake_update(requested_run_id, **values):
        assert requested_run_id == run_id

        if run.status != "running":
            return run.status

        for field_name, value in values.items():
            setattr(run, field_name, value)

        return run.status

    monkeypatch.setattr(tasks, "_claim_run", fake_claim)
    monkeypatch.setattr(tasks, "_update_run_if_running", fake_update)

    integration = SimpleNamespace(
        id=integration_id,
        endpoint_url="https://example.com/",
        http_method="GET",
        request_headers={},
        request_body=None,
        is_active=True,
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def get(self, model, object_id):
            if model is Run and object_id == run_id:
                return run

            if model is Integration and object_id == integration_id:
                return integration

            return None

        def commit(self) -> None:
            pass

    monkeypatch.setattr(
        tasks,
        "SessionLocal",
        lambda: FakeSession(),
    )

    return run_id, run


@pytest.mark.parametrize(
    ("http_status_code", "expected_status"),
    [
        (200, "succeeded"),
        (503, "failed"),
    ],
)
def test_execute_integration_run_saves_result(
    monkeypatch: pytest.MonkeyPatch,
    http_status_code: int,
    expected_status: str,
) -> None:
    run_id, run = setup_fake_task_db(monkeypatch)

    monkeypatch.setattr(
        tasks,
        "execute_http_request",
        lambda **kwargs: (http_status_code, "test response"),
    )

    result = tasks.execute_integration_run.run(str(run_id))

    assert result["status"] == expected_status
    assert run.status == expected_status
    assert run.attempts == 1
    assert run.http_status_code == http_status_code
    assert run.response_excerpt == "test response"
    assert run.completed_at is not None

    if http_status_code == 200:
        assert run.error_message is None
    else:
        assert run.error_message == (f"Remote endpoint returned HTTP {http_status_code}.")


def test_network_timeout_requests_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, run = setup_fake_task_db(monkeypatch)
    timeout_error = httpx2.TimeoutException("request timed out")
    retry_calls = []

    def raise_timeout(**kwargs):
        raise timeout_error

    class RetryRequested(Exception):
        pass

    def fake_retry(*, exc, countdown):
        retry_calls.append((exc, countdown))
        raise RetryRequested()

    monkeypatch.setattr(
        tasks,
        "execute_http_request",
        raise_timeout,
    )
    monkeypatch.setattr(
        tasks.execute_integration_run,
        "retry",
        fake_retry,
    )

    with pytest.raises(RetryRequested):
        tasks.execute_integration_run.run(str(run_id))

    assert len(retry_calls) == 1
    assert retry_calls[0][0] is timeout_error
    assert retry_calls[0][1] == 1
    assert run.status == "queued"
    assert run.attempts == 1
    assert run.error_message == "TimeoutException"
    assert run.completed_at is None


def test_unsafe_endpoint_fails_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, run = setup_fake_task_db(monkeypatch)
    error_message = "The endpoint must resolve only to public IP addresses."

    def reject_endpoint(**kwargs):
        raise UnsafeEndpointError(error_message)

    monkeypatch.setattr(
        tasks,
        "execute_http_request",
        reject_endpoint,
    )

    result = tasks.execute_integration_run.run(str(run_id))

    assert result["status"] == "failed"
    assert run.status == "failed"
    assert run.error_message == error_message
    assert run.completed_at is not None


def test_duplicate_delivery_does_not_repeat_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, run = setup_fake_task_db(monkeypatch)
    run.status = "succeeded"
    run.attempts = 1

    def unexpected_request(**kwargs):
        pytest.fail("A completed run must not issue another HTTP request.")

    monkeypatch.setattr(
        tasks,
        "execute_http_request",
        unexpected_request,
    )

    result = tasks.execute_integration_run.run(str(run_id))

    assert result["status"] == "succeeded"
    assert run.attempts == 1


def test_network_timeout_fails_after_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, run = setup_fake_task_db(monkeypatch)
    timeout_error = httpx2.TimeoutException("request timed out")
    retry_calls = []

    def raise_timeout(**kwargs):
        raise timeout_error

    class RetryRequested(Exception):
        pass

    def fake_retry(*, exc, countdown):
        retry_calls.append((exc, countdown))
        raise RetryRequested()

    monkeypatch.setattr(
        tasks,
        "execute_http_request",
        raise_timeout,
    )
    monkeypatch.setattr(
        tasks.execute_integration_run,
        "retry",
        fake_retry,
    )
    monkeypatch.setattr(
        tasks.execute_integration_run.request,
        "retries",
        tasks.execute_integration_run.max_retries,
    )

    result = tasks.execute_integration_run.run(str(run_id))

    assert result["status"] == "failed"
    assert run.status == "failed"
    assert run.attempts == 1
    assert run.error_message == "TimeoutException"
    assert run.completed_at is not None
    assert retry_calls == []
