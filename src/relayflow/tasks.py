from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID

import httpx2

from relayflow.celery_app import celery_app
from relayflow.core.http_client import (
    UnsafeEndpointError,
    execute_http_request,
)
from relayflow.db.session import SessionLocal
from relayflow.models.integration import Integration
from relayflow.models.run import Run


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

    with SessionLocal() as db:
        run = db.get(Run, parsed_run_id)

        if run is None:
            return {"run_id": run_id, "status": "missing"}

        integration = db.get(Integration, run.integration_id)

        if integration is None:
            run.status = "failed"
            run.error_message = "Integration not found."
            run.completed_at = datetime.now(UTC)
            db.commit()

            return {"run_id": run_id, "status": "failed"}

        if not integration.is_active:
            run.status = "failed"
            run.error_message = "Integration is inactive."
            run.completed_at = datetime.now(UTC)
            db.commit()

            return {"run_id": run_id, "status": "failed"}

        # Copy the configuration needed by the HTTP request.
        url = integration.endpoint_url
        method = integration.http_method
        headers = dict(integration.request_headers)
        body = integration.request_body

        run.status = "running"
        run.attempts += 1
        run.started_at = run.started_at or datetime.now(UTC)
        run.completed_at = None
        run.http_status_code = None
        run.response_excerpt = None
        run.error_message = None
        db.commit()

    started = perf_counter()

    try:
        http_status_code, response_excerpt = execute_http_request(
            url=url,
            method=method,
            headers=headers,
            body=body,
        )

    except (httpx2.TimeoutException, httpx2.NetworkError) as exc:
        elapsed_ms = int((perf_counter() - started) * 1000)
        should_retry = self.request.retries < self.max_retries

        with SessionLocal() as db:
            run = db.get(Run, parsed_run_id)

            if run is None:
                return {"run_id": run_id, "status": "missing"}

            run.duration_ms = elapsed_ms
            run.error_message = type(exc).__name__

            if should_retry:
                run.status = "queued"
            else:
                run.status = "failed"
                run.completed_at = datetime.now(UTC)

            db.commit()

        if should_retry:
            raise self.retry(
                exc=exc,
                countdown=2**self.request.retries,
            ) from exc

        return {"run_id": run_id, "status": "failed"}

    except Exception as exc:
        elapsed_ms = int((perf_counter() - started) * 1000)

        with SessionLocal() as db:
            run = db.get(Run, parsed_run_id)

            if run is None:
                return {"run_id": run_id, "status": "missing"}

            run.status = "failed"
            run.error_message = (
                str(exc) if isinstance(exc, UnsafeEndpointError) else type(exc).__name__
            )
            run.duration_ms = elapsed_ms
            run.completed_at = datetime.now(UTC)
            db.commit()

        return {"run_id": run_id, "status": "failed"}

    elapsed_ms = int((perf_counter() - started) * 1000)
    succeeded = 200 <= http_status_code < 300

    with SessionLocal() as db:
        run = db.get(Run, parsed_run_id)

        if run is None:
            return {"run_id": run_id, "status": "missing"}

        run.http_status_code = http_status_code
        run.response_excerpt = response_excerpt
        run.duration_ms = elapsed_ms
        run.status = "succeeded" if succeeded else "failed"
        run.error_message = (
            None if succeeded else f"Remote endpoint returned HTTP {http_status_code}."
        )
        run.completed_at = datetime.now(UTC)
        db.commit()

    return {
        "run_id": run_id,
        "status": "succeeded" if succeeded else "failed",
        "http_status_code": http_status_code,
    }
