from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from relayflow.core.security import require_admin_api_key
from relayflow.db.session import get_db
from relayflow.models.run import Run
from relayflow.models.webhook_event import WebhookEvent

router = APIRouter(
    prefix="/ops",
    tags=["Operations"],
    dependencies=[Depends(require_admin_api_key)],
)


def _status_counts(
    db: Session,
    model: type,
    expected_statuses: tuple[str, ...],
) -> dict[str, int]:
    counts = dict.fromkeys(expected_statuses, 0)

    rows = db.execute(select(model.status, func.count()).group_by(model.status)).all()

    for item_status, count in rows:
        counts[item_status] = count

    return counts


def _age_seconds(
    timestamp: datetime | None,
    now: datetime,
) -> int | None:
    if timestamp is None:
        return None

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)

    return max(0, int((now - timestamp).total_seconds()))


@router.get("/summary")
def operational_summary(
    db: Session = Depends(get_db),
) -> dict[str, object]:
    now = datetime.now(UTC)

    runs_by_status = _status_counts(
        db,
        Run,
        ("queued", "running", "succeeded", "failed"),
    )

    webhook_events_by_status = _status_counts(
        db,
        WebhookEvent,
        ("received", "processing", "processed", "failed"),
    )

    oldest_running_run = db.scalar(
        select(func.min(Run.started_at)).where(
            Run.status == "running",
            Run.started_at.is_not(None),
        )
    )

    oldest_unprocessed_webhook = db.scalar(
        select(func.min(WebhookEvent.received_at)).where(
            WebhookEvent.status.in_(("received", "processing", "failed"))
        )
    )

    return {
        "generated_at": now.isoformat(),
        "runs_by_status": runs_by_status,
        "webhook_events_by_status": webhook_events_by_status,
        "oldest_running_run_age_seconds": _age_seconds(
            oldest_running_run,
            now,
        ),
        "oldest_unprocessed_webhook_age_seconds": _age_seconds(
            oldest_unprocessed_webhook,
            now,
        ),
    }
