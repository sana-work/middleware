from typing import List, Optional, Any
from pydantic import BaseModel


class ExportEventDTO(BaseModel):
    event_type: str
    normalized_event_type: str
    status: str
    summary: str
    business_description: Optional[str] = None
    timestamp: str
    tool_name: Optional[str] = None
    raw_payload_excerpt: Optional[Any] = None


class ExportExecutionDTO(BaseModel):
    soeid: str
    session_id: str
    correlation_id: str
    status: str
    request_context: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    latest_event_type: Optional[str] = None
    events: List[ExportEventDTO] = []
    final_response: Optional[str] = None
    error_details: Optional[str] = None
