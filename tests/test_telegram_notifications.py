from types import SimpleNamespace
from uuid import uuid4

import pytest

from relayflow.core.telegram_notifications import (
    build_run_notification,
    parse_allowed_telegram_user_ids,
)


def test_parse_allowed_user_ids_deduplicates() -> None:
    assert parse_allowed_telegram_user_ids("12345, 67890,12345") == [
        12345,
        67890,
    ]


def test_empty_allowlist_returns_no_recipients() -> None:
    assert parse_allowed_telegram_user_ids("") == []


def test_invalid_user_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_allowed_telegram_user_ids("12345,not-a-number")


def test_notification_excludes_response_and_error_contents() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        status="failed",
        http_status_code=502,
        duration_ms=450,
        response_excerpt="private-response-data",
        error_message="sensitive-error-content",
    )
    integration = SimpleNamespace(
        id=uuid4(),
        name="  billing   integration  ",
    )

    message = build_run_notification(run, integration)

    assert "FAILED" in message
    assert "billing integration" in message
    assert str(run.id) in message
    assert "502" in message
    assert "private-response-data" not in message
    assert "sensitive-error-content" not in message


def test_notification_requires_terminal_status() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        status="running",
        http_status_code=None,
        duration_ms=None,
    )
    integration = SimpleNamespace(name="billing integration")

    with pytest.raises(ValueError):
        build_run_notification(run, integration)
