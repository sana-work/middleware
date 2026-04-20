import pytest
from app.utils.business_logic import get_business_description

def test_get_business_description_tool_mapped():
    """Test mapping for a known tool."""
    # Input event
    desc = get_business_description("TOOL_INPUT_EVENT", "get_case", None)
    assert desc == "Retrieving case details from the case management system"
    
    # Output event
    desc = get_business_description("TOOL_OUTPUT_EVENT", "get_case", None)
    assert desc == "Completed: Retrieving case details from the case management system"
    
    # Error event
    desc = get_business_description("TOOL_ERROR_EVENT", "get_case", None)
    assert desc == "Failed: Retrieving case details from the case management system"

def test_get_business_description_tool_fallback():
    """Test fallback for an unknown tool."""
    desc = get_business_description("TOOL_INPUT_EVENT", "unknown_tool", None)
    assert desc == "Executing: Unknown Tool"
    
    desc = get_business_description("TOOL_OUTPUT_EVENT", "unknown_tool", None)
    assert desc == "Completed: Unknown Tool"

def test_get_business_description_agent_mapped():
    """Test mapping for a known agent."""
    desc = get_business_description("AGENT_START_EVENT", None, "recon_ops_investigator_agent")
    assert desc == "Reconciliation Operations Investigator has begun analyzing the request"
    
    desc = get_business_description("AGENT_COMPLETION_EVENT", None, "recon_ops_investigator_agent")
    assert desc == "Reconciliation Operations Investigator has finished its analysis"

def test_get_business_description_agent_fallback():
    """Test fallback for an unknown agent."""
    desc = get_business_description("AGENT_START_EVENT", None, "unknown_agent")
    assert desc == "Unknown Agent has begun analyzing the request"

def test_get_business_description_final_response():
    """Test final response event."""
    desc = get_business_description("EXECUTION_FINAL_RESPONSE", None, None)
    assert desc == "Investigation complete"

def test_get_business_description_none():
    """Test with None event type."""
    assert get_business_description(None, None, None) is None
    assert get_business_description("UNKNOWN_EVENT", None, None) is None
