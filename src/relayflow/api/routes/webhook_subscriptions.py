from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from relayflow.core.security import require_admin_api_key
from relayflow.db.session import get_db
from relayflow.models.integration import Integration
from relayflow.models.webhook_subscription import WebhookSubscription
from relayflow.schemas.webhook_subscription import (
    WebhookSubscriptionCreate,
    WebhookSubscriptionRead,
)

router = APIRouter(
    prefix="/webhook-subscriptions",
    tags=["Webhook Subscriptions"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.post(
    "",
    response_model=WebhookSubscriptionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_webhook_subscription(
    payload: WebhookSubscriptionCreate,
    db: Session = Depends(get_db),
) -> WebhookSubscription:
    integration = db.get(Integration, payload.integration_id)

    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found.",
        )

    subscription = WebhookSubscription(
        source=payload.source,
        event_type=payload.event_type,
        integration_id=payload.integration_id,
        is_active=payload.is_active,
    )

    db.add(subscription)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This webhook subscription already exists.",
        ) from exc

    db.refresh(subscription)
    return subscription


@router.get(
    "",
    response_model=list[WebhookSubscriptionRead],
)
def list_webhook_subscriptions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[WebhookSubscription]:
    statement = (
        select(WebhookSubscription)
        .order_by(WebhookSubscription.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    return list(db.scalars(statement).all())


@router.delete(
    "/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_webhook_subscription(
    subscription_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    subscription = db.get(WebhookSubscription, subscription_id)

    if subscription is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook subscription not found.",
        )

    db.delete(subscription)
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
