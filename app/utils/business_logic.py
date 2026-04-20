from typing import Optional
from app.models.kafka_events import TOOL_BUSINESS_CONTEXT_MAP, AGENT_BUSINESS_CONTEXT_MAP

def get_business_description(
    event_type: Optional[str], tool_name: Optional[str], agent_name: Optional[str]
) -> Optional[str]:
    """Generate a business-friendly description for a given event."""
    if not event_type:
        return None

    # Tool events: use the business context map or generate from tool name
    if event_type in ("TOOL_INPUT_EVENT", "TOOL_OUTPUT_EVENT", "TOOL_ERROR_EVENT") and tool_name:
        mapped = TOOL_BUSINESS_CONTEXT_MAP.get(tool_name)
        if mapped:
            if event_type == "TOOL_OUTPUT_EVENT":
                return f"Completed: {mapped}"
            elif event_type == "TOOL_ERROR_EVENT":
                return f"Failed: {mapped}"
            return mapped
        
        # Fallback: generate readable name from tool_name
        readable = tool_name.replace("_", " ").title()
        if event_type == "TOOL_OUTPUT_EVENT":
            return f"Completed: {readable}"
        elif event_type == "TOOL_ERROR_EVENT":
            return f"Failed: {readable}"
        return f"Executing: {readable}"

    # Agent events
    if event_type in ("AGENT_START_EVENT", "AGENT_COMPLETION_EVENT", "AGENT_ERROR_EVENT") and agent_name:
        friendly = AGENT_BUSINESS_CONTEXT_MAP.get(agent_name, agent_name.replace("_", " ").title())
        if event_type == "AGENT_START_EVENT":
            return f"{friendly} has begun analyzing the request"
        elif event_type == "AGENT_COMPLETION_EVENT":
            return f"{friendly} has finished its analysis"
        elif event_type == "AGENT_ERROR_EVENT":
            return f"{friendly} encountered an issue during analysis"

    if event_type == "EXECUTION_FINAL_RESPONSE":
        return "Investigation complete"

    return None
