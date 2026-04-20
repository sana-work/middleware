import pytest
from app.services.pdf_export_service import PDFExportService
from app.models.export_models import ExportEventDTO

def test_consolidate_timeline_hierarchical():
    """Verify that events are correctly nested under agents."""
    events = [
        ExportEventDTO(
            event_type="AGENT_START_EVENT",
            normalized_event_type="agent_started",
            status="running",
            summary="Agent started: recon_ops_investigator_agent",
            timestamp="2024-05-01T10:00:00Z"
        ),
        ExportEventDTO(
            event_type="TOOL_INPUT_EVENT",
            normalized_event_type="tool_started",
            status="in_progress",
            summary="Tool invoked: get_case",
            tool_name="get_case",
            timestamp="2024-05-01T10:00:05Z"
        ),
        ExportEventDTO(
            event_type="TOOL_OUTPUT_EVENT",
            normalized_event_type="tool_completed",
            status="completed",
            summary="Tool output received",
            tool_name="get_case",
            timestamp="2024-05-01T10:00:10Z"
        ),
        ExportEventDTO(
            event_type="EXECUTION_FINAL_RESPONSE",
            normalized_event_type="agent_completed",
            status="completed",
            summary="Final response generated",
            timestamp="2024-05-01T10:01:00Z"
        )
    ]

    hierarchy = PDFExportService._consolidate_timeline(events)

    assert len(hierarchy) == 2  # Agent and Final Response
    
    agent_step = hierarchy[0]
    assert agent_step["type"] == "agent"
    assert "Reconciliation Operations Investigator" in agent_step["label"]
    assert len(agent_step["tools"]) == 1
    
    tool = agent_step["tools"][0]
    assert tool["name"] == "get_case"
    assert "Retrieving case details" in tool["label"]
    assert tool["start_ts"] == "2024-05-01T10:00:05Z"
    assert tool["end_ts"] == "2024-05-01T10:00:10Z"
    assert tool["status"] == "completed"
    assert "5s" in tool["display_label"]

def test_consolidate_timeline_multiple_tools():
    """Verify multiple tool calls under the same agent."""
    events = [
        ExportEventDTO(
            event_type="AGENT_START_EVENT",
            normalized_event_type="agent_started",
            status="running",
            summary="Agent started: recon_ops_investigator_agent",
            timestamp="10:00:00"
        ),
        ExportEventDTO(
            event_type="TOOL_INPUT_EVENT",
            normalized_event_type="tool_started",
            status="in_progress",
            summary="Tool invoked: get_case",
            tool_name="get_case",
            timestamp="10:00:05"
        ),
        ExportEventDTO(
            event_type="TOOL_OUTPUT_EVENT",
            normalized_event_type="tool_completed",
            status="completed",
            summary="Tool output received",
            tool_name="get_case",
            timestamp="10:00:10"
        ),
        ExportEventDTO(
            event_type="TOOL_INPUT_EVENT",
            normalized_event_type="tool_started",
            status="in_progress",
            summary="Tool invoked: get_nostro_id",
            tool_name="get_nostro_id",
            timestamp="10:00:15"
        ),
        ExportEventDTO(
            event_type="TOOL_OUTPUT_EVENT",
            normalized_event_type="tool_completed",
            status="completed",
            summary="Tool output received",
            tool_name="get_nostro_id",
            timestamp="10:00:20"
        )
    ]

    hierarchy = PDFExportService._consolidate_timeline(events)
    agent = hierarchy[0]
    assert len(agent["tools"]) == 2
    assert agent["tools"][0]["name"] == "get_case"
    assert agent["tools"][1]["name"] == "get_nostro_id"
