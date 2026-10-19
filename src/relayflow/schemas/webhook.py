from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class WebhookEventRead(BaseModel):
    id: UUID
    source: str
    external_event_id: str
    event_type: str | None
    status: Literal["received", "processing", "processed", "failed"]
    received_at: datetime
    duplicate: bool
