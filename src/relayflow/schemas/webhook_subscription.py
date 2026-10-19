from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebhookSubscriptionCreate(BaseModel):
    source: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$",
    )
    event_type: str = Field(min_length=1, max_length=255)
    integration_id: UUID
    is_active: bool = True

    @field_validator("event_type")
    @classmethod
    def normalize_event_type(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("event_type must not be blank.")

        return value


class WebhookSubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    event_type: str
    integration_id: UUID
    is_active: bool
    created_at: datetime
