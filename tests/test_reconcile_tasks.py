from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from relayflow import tasks


class FakeResult:
    def __init__(self, values: list[UUID]) -> None:
        self.values = values

    def scalars(self) -> FakeResult:
        return self

    def all(self) -> list[UUID]:
        return self.values


class FakeSession:
    def __init__(self, stale_run_ids: list[UUID]) -> None:
        self.result = FakeResult(stale_run_ids)
        self.execute_calls = 0
        self.commit_calls = 0

    def __enter__(self) -> FakeSession:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> bool:
        return False

    def execute(self, statement: object) -> FakeResult:
        self.execute_calls += 1
        return self.result

    def commit(self) -> None:
        self.commit_calls += 1


@pytest.mark.parametrize("stale_count", [0, 2])
def test_reconcile_stale_runs_reports_and_notifies(
    monkeypatch: pytest.MonkeyPatch,
    stale_count: int,
) -> None:
    stale_run_ids = [uuid4() for _ in range(stale_count)]
    session = FakeSession(stale_run_ids)
    notifications: list[UUID] = []

    monkeypatch.setattr(
        tasks,
        "SessionLocal",
        lambda: session,
    )
    monkeypatch.setattr(
        tasks,
        "_enqueue_run_notifications",
        notifications.append,
    )

    result = tasks.reconcile_stale_runs.run()

    assert result == {"marked_failed": stale_count}
    assert session.execute_calls == 1
    assert session.commit_calls == 1
    assert notifications == stale_run_ids


class FakeWebhookEvent:
    def __init__(self, event_id: UUID) -> None:
        self.id = event_id
        self.last_reconciled_at = None


class FakeWebhookResult:
    def __init__(self, events: list[FakeWebhookEvent]) -> None:
        self.events = events

    def all(self) -> list[FakeWebhookEvent]:
        return self.events


class FakeWebhookQuery:
    def __init__(self) -> None:
        self.where_criteria: tuple[object, ...] = ()
        self.limit_value: int | None = None
        self.lock_options: dict[str, object] = {}
        self.ordered = False

    def where(self, *criteria: object) -> FakeWebhookQuery:
        self.where_criteria = criteria
        return self

    def order_by(self, *criteria: object) -> FakeWebhookQuery:
        self.ordered = True
        return self

    def limit(self, value: int) -> FakeWebhookQuery:
        self.limit_value = value
        return self

    def with_for_update(self, **kwargs: object) -> FakeWebhookQuery:
        self.lock_options = kwargs
        return self


class FakeWebhookSession:
    def __init__(self, events: list[FakeWebhookEvent]) -> None:
        self.result = FakeWebhookResult(events)
        self.commit_calls = 0
        self.scalars_calls = 0

    def __enter__(self) -> FakeWebhookSession:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> bool:
        return False

    def scalars(self, statement: object) -> FakeWebhookResult:
        self.scalars_calls += 1
        return self.result

    def commit(self) -> None:
        self.commit_calls += 1


@pytest.mark.parametrize("event_count", [0, 2])
def test_reconcile_stale_webhook_events_records_timestamp_and_queues(
    monkeypatch: pytest.MonkeyPatch,
    event_count: int,
) -> None:
    events = [FakeWebhookEvent(uuid4()) for _ in range(event_count)]
    session = FakeWebhookSession(events)
    query = FakeWebhookQuery()
    queued_ids: list[str] = []

    monkeypatch.setattr(tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(tasks, "select", lambda model: query)

    def fake_apply_async(*, args: list[str]) -> None:
        queued_ids.extend(args)

    monkeypatch.setattr(
        tasks.process_webhook_event,
        "apply_async",
        fake_apply_async,
    )

    result = tasks.reconcile_stale_webhook_events.run()

    assert result == {"requeued": event_count}
    assert session.scalars_calls == 1
    assert session.commit_calls == 1
    assert query.limit_value == 100
    assert query.ordered is True
    assert query.lock_options == {"skip_locked": True}
    assert len(query.where_criteria) == 3

    assert queued_ids == [str(event.id) for event in events]

    for event in events:
        assert event.last_reconciled_at is not None
        assert event.last_reconciled_at.tzinfo is not None


def test_reconcile_stale_webhook_events_continues_after_queue_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_event = FakeWebhookEvent(uuid4())
    second_event = FakeWebhookEvent(uuid4())
    events = [first_event, second_event]
    session = FakeWebhookSession(events)
    query = FakeWebhookQuery()
    attempted_ids: list[str] = []
    queued_ids: list[str] = []

    monkeypatch.setattr(tasks, "SessionLocal", lambda: session)
    monkeypatch.setattr(tasks, "select", lambda model: query)

    def fake_apply_async(*, args: list[str]) -> None:
        event_id = args[0]
        attempted_ids.append(event_id)

        if event_id == str(first_event.id):
            raise RuntimeError("Redis unavailable")

        queued_ids.append(event_id)

    monkeypatch.setattr(
        tasks.process_webhook_event,
        "apply_async",
        fake_apply_async,
    )

    result = tasks.reconcile_stale_webhook_events.run()

    assert result == {"requeued": 1}
    assert session.commit_calls == 1
    assert attempted_ids == [str(event.id) for event in events]
    assert queued_ids == [str(second_event.id)]

    for event in events:
        assert event.last_reconciled_at is not None
