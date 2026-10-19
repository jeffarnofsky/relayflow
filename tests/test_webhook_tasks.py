from types import SimpleNamespace
from uuid import uuid4

import pytest

from relayflow import tasks


def test_processed_webhook_event_is_not_enqueued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_id = uuid4()
    event = SimpleNamespace(
        id=event_id,
        status="processed",
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def scalar(self, statement):
            return event

    monkeypatch.setattr(
        tasks,
        "SessionLocal",
        lambda: FakeSession(),
    )

    def unexpected_enqueue(*args, **kwargs):
        pytest.fail("A processed webhook must not be enqueued again.")

    monkeypatch.setattr(
        tasks.execute_integration_run,
        "apply_async",
        unexpected_enqueue,
    )

    result = tasks.process_webhook_event.run(str(event_id))

    assert result == {
        "event_id": str(event_id),
        "status": "processed",
    }
    assert event.status == "processed"


def test_enqueue_failure_marks_run_and_event_failed_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from relayflow.models.run import Run
    from relayflow.models.webhook_event import WebhookEvent

    event_id = uuid4()
    integration_id = uuid4()
    run_id = uuid4()

    event = SimpleNamespace(
        id=event_id,
        source="demo",
        event_type="invoice.created",
        status="received",
        error_message=None,
        processed_at=None,
    )
    integration = SimpleNamespace(
        id=integration_id,
        is_active=True,
    )
    run = SimpleNamespace(
        id=run_id,
        integration_id=integration_id,
        webhook_event_id=event_id,
        celery_task_id=str(uuid4()),
        status="queued",
        error_message=None,
        completed_at=None,
    )
    subscription = SimpleNamespace(
        integration_id=integration_id,
    )

    class FakeScalars:
        def all(self):
            return [subscription]

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def scalar(self, statement):
            sql = str(statement)

            if "webhook_events" in sql:
                return event

            if "runs" in sql:
                return run

            return None

        def scalars(self, statement):
            return FakeScalars()

        def get(self, model, object_id):
            if model is Integration and object_id == integration_id:
                return integration
            if model is Run and object_id == run_id:
                return run
            if model is WebhookEvent and object_id == event_id:
                return event
            return None

        def add(self, obj):
            pass

        def flush(self):
            pass

        def commit(self):
            pass

    from relayflow.models.integration import Integration

    monkeypatch.setattr(tasks, "SessionLocal", FakeSession)

    def fail_enqueue(*args, **kwargs):
        raise RuntimeError("simulated broker failure")

    monkeypatch.setattr(
        tasks.execute_integration_run,
        "apply_async",
        fail_enqueue,
    )

    class RetryRequested(Exception):
        pass

    retry_calls = []

    def fake_retry(*, exc, countdown):
        retry_calls.append((exc, countdown))
        raise RetryRequested()

    monkeypatch.setattr(
        tasks.process_webhook_event,
        "retry",
        fake_retry,
    )

    with pytest.raises(RetryRequested):
        tasks.process_webhook_event.run(str(event_id))

    assert run.status == "failed"
    assert run.error_message == tasks.WEBHOOK_QUEUE_ERROR
    assert run.completed_at is not None
    assert event.status == "failed"
    assert event.error_message == "One or more integration runs could not be queued."
    assert len(retry_calls) == 1
    assert isinstance(retry_calls[0][0], RuntimeError)
    assert retry_calls[0][1] == 1
