from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    integration_id: UUID
    celery_task_id: str | None
    status: Literal["queued", "running", "succeeded", "failed"]
    attempts: int
    http_status_code: int | None
    response_excerpt: str | None
    error_message: str | None
    duration_ms: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
