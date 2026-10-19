import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from relayflow.db.base import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'processing', 'processed', 'failed')",
            name="status_allowed",
        ),
        UniqueConstraint(
            "source",
            "external_event_id",
            name="source_external_event_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    source: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    external_event_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    event_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="received",
        server_default="received",
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
