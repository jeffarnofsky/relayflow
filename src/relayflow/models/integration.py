import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from relayflow.db.base import Base

if TYPE_CHECKING:
    from relayflow.models.run import Run


class Integration(Base):
    __tablename__ = "integrations"

    __table_args__ = (
        CheckConstraint(
            "http_method IN ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')",
            name="http_method_allowed",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(120),
        unique=True,
        nullable=False,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    endpoint_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )

    http_method: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="GET",
        server_default="GET",
    )

    # Only non-sensitive headers belong here.
    request_headers: Mapped[dict[str, str]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

    request_body: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )

    # Reserved for encrypted authentication settings.
    # Never store raw API tokens in this field.
    auth_config_encrypted: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    runs: Mapped[list["Run"]] = relationship(
        back_populates="integration",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
