from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=255)


class SessionResponse(BaseModel):
    id: UUID
    title: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
