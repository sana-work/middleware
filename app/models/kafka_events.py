from typing import Optional, Dict, Any, Set
from pydantic import BaseModel, ConfigDict


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

# --- Error detection patterns ---
# Substrings that, if found in a final response, indicate the run should be flagged as 'failed'
TERMINAL_ERROR_PATTERNS: Set[str] = {
    "MCP Error:",
    "Internal error: Error calling tool",
    "Agent failed to provide a valid response",
    "Backend error: ",
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
    "AGENT_COMPLETION_EVENT": "in_progress",
    "TOOL_ERROR_EVENT": "failed",
    "AGENT_ERROR_EVENT": "failed",
    "EXECUTION_FINAL_RESPONSE": "completed",
}

# --- Summary generation map ---

EVENT_SUMMARY_MAP: Dict[str, str] = {
    "AGENT_START_EVENT": "Agent started: {agent_name}",
    "TOOL_INPUT_EVENT": "Tool invoked: {tool_name}",
    "TOOL_OUTPUT_EVENT": "Tool output received: {tool_name}",
    "TOOL_ERROR_EVENT": "Tool failed: {tool_name}",
    "AGENT_COMPLETION_EVENT": "Agent completed: {agent_name}",
    "AGENT_ERROR_EVENT": "Agent failed: {agent_name}",
    "EXECUTION_FINAL_RESPONSE": "Final response generated",
}

# --- Business-friendly tool descriptions ---
# Maps technical tool names to human-readable descriptions for reports.
# Tools not listed here will fall back to a generated description from the tool name.

TOOL_BUSINESS_CONTEXT_MAP: Dict[str, str] = {
    "get_case": "Retrieving case details from the case management system",
    "get_nostro_id": "Looking up nostro account identifiers for reconciliation",
    "find_break_details": "Investigating reconciliation break details and root causes",
    "find_payments_by_amount": "Searching payment transactions matching the specified amount",
    "get_payment_details": "Fetching detailed payment transaction information",
    "search_cases": "Searching case management system for related cases",
    "get_account_details": "Retrieving account information and balances",
    "get_transaction_history": "Pulling transaction history for the specified account",
    "match_payments": "Cross-referencing payments across systems for matching",
    "get_break_summary": "Generating a summary of outstanding reconciliation breaks",
}

# --- Business-friendly agent descriptions ---

AGENT_BUSINESS_CONTEXT_MAP: Dict[str, str] = {
    "recon_ops_investigator_agent": "Reconciliation Operations Investigator",
    "payment_investigator_agent": "Payment Investigation Specialist",
    "break_resolution_agent": "Break Resolution Analyst",
}



class RawKafkaEvent(BaseModel):
    """
    Flexible model for raw Kafka event payloads.
    Fields are optional because different event types carry different fields.
    """
    model_config = ConfigDict(extra="allow")

    x_correlation_id: Optional[str] = None
    correlation_id: Optional[str] = None
    status: Optional[str] = None
    event_type: Optional[str] = None
    latest_event_type: Optional[str] = None
    event: Optional[str] = None
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
    business_description: Optional[str] = None
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
