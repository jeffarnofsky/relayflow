import asyncio
import logging
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import httpx2
from sqlalchemy import select, update
from telegram.error import TelegramError

from relayflow.celery_app import celery_app
from relayflow.core.config import settings
from relayflow.core.credentials import decrypt_auth_config
from relayflow.core.http_client import (
    UnsafeEndpointError,
    execute_http_request,
)
from relayflow.core.telegram_notifications import (
    build_run_notification,
    parse_allowed_telegram_user_ids,
    send_telegram_message,
)
from relayflow.db.session import SessionLocal
from relayflow.models.integration import Integration
from relayflow.models.run import Run
from relayflow.models.webhook_event import WebhookEvent
from relayflow.models.webhook_subscription import WebhookSubscription

STALE_RUN_MINUTES = 15

STALE_RUN_ERROR = (
    "Execution exceeded the allowed runtime. "
    "The run was marked failed to avoid an unsafe automatic replay."
)


logger = logging.getLogger(__name__)


def _claim_run(
    run_id: UUID,
) -> tuple[bool, str, dict[str, Any] | None, str | None]:
    """Atomically claim a queued run before making an external request."""
    now = datetime.now(UTC)

    with SessionLocal() as db:
        result = db.execute(
            update(Run)
            .where(
                Run.id == run_id,
                Run.status == "queued",
            )
            .values(
                status="running",
                attempts=Run.attempts + 1,
                started_at=now,
                completed_at=None,
                http_status_code=None,
                response_excerpt=None,
                error_message=None,
            )
        )

        if result.rowcount != 1:
            db.rollback()
            existing = db.get(Run, run_id)
            return (
                False,
                existing.status if existing is not None else "missing",
                None,
                None,
            )

        db.commit()

        run = db.get(Run, run_id)
        if run is None:
            return False, "missing", None, None

        integration = db.get(Integration, run.integration_id)

        if integration is None:
            return True, "running", None, "Integration not found."

        if not integration.is_active:
            return True, "running", None, "Integration is inactive."

        request_data: dict[str, Any] = {
            "url": integration.endpoint_url,
            "method": integration.http_method,
            "headers": dict(integration.request_headers),
            "body": integration.request_body,
            "auth_config_encrypted": integration.auth_config_encrypted,
        }

        return True, "running", request_data, None


def _update_run_if_running(run_id: UUID, **values: Any) -> str:
    """Update a run only if it is still owned by an active execution."""
    with SessionLocal() as db:
        result = db.execute(
            update(Run)
            .where(
                Run.id == run_id,
                Run.status == "running",
            )
            .values(**values)
        )

        if result.rowcount == 1:
            saved_status = str(values.get("status", "running"))
            db.commit()

            if saved_status in {"succeeded", "failed"}:
                _enqueue_run_notifications(run_id)

            return saved_status

        db.rollback()
        current = db.get(Run, run_id)
        return current.status if current is not None else "missing"


@celery_app.task(name="relayflow.health_check")
def worker_health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "relayflow-worker",
    }


@celery_app.task(
    bind=True,
    name="relayflow.execute_integration_run",
    max_retries=2,
)
def execute_integration_run(
    self,
    run_id: str,
) -> dict[str, str | int]:
    parsed_run_id = UUID(run_id)

    claimed, current_status, request_data, preflight_error = _claim_run(parsed_run_id)

    if not claimed:
        # A duplicate delivery must not repeat an external HTTP request.
        return {
            "run_id": run_id,
            "status": current_status,
        }

    if preflight_error is not None:
        final_status = _update_run_if_running(
            parsed_run_id,
            status="failed",
            error_message=preflight_error,
            completed_at=datetime.now(UTC),
        )
        return {
            "run_id": run_id,
            "status": final_status,
        }

    if request_data is None:
        return {
            "run_id": run_id,
            "status": "missing",
        }

    started = perf_counter()

    try:
        http_status_code, response_excerpt = execute_http_request(
            url=request_data["url"],
            method=request_data["method"],
            headers=request_data["headers"],
            body=request_data["body"],
            auth_config=decrypt_auth_config(request_data.get("auth_config_encrypted")),
        )

    except (httpx2.TimeoutException, httpx2.NetworkError) as exc:
        elapsed_ms = int((perf_counter() - started) * 1000)
        should_retry = self.request.retries < self.max_retries
        next_status = "queued" if should_retry else "failed"

        saved_status = _update_run_if_running(
            parsed_run_id,
            status=next_status,
            error_message=type(exc).__name__,
            duration_ms=elapsed_ms,
            completed_at=None if should_retry else datetime.now(UTC),
        )

        # The run may have been reconciled while this task was running.
        if saved_status != next_status:
            return {
                "run_id": run_id,
                "status": saved_status,
            }

        if should_retry:
            raise self.retry(
                exc=exc,
                countdown=2**self.request.retries,
            ) from exc

        return {
            "run_id": run_id,
            "status": "failed",
        }

    except Exception as exc:
        elapsed_ms = int((perf_counter() - started) * 1000)
        message = str(exc) if isinstance(exc, UnsafeEndpointError) else type(exc).__name__

        saved_status = _update_run_if_running(
            parsed_run_id,
            status="failed",
            error_message=message,
            duration_ms=elapsed_ms,
            completed_at=datetime.now(UTC),
        )

        return {
            "run_id": run_id,
            "status": saved_status,
        }

    elapsed_ms = int((perf_counter() - started) * 1000)
    succeeded = 200 <= http_status_code < 300
    final_status = "succeeded" if succeeded else "failed"

    saved_status = _update_run_if_running(
        parsed_run_id,
        status=final_status,
        http_status_code=http_status_code,
        response_excerpt=response_excerpt,
        duration_ms=elapsed_ms,
        error_message=(None if succeeded else f"Remote endpoint returned HTTP {http_status_code}."),
        completed_at=datetime.now(UTC),
    )

    return {
        "run_id": run_id,
        "status": saved_status,
        "http_status_code": http_status_code,
    }


@celery_app.task(name="relayflow.reconcile_stale_runs")
def reconcile_stale_runs() -> dict[str, int]:
    """Mark over-age running executions failed without replaying them."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=STALE_RUN_MINUTES)

    with SessionLocal() as db:
        result = db.execute(
            update(Run)
            .where(
                Run.status == "running",
                Run.started_at.is_not(None),
                Run.started_at < cutoff,
            )
            .values(
                status="failed",
                error_message=STALE_RUN_ERROR,
                completed_at=now,
            )
            .returning(Run.id)
        )

        stale_run_ids = list(result.scalars().all())
        db.commit()

    for stale_run_id in stale_run_ids:
        _enqueue_run_notifications(stale_run_id)

    return {"marked_failed": len(stale_run_ids)}


WEBHOOK_QUEUE_ERROR = "Unable to enqueue webhook-triggered run."


@celery_app.task(
    bind=True,
    name="relayflow.process_webhook_event",
    max_retries=3,
)
def process_webhook_event(
    self,
    event_id: str,
) -> dict[str, str | int]:
    parsed_event_id = UUID(event_id)
    pending_runs: list[tuple[str, str]] = []

    with SessionLocal() as db:
        event = db.scalar(
            select(WebhookEvent).where(WebhookEvent.id == parsed_event_id).with_for_update()
        )

        if event is None:
            return {"event_id": event_id, "status": "missing"}

        if event.status == "processed":
            return {"event_id": event_id, "status": "processed"}

        if event.status not in {"received", "processing", "failed"}:
            return {"event_id": event_id, "status": event.status}

        event.status = "processing"
        event.error_message = None

        subscriptions = db.scalars(
            select(WebhookSubscription).where(
                WebhookSubscription.source == event.source,
                WebhookSubscription.event_type == event.event_type,
                WebhookSubscription.is_active.is_(True),
            )
        ).all()

        for subscription in subscriptions:
            integration = db.get(Integration, subscription.integration_id)

            if integration is None or not integration.is_active:
                continue

            run = db.scalar(
                select(Run)
                .where(
                    Run.webhook_event_id == event.id,
                    Run.integration_id == integration.id,
                )
                .with_for_update()
            )

            if run is None:
                run_id = uuid4()
                task_id = str(uuid4())

                run = Run(
                    id=run_id,
                    integration_id=integration.id,
                    webhook_event_id=event.id,
                    celery_task_id=task_id,
                    status="queued",
                )

                db.add(run)
                db.flush()

                pending_runs.append((str(run_id), task_id))

            elif run.status == "failed" and run.error_message == WEBHOOK_QUEUE_ERROR:
                # Retry only failures caused by enqueueing.
                # Do not replay completed HTTP requests.
                task_id = str(uuid4())
                run.status = "queued"
                run.celery_task_id = task_id
                run.error_message = None
                run.completed_at = None

                pending_runs.append((str(run.id), task_id))

            elif run.status == "queued":
                task_id = run.celery_task_id or str(uuid4())
                run.celery_task_id = task_id

                pending_runs.append((str(run.id), task_id))

        db.commit()

    for run_id, task_id in pending_runs:
        try:
            execute_integration_run.apply_async(
                args=[run_id],
                task_id=task_id,
            )
        except Exception as exc:
            with SessionLocal() as db:
                run = db.get(Run, UUID(run_id))

                if run is not None and run.status == "queued":
                    run.status = "failed"
                    run.error_message = WEBHOOK_QUEUE_ERROR
                    run.completed_at = datetime.now(UTC)

                event = db.get(WebhookEvent, parsed_event_id)

                if event is not None:
                    event.status = "failed"
                    event.error_message = "One or more integration runs could not be queued."

                db.commit()

            raise self.retry(
                exc=exc,
                countdown=2**self.request.retries,
            ) from exc

    with SessionLocal() as db:
        event = db.scalar(
            select(WebhookEvent).where(WebhookEvent.id == parsed_event_id).with_for_update()
        )

        if event is not None and event.status != "processed":
            event.status = "processed"
            event.processed_at = datetime.now(UTC)
            event.error_message = None
            db.commit()

    return {
        "event_id": event_id,
        "status": "processed",
        "runs_queued": len(pending_runs),
    }


@celery_app.task(name="relayflow.reconcile_stale_webhook_events")
def reconcile_stale_webhook_events() -> dict[str, int]:
    cutoff = datetime.now(UTC) - timedelta(minutes=5)

    with SessionLocal() as db:
        event_ids = list(
            db.scalars(
                select(WebhookEvent.id)
                .where(
                    WebhookEvent.status.in_(["received", "processing", "failed"]),
                    WebhookEvent.received_at < cutoff,
                )
                .order_by(WebhookEvent.received_at.asc())
                .limit(100)
            ).all()
        )

    for event_id in event_ids:
        process_webhook_event.apply_async(args=[str(event_id)])

    return {"requeued": len(event_ids)}


@celery_app.task(
    name="relayflow.send_telegram_run_notification",
    autoretry_for=(TelegramError,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=3,
)
def send_telegram_run_notification(
    run_id: str,
    chat_id: int,
) -> dict[str, str]:
    token = settings.telegram_bot_token

    if not token:
        return {"status": "skipped", "reason": "telegram_not_configured"}

    parsed_run_id = UUID(run_id)

    with SessionLocal() as db:
        run = db.get(Run, parsed_run_id)

        if run is None:
            return {"status": "skipped", "reason": "run_not_found"}

        if run.status not in {"succeeded", "failed"}:
            return {"status": "skipped", "reason": "run_not_finished"}

        integration = db.get(Integration, run.integration_id)

        if integration is None:
            return {"status": "skipped", "reason": "integration_not_found"}

        message = build_run_notification(run, integration)

    asyncio.run(
        send_telegram_message(
            token=token,
            chat_id=chat_id,
            message=message,
        )
    )

    return {
        "status": "sent",
        "run_id": run_id,
        "chat_id": str(chat_id),
    }


def _enqueue_run_notifications(run_id: UUID) -> None:
    if not settings.telegram_bot_token:
        return

    try:
        chat_ids = parse_allowed_telegram_user_ids(settings.telegram_allowed_user_ids)
    except ValueError:
        logger.error("Telegram notifications disabled: invalid allowed user IDs.")
        return

    if not chat_ids:
        logger.warning("Telegram notifications enabled without an allowed user ID.")
        return

    for chat_id in chat_ids:
        try:
            send_telegram_run_notification.apply_async(
                args=[str(run_id), chat_id],
            )
        except Exception:
            # Do not fail the already-completed Run because notification
            # dispatch failed. The error is recorded without printing secrets.
            logger.exception(
                "Could not enqueue Telegram notification for Run %s.",
                run_id,
            )
