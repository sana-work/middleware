import pytest
from unittest.mock import AsyncMock, patch

from app.models.kafka_events import (
    ALLOWED_EVENT_TYPES,
    EVENT_NORMALIZATION_MAP,
    EXECUTION_STATUS_MAP,
    RawKafkaEvent,
)
from app.services.event_processing_service import EventProcessingService


# --- Normalization tests ---

def test_all_allowed_event_types_have_normalization():
    """Every allowed event type must have a normalization mapping."""
    for event_type in ALLOWED_EVENT_TYPES:
        assert event_type in EVENT_NORMALIZATION_MAP, f"Missing normalization for {event_type}"


def test_all_allowed_event_types_have_status_mapping():
    """Every allowed event type must have an execution status mapping."""
    for event_type in ALLOWED_EVENT_TYPES:
        assert event_type in EXECUTION_STATUS_MAP, f"Missing status mapping for {event_type}"


def test_normalization_values():
    """Verify the specific normalization values from the spec."""
    assert EVENT_NORMALIZATION_MAP["AGENT_START_EVENT"] == "agent_started"
    assert EVENT_NORMALIZATION_MAP["TOOL_INPUT_EVENT"] == "tool_started"
    assert EVENT_NORMALIZATION_MAP["TOOL_OUTPUT_EVENT"] == "tool_completed"
    assert EVENT_NORMALIZATION_MAP["TOOL_ERROR_EVENT"] == "tool_failed"
    assert EVENT_NORMALIZATION_MAP["AGENT_COMPLETION_EVENT"] == "agent_completed"
    assert EVENT_NORMALIZATION_MAP["AGENT_ERROR_EVENT"] == "agent_failed"
    assert EVENT_NORMALIZATION_MAP["EXECUTION_FINAL_RESPONSE"] == "agent_completed"


def test_execution_status_values():
    """Verify the specific execution status transitions from the spec."""
    assert EXECUTION_STATUS_MAP["AGENT_START_EVENT"] == "running"
    assert EXECUTION_STATUS_MAP["TOOL_INPUT_EVENT"] == "in_progress"
    assert EXECUTION_STATUS_MAP["TOOL_OUTPUT_EVENT"] == "in_progress"
    assert EXECUTION_STATUS_MAP["AGENT_COMPLETION_EVENT"] == "in_progress"
    assert EXECUTION_STATUS_MAP["TOOL_ERROR_EVENT"] == "failed"
    assert EXECUTION_STATUS_MAP["AGENT_ERROR_EVENT"] == "failed"
    assert EXECUTION_STATUS_MAP["EXECUTION_FINAL_RESPONSE"] == "completed"


def test_ignored_event_types():
    """Non-allowed event types should not be in the allowed set."""
    assert "TASK_START_EVENT" not in ALLOWED_EVENT_TYPES
    assert "SYSTEM_EVENT" not in ALLOWED_EVENT_TYPES
    assert "" not in ALLOWED_EVENT_TYPES


# --- Event processing tests ---

@pytest.mark.asyncio
async def test_process_agent_start_event(mock_settings):
    """Test that AGENT_START_EVENT is normalized and persisted correctly."""
    raw_event = RawKafkaEvent(
        x_correlation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        status="STARTED",
        event_type="AGENT_START_EVENT",
        agent_name="recon_ops_investigator_agent",
        timestamp="2026-04-15T10:15:29.447056",
    )

    mock_execution = {
        "correlation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "session_id": "11111111-2222-3333-4444-555555555555",
        "soeid": "TEST001",
        "status": "accepted",
    }

    with (
        patch.object(
            EventProcessingService, "__init__", lambda self: None
        ) if False else patch(
            "app.services.event_processing_service.ExecutionsRepository.get_execution",
            new_callable=AsyncMock,
            return_value=mock_execution,
        ),
        patch(
            "app.services.event_processing_service.EventsRepository.insert_event",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_insert,
        patch(
            "app.services.event_processing_service.ExecutionsRepository.update_execution_status",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        result = await EventProcessingService.process_kafka_event(
            raw_event=raw_event,
            correlation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )

        assert result is not None
        assert result["event_type"] == "AGENT_START_EVENT"
        assert result["normalized_event_type"] == "agent_started"
        assert result["status"] == "running"
        assert result["summary"] == "Agent started: recon_ops_investigator_agent"

        mock_insert.assert_called_once()
        mock_update.assert_called_once()


@pytest.mark.asyncio
async def test_process_duplicate_event_skipped(mock_settings):
    """Test that duplicate events (insert returns False) result in None return."""
    raw_event = RawKafkaEvent(
        x_correlation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        status="STARTED",
        event_type="AGENT_START_EVENT",
        agent_name="recon_ops_investigator_agent",
        timestamp="2026-04-15T10:15:29.447056",
    )

    mock_execution = {
        "correlation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "session_id": "11111111-2222-3333-4444-555555555555",
        "soeid": "TEST001",
        "status": "accepted",
    }

    with (
        patch(
            "app.services.event_processing_service.ExecutionsRepository.get_execution",
            new_callable=AsyncMock,
            return_value=mock_execution,
        ),
        patch(
            "app.services.event_processing_service.EventsRepository.insert_event",
            new_callable=AsyncMock,
            return_value=False,  # Duplicate
        ),
        patch(
            "app.services.event_processing_service.ExecutionsRepository.update_execution_status",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        result = await EventProcessingService.process_kafka_event(
            raw_event=raw_event,
            correlation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )

        assert result is None
        mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_process_tool_input_event(mock_settings):
    """Test TOOL_INPUT_EVENT normalization with tool_name in summary."""
    raw_event = RawKafkaEvent(
        x_correlation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        status="STARTED",
        event_type="TOOL_INPUT_EVENT",
        tool_name="find_payments_by_amount",
        agent_name="recon_ops_investigator_agent",
        timestamp="2026-04-15T10:24:47.633300",
        response={"maximum_amount": "1037.24", "currency": "AUD"},
    )

    mock_execution = {
        "correlation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "session_id": "11111111-2222-3333-4444-555555555555",
        "soeid": "TEST001",
        "status": "accepted",
    }

    with (
        patch(
            "app.services.event_processing_service.ExecutionsRepository.get_execution",
            new_callable=AsyncMock,
            return_value=mock_execution,
        ),
        patch(
            "app.services.event_processing_service.EventsRepository.insert_event",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.services.event_processing_service.ExecutionsRepository.update_execution_status",
            new_callable=AsyncMock,
        ),
    ):
        result = await EventProcessingService.process_kafka_event(
            raw_event=raw_event,
            correlation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )

        assert result is not None
        assert result["normalized_event_type"] == "tool_started"
        assert result["status"] == "in_progress"
        assert "find_payments_by_amount" in result["summary"]


@pytest.mark.asyncio
async def test_process_final_response_with_hidden_error(mock_settings):
    """Test that EXECUTION_FINAL_RESPONSE with 'MCP Error' triggers status=failed."""
    raw_event = RawKafkaEvent(
        x_correlation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        event_type="EXECUTION_FINAL_RESPONSE",
        response="MCP Error: occurred during debug_payment_transaction_by_id",
        timestamp="2026-04-15T10:30:00.000000",
    )

    mock_execution = {
        "correlation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "session_id": "11111111-2222-3333-4444-555555555555",
        "soeid": "TEST001",
        "status": "in_progress",
    }

    with (
        patch(
            "app.services.event_processing_service.ExecutionsRepository.get_execution",
            new_callable=AsyncMock,
            return_value=mock_execution,
        ),
        patch(
            "app.services.event_processing_service.EventsRepository.insert_event",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.services.event_processing_service.ExecutionsRepository.update_execution_status",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        result = await EventProcessingService.process_kafka_event(
            raw_event=raw_event,
            correlation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )

        assert result is not None
        assert result["status"] == "failed"
        assert result["normalized_event_type"] == "agent_failed"
        assert "Hidden agent error detected" in result["summary"]

        # Verify that update_execution_status was called with 'failed'
        mock_update.assert_called_once_with(
            correlation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            status="failed",
            latest_event_type="EXECUTION_FINAL_RESPONSE",
            completed_at="2026-04-15T10:30:00.000000",
        )


@pytest.mark.asyncio
async def test_process_implicit_success_event(mock_settings):
    """Test that an event with status='SUCCESS' but NO event_type is inferred as EXECUTION_FINAL_RESPONSE."""
    raw_event = RawKafkaEvent(
        x_correlation_id="aaaa-bbbb-cccc",
        status="SUCCESS",
        response="Operation finished successfully",
        timestamp="2026-04-15T12:00:00.000000",
    )

    mock_execution = {
        "correlation_id": "aaaa-bbbb-cccc",
        "session_id": "sess-999",
        "soeid": "TEST001",
        "status": "in_progress",
    }

    with (
        patch("app.services.event_processing_service.ExecutionsRepository.get_execution", new_callable=AsyncMock, return_value=mock_execution),
        patch("app.services.event_processing_service.EventsRepository.insert_event", new_callable=AsyncMock, return_value=True),
        patch("app.services.event_processing_service.ExecutionsRepository.update_execution_status", new_callable=AsyncMock) as mock_update,
    ):
        result = await EventProcessingService.process_kafka_event(
            raw_event=raw_event,
            correlation_id="aaaa-bbbb-cccc",
        )

        assert result is not None
        assert result["event_type"] == "EXECUTION_FINAL_RESPONSE"
        assert result["status"] == "completed"
        assert "Operation finished successfully" in str(result["payload"])
        
        mock_update.assert_called_once_with(
            correlation_id="aaaa-bbbb-cccc",
            status="completed",
            latest_event_type="EXECUTION_FINAL_RESPONSE",
            completed_at="2026-04-15T12:00:00.000000",
        )
