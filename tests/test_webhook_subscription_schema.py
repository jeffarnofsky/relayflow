from uuid import uuid4

import pytest
from pydantic import ValidationError

from relayflow.schemas.webhook_subscription import WebhookSubscriptionCreate


def test_valid_subscription_payload() -> None:
    payload = WebhookSubscriptionCreate(
        source="demo",
        event_type="invoice.created",
        integration_id=uuid4(),
    )

    assert payload.source == "demo"
    assert payload.event_type == "invoice.created"
    assert payload.is_active is True


def test_blank_event_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        WebhookSubscriptionCreate(
            source="demo",
            event_type="   ",
            integration_id=uuid4(),
        )


def test_invalid_source_is_rejected() -> None:
    with pytest.raises(ValidationError):
        WebhookSubscriptionCreate(
            source="../demo",
            event_type="invoice.created",
            integration_id=uuid4(),
        )
