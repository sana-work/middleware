from typing import Optional, Dict, Any
from pydantic import BaseModel


class SessionDocument(BaseModel):
    """MongoDB document shape for the sessions collection."""
    session_id: str
    soeid: str
    created_at: str
    updated_at: str
    last_correlation_id: str
    metadata: Optional[Dict[str, Any]] = None


class ExecutionDocument(BaseModel):
    """MongoDB document shape for the recon collection (Executions)."""
    correlation_id: str
    session_id: str
    soeid: str
    request_context: str
    backend_request: Optional[Dict[str, Any]] = None
    backend_ack: Optional[Dict[str, Any]] = None
    status: str
    latest_event_type: Optional[str] = None
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None


class EventDocument(BaseModel):
    """MongoDB document shape for the events collection."""
    event_idempotency_key: str
    correlation_id: str
    session_id: str
    soeid: str
    event_type: str
    normalized_event_type: str
    status: str
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    timestamp: str
    summary: str
    raw_payload: Optional[Dict[str, Any]] = None
    normalized_payload: Optional[Dict[str, Any]] = None
    # Kafka metadata for debugging
    kafka_topic: Optional[str] = None
    kafka_partition: Optional[int] = None
    kafka_offset: Optional[int] = None
    consumed_at: Optional[str] = None
    kafka_raw_key: Optional[str] = None
    created_at: str
