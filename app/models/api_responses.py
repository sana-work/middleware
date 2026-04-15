from typing import Optional, List, Dict, Any
from pydantic import BaseModel


# --- Execute endpoint ---

class ExecuteData(BaseModel):
    """Payload for the immediate response from POST /chat/execute."""
    correlation_id: str
    session_id: str
    status: str
    backend_ack: Dict[str, Any]
    websocket_url: str
    created_at: str


class ChatExecuteResponse(BaseModel):
    """Top-level response for POST /chat/execute."""
    success: bool
    message: str
    data: ExecuteData


# --- Status endpoint ---

class EventSummary(BaseModel):
    """A single normalized event in a timeline."""
    event_id: Optional[str] = None
    event_type: str
    normalized_event_type: str
    status: str
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    timestamp: str
    summary: str


class StatusData(BaseModel):
    """Payload for GET /chat/status/{correlation_id}."""
    correlation_id: str
    session_id: str
    soeid: str
    status: str
    latest_event_type: Optional[str] = None
    started_at: str
    updated_at: str
    completed_at: Optional[str] = None
    request_context: str
    events: List[EventSummary] = []


class ChatStatusResponse(BaseModel):
    """Top-level response for GET /chat/status/{correlation_id}."""
    success: bool
    data: StatusData


# --- History endpoint ---

class ExecutionInHistory(BaseModel):
    """One execution inside a session history response."""
    correlation_id: str
    status: str
    request_context: str
    started_at: str
    completed_at: Optional[str] = None
    latest_event_type: Optional[str] = None
    events: List[EventSummary] = []


class HistoryData(BaseModel):
    """Payload for GET /chat/history/{session_id}."""
    session_id: str
    soeid: str
    created_at: str
    updated_at: str
    executions: List[ExecutionInHistory] = []
    total: int = 0
    limit: int = 50
    skip: int = 0


class ChatHistoryResponse(BaseModel):
    """Top-level response for GET /chat/history/{session_id}."""
    success: bool
    data: HistoryData


# --- Health endpoints ---

class HealthLiveResponse(BaseModel):
    """Response for GET /health/live."""
    status: str = "ok"


class HealthReadyChecks(BaseModel):
    """Individual dependency checks."""
    mongodb: str
    kafka: str
    token_client_config: str
    backend_client_config: str


class HealthReadyResponse(BaseModel):
    """Response for GET /health/ready."""
    status: str
    checks: HealthReadyChecks
