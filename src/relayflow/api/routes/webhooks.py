import json
import re

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from relayflow.core.config import settings
from relayflow.core.webhook_security import verify_webhook_signature
from relayflow.db.session import get_db
from relayflow.models.webhook_event import WebhookEvent
from relayflow.schemas.webhook import WebhookEventRead
from relayflow.tasks import process_webhook_event

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

MAX_WEBHOOK_BYTES = 1_048_576
SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


async def _read_limited_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")

    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length.",
            ) from exc

        if declared_length < 0 or declared_length > MAX_WEBHOOK_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Webhook payload is too large.",
            )

    body = bytearray()

    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_WEBHOOK_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Webhook payload is too large.",
            )

        body.extend(chunk)

    return bytes(body)


@router.post(
    "/{source}",
    response_model=WebhookEventRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_webhook(
    source: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> WebhookEventRead:
    if not SOURCE_PATTERN.fullmatch(source):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook source.",
        )

    secret = settings.webhook_signing_secret

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook signing is not configured.",
        )

    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()

    if media_type != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/json.",
        )

    raw_body = await _read_limited_body(request)

    signature = request.headers.get("X-Webhook-Signature")

    if not verify_webhook_signature(raw_body, signature, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )

    external_event_id = request.headers.get("X-Webhook-Event-Id", "").strip()

    if (
        not external_event_id
        or len(external_event_id) > 255
        or any(ord(char) < 32 for char in external_event_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid X-Webhook-Event-Id is required.",
        )

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook body must contain valid JSON.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Webhook payload must be a JSON object.",
        )

    event_type = request.headers.get("X-Webhook-Event-Type")
    if event_type is None:
        event_type = payload.get("event_type", payload.get("type"))

    if event_type is not None:
        if not isinstance(event_type, str) or len(event_type) > 255:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid webhook event type.",
            )

        event_type = event_type or None

    event = WebhookEvent(
        source=source,
        external_event_id=external_event_id,
        event_type=event_type,
        payload=payload,
        status="received",
    )

    duplicate = False

    try:
        db.add(event)
        db.commit()
        db.refresh(event)

    except IntegrityError as exc:
        db.rollback()

        event = db.scalar(
            select(WebhookEvent).where(
                WebhookEvent.source == source,
                WebhookEvent.external_event_id == external_event_id,
            )
        )

        if event is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Could not register the webhook event.",
            ) from exc

        duplicate = True

    # If an earlier delivery was saved but its task could not be queued,
    # a retry with the same event ID can enqueue it again.
    should_enqueue = not duplicate or event.status in {
        "received",
        "failed",
    }

    if should_enqueue:
        try:
            process_webhook_event.apply_async(
                args=[str(event.id)],
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "The event was saved but could not be queued. Retry with the same event ID."
                ),
            ) from exc

    response.status_code = status.HTTP_202_ACCEPTED if should_enqueue else status.HTTP_200_OK

    return WebhookEventRead(
        id=event.id,
        source=event.source,
        external_event_id=event.external_event_id,
        event_type=event.event_type,
        status=event.status,
        received_at=event.received_at,
        duplicate=duplicate,
    )
