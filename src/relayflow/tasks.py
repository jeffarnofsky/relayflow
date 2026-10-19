from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import UUID

import httpx2
from sqlalchemy import update

from relayflow.celery_app import celery_app
from relayflow.core.credentials import decrypt_auth_config
from relayflow.core.http_client import (
    UnsafeEndpointError,
    execute_http_request,
)
from relayflow.db.session import SessionLocal
from relayflow.models.integration import Integration
from relayflow.models.run import Run

STALE_RUN_MINUTES = 15

STALE_RUN_ERROR = (
    "Execution exceeded the allowed runtime. "
    "The run was marked failed to avoid an unsafe automatic replay."
)


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
            db.commit()
            return str(values.get("status", "running"))

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
        )
        count = max(result.rowcount or 0, 0)
        db.commit()

    return {"marked_failed": count}
