from pydantic import BaseModel, ConfigDict, Field
from typing import Optional


class ChatRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "example": {"session_id": "550e8400-e29b-41d4-a716-446655440000", "message": "여기에 메시지를 입력하세요"}
    })
    session_id: Optional[str] = Field(default=None, description="기존 세션 ID (없으면 새 세션 생성)")
    message: str = Field(..., description="사용자 메시지")


class CollectedData(BaseModel):
    service_description: Optional[str] = None
    target_users: Optional[str] = None
    target_market: Optional[str] = None
    revenue_model: Optional[str] = None
    revenue_details: Optional[str] = None
    mvp_features: Optional[str] = None
    dev_timeline_stack: Optional[str] = None
    acquisition_channel: Optional[str] = None
    channel_resources: Optional[str] = None
    competitors: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    message: str
    is_complete: bool
    collected_data: CollectedData


class SessionInfo(BaseModel):
    session_id: str
    collected_data: CollectedData
    is_complete: bool
    message_count: int


class DraftResponse(BaseModel):
    draft: str
    session_id: str
