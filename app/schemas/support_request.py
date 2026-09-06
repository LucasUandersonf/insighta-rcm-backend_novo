from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SupportRequestCreateRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=5000)


class SupportRequestResponse(BaseModel):
    id: UUID
    subject: str
    message: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
