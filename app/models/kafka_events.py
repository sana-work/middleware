from typing import Optional, Dict, Any, Set
from pydantic import BaseModel


# --- Allowed event types ---

ALLOWED_EVENT_TYPES: Set[str] = {
    "TOOL_INPUT_EVENT",
    "TOOL_OUTPUT_EVENT",
    "TOOL_ERROR_EVENT",
    "AGENT_START_EVENT",
    "AGENT_COMPLETION_EVENT",
    "AGENT_ERROR_EVENT",
    "EXECUTION_FINAL_RESPONSE",
}

# --- Normalization map ---

EVENT_NORMALIZATION_MAP: Dict[str, str] = {
    "AGENT_START_EVENT": "agent_started",
    "TOOL_INPUT_EVENT": "tool_started",
    "TOOL_OUTPUT_EVENT": "tool_completed",
    "TOOL_ERROR_EVENT": "tool_failed",
    "AGENT_COMPLETION_EVENT": "agent_completed",
    "AGENT_ERROR_EVENT": "agent_failed",
    "EXECUTION_FINAL_RESPONSE": "agent_completed",
}

# --- Execution status transition map ---

EXECUTION_STATUS_MAP: Dict[str, str] = {
    "AGENT_START_EVENT": "running",
    "TOOL_INPUT_EVENT": "in_progress",
    "TOOL_OUTPUT_EVENT": "in_progress",
    "AGENT_COMPLETION_EVENT": "completed",
    "TOOL_ERROR_EVENT": "failed",
    "AGENT_ERROR_EVENT": "failed",
    "EXECUTION_FINAL_RESPONSE": "completed",
}

# --- Summary generation map ---

EVENT_SUMMARY_MAP: Dict[str, str] = {
    "AGENT_START_EVENT": "Agent execution started",
    "TOOL_INPUT_EVENT": "Tool invoked: {tool_name}",
    "TOOL_OUTPUT_EVENT": "Tool output received: {tool_name}",
    "TOOL_ERROR_EVENT": "Tool failed: {tool_name}",
    "AGENT_COMPLETION_EVENT": "Agent completed successfully",
    "AGENT_ERROR_EVENT": "Agent execution failed",
    "EXECUTION_FINAL_RESPONSE": "Final agent response received",
}


class RawKafkaEvent(BaseModel):
    """
    Flexible model for raw Kafka event payloads.
    Fields are optional because different event types carry different fields.
    """
    x_correlation_id: Optional[str] = None
    status: Optional[str] = None
    event_type: Optional[str] = None
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    invocation_id: Optional[str] = None
    function_call_id: Optional[str] = None
    response: Optional[Any] = None
    timestamp: Optional[str] = None


class NormalizedEvent(BaseModel):
    """Normalized representation of a Kafka event, ready for persistence and broadcast."""
    event_type: str
    normalized_event_type: str
    status: str
    agent_name: Optional[str] = None
    tool_name: Optional[str] = None
    timestamp: str
    summary: str
    payload: Optional[Dict[str, Any]] = None


class KafkaMessageMetadata(BaseModel):
    """Metadata from the Kafka message itself, stored for debugging/replay."""
    topic: Optional[str] = None
    partition: Optional[int] = None
    offset: Optional[int] = None
    consumed_at: Optional[str] = None
    raw_key: Optional[str] = None
