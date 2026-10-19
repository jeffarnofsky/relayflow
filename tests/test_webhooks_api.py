import hashlib
import hmac
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from relayflow.api.routes import webhooks
from relayflow.db.session import get_db
from relayflow.main import app
from relayflow.models.webhook_event import WebhookEvent

client = TestClient(app)
TEST_SECRET = "test-webhook-secret"


class FakeDB:
    """Minimal fake session for webhook API tests."""

    def __init__(self, existing_event: WebhookEvent | None = None):
        self.events: list[WebhookEvent] = []
        self.pending: list[WebhookEvent] = []
        self.existing_event = existing_event

    def add(self, event: WebhookEvent) -> None:
        if event.id is None:
            event.id = uuid4()

        if event.received_at is None:
            event.received_at = datetime.now(UTC)

        self.pending.append(event)

    def commit(self) -> None:
        for event in self.pending:
            duplicate_exists = any(
                saved.source == event.source and saved.external_event_id == event.external_event_id
                for saved in self.events
            )

            if (
                self.existing_event is not None
                and self.existing_event.source == event.source
                and self.existing_event.external_event_id == event.external_event_id
            ):
                duplicate_exists = True

            if duplicate_exists:
                raise IntegrityError(
                    "INSERT webhook event",
                    {},
                    Exception("duplicate event"),
                )

        self.events.extend(self.pending)
        self.pending.clear()

    def rollback(self) -> None:
        self.pending.clear()

    def refresh(self, event: WebhookEvent) -> None:
        pass

    def scalar(self, statement):
        return self.existing_event

    def close(self) -> None:
        pass


@pytest.fixture
def configure_webhook_api(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        webhooks.settings,
        "webhook_signing_secret",
        TEST_SECRET,
    )

    def configure_db(db: FakeDB) -> None:
        def override_get_db():
            yield db

        monkeypatch.setitem(
            app.dependency_overrides,
            get_db,
            override_get_db,
        )

    return configure_db


def signed_headers(
    body: bytes,
    *,
    event_id: str = "evt-001",
) -> dict[str, str]:
    digest = hmac.new(
        TEST_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return {
        "Content-Type": "application/json",
        "X-Webhook-Signature": f"sha256={digest}",
        "X-Webhook-Event-Id": event_id,
        "X-Webhook-Event-Type": "invoice.created",
    }


def test_invalid_signature_returns_401(
    configure_webhook_api,
) -> None:
    configure_webhook_api(FakeDB())

    response = client.post(
        "/api/v1/webhooks/demo",
        content=b'{"id":"evt-001"}',
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": "sha256=invalid",
            "X-Webhook-Event-Id": "evt-001",
        },
    )

    assert response.status_code == 401


def test_unsupported_content_type_returns_415(
    configure_webhook_api,
) -> None:
    configure_webhook_api(FakeDB())

    response = client.post(
        "/api/v1/webhooks/demo",
        content=b'{"id":"evt-001"}',
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 415


def test_invalid_json_returns_400(
    configure_webhook_api,
) -> None:
    configure_webhook_api(FakeDB())
    body = b'{"id":'

    response = client.post(
        "/api/v1/webhooks/demo",
        content=body,
        headers=signed_headers(body),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Webhook body must contain valid JSON."


def test_processed_duplicate_is_not_enqueued_again(
    monkeypatch: pytest.MonkeyPatch,
    configure_webhook_api,
) -> None:
    existing_event = WebhookEvent(
        id=uuid4(),
        source="demo",
        external_event_id="evt-001",
        event_type="invoice.created",
        payload={"id": "evt-001"},
        status="processed",
        received_at=datetime.now(UTC),
    )
    db = FakeDB(existing_event=existing_event)
    configure_webhook_api(db)

    def unexpected_enqueue(*args, **kwargs):
        pytest.fail("A processed duplicate must not be enqueued again.")

    monkeypatch.setattr(
        webhooks.process_webhook_event,
        "apply_async",
        unexpected_enqueue,
    )

    body = b'{"id":"evt-001"}'
    response = client.post(
        "/api/v1/webhooks/demo",
        content=body,
        headers=signed_headers(body),
    )

    assert response.status_code == 200
    assert response.json()["duplicate"] is True
    assert response.json()["id"] == str(existing_event.id)
    assert db.events == []


def test_queue_failure_returns_503_and_preserves_event(
    monkeypatch: pytest.MonkeyPatch,
    configure_webhook_api,
) -> None:
    db = FakeDB()
    configure_webhook_api(db)

    def fail_enqueue(*args, **kwargs):
        raise RuntimeError("simulated broker failure")

    monkeypatch.setattr(
        webhooks.process_webhook_event,
        "apply_async",
        fail_enqueue,
    )

    body = b'{"id":"evt-001"}'
    response = client.post(
        "/api/v1/webhooks/demo",
        content=body,
        headers=signed_headers(body),
    )

    assert response.status_code == 503
    assert len(db.events) == 1
    assert db.events[0].external_event_id == "evt-001"
    assert db.events[0].status == "received"
