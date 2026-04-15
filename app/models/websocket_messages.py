from typing import Optional, Dict, Any
from pydantic import BaseModel


class WSConnectedMessage(BaseModel):
    """Sent immediately after WebSocket connection is accepted."""
    type: str = "connected"
    correlation_id: str
    session_id: str
    status: str
    timestamp: str


class WSEventMessage(BaseModel):
    """Sent when a new Kafka-derived event arrives."""
    type: str = "event"
    correlation_id: str
    session_id: str
    event: Dict[str, Any]


class WSStatusMessage(BaseModel):
    """Sent as a status-only update."""
    type: str = "status"
    correlation_id: str
    status: str
    latest_event_type: Optional[str] = None
    timestamp: str


class WSCompletedMessage(BaseModel):
    """Terminal message when execution completes successfully."""
    type: str = "completed"
    correlation_id: str
    status: str = "completed"
    latest_event_type: Optional[str] = None
    timestamp: str


class WSFailedMessage(BaseModel):
    """Terminal message when execution fails."""
    type: str = "failed"
    correlation_id: str
    status: str = "failed"
    latest_event_type: Optional[str] = None
    timestamp: str
    error: Optional[Dict[str, str]] = None


class WSHeartbeatMessage(BaseModel):
    """Periodic heartbeat to keep connections alive."""
    type: str = "heartbeat"
    correlation_id: str
    timestamp: str
