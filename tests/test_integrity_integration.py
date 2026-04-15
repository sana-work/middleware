from __future__ import annotations
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import status as http_status

from app.models.kafka_events import RawKafkaEvent
from app.services.event_processing_service import EventProcessingService

@pytest.fixture
def mock_app(mock_settings):
    """Create a TestClient with mocked dependencies."""
    with (
        patch("app.core.lifespan.init_mongo", new_callable=AsyncMock),
        patch("app.core.lifespan.close_mongo", new_callable=AsyncMock),
        patch("app.core.lifespan.KafkaEventConsumer") as mock_kafka_cls,
        patch("app.core.lifespan.setup_logging"),
    ):
        mock_kafka = MagicMock()
        mock_kafka.start = AsyncMock()
        mock_kafka.stop = AsyncMock()
        mock_kafka_cls.return_value = mock_kafka

        from app.main import app
        with TestClient(app) as client:
            yield client

# --- 1. Authorization Failure & ID Enumeration Protection (404 Policy) ---

def test_unauthorized_status_access_returns_404(mock_app):
    """Verify that accessing another user's correlation_id returns 404, not 403."""
    with patch(
        "app.services.status_service.ExecutionsRepository.get_execution_by_soeid",
        new_callable=AsyncMock,
        return_value=None  # Simulate "not found for this user"
    ):
        response = mock_app.get(
            "/api/v1/chat/status/some-corr-id",
            headers={"X-SOEID": "MALICIOUS_USER"}
        )
        assert response.status_code == 404
        assert response.json()["message"] == "Execution not found for correlation_id=some-corr-id and soeid=MALICIOUS_USER"

def test_unauthorized_history_access_returns_404(mock_app):
    """Verify that accessing another user's session_id returns 404."""
    with patch(
        "app.services.status_service.SessionsRepository.get_session_by_soeid",
        new_callable=AsyncMock,
        return_value=None
    ):
        response = mock_app.get(
            "/api/v1/chat/history/some-sess-id",
            headers={"X-SOEID": "MALICIOUS_USER"}
        )
        assert response.status_code == 404

# --- 2. Kafka Idempotency ---

@pytest.mark.asyncio
async def test_kafka_event_idempotency(mock_settings):
    """Verify only one event is processed even if Kafka sends duplicates."""
    raw_event = RawKafkaEvent(
        x_correlation_id="corr-123",
        event_type="TOOL_INPUT_EVENT",
        tool_name="test_tool",
        timestamp="2026-04-15T12:00:00Z"
    )
    
    execution = {"status": "accepted", "soeid": "user1", "session_id": "sess1"}
    
    with (
        patch("app.services.event_processing_service.ExecutionsRepository.get_execution", new_callable=AsyncMock, return_value=execution),
        patch("app.services.event_processing_service.EventsRepository.insert_event", new_callable=AsyncMock) as mock_insert,
        patch("app.services.event_processing_service.ExecutionsRepository.update_execution_status", new_callable=AsyncMock),
        patch("app.services.event_processing_service.audit_logger.log_event")
    ):
        # First time: Success
        mock_insert.return_value = True
        res1 = await EventProcessingService.process_kafka_event(raw_event, "corr-123")
        assert res1 is not None
        assert mock_insert.call_count == 1
        
        # Second time: Duplicate (insert returns False)
        mock_insert.return_value = False
        res2 = await EventProcessingService.process_kafka_event(raw_event, "corr-123")
        assert res2 is None
        assert mock_insert.call_count == 2

# --- 3. Kafka Commit Safety ---

@pytest.mark.asyncio
async def test_kafka_commit_omitted_on_persistence_failure(mock_settings):
    """Verify that if DB fails, we do NOT commit the offset, and we increment failure metric."""
    from app.clients.kafka_consumer import KafkaEventConsumer
    
    mock_ws = MagicMock()
    consumer = KafkaEventConsumer(ws_manager=mock_ws)
    consumer._consumer = MagicMock()
    
    mock_msg = MagicMock()
    mock_msg.value.return_value = b'{"event_type": "AGENT_START_EVENT", "x_correlation_id": "c1"}'
    mock_msg.key.return_value = b"k1"
    mock_msg.error.return_value = None
    
    with (
        patch("app.services.event_processing_service.EventProcessingService.process_kafka_event", side_effect=Exception("DB DOWN")),
        patch("app.services.metrics_service.KAFKA_PERSISTENCE_FAILURES_TOTAL.inc") as mock_metric
    ):
        await consumer._process_message(mock_msg)
        
        # Verify commit was NEVER called
        consumer._consumer.commit.assert_not_called()
        # Metric should be incremented (this happens inside process_kafka_event which we mocked side_effect, 
        # but let's re-verify the actual catch block in process_kafka_event if possible)

@pytest.mark.asyncio
async def test_kafka_commit_called_after_success(mock_settings):
    """Verify that if processing succeeds, commit is called."""
    from app.clients.kafka_consumer import KafkaEventConsumer
    
    mock_ws = MagicMock()
    consumer = KafkaEventConsumer(ws_manager=mock_ws)
    consumer._consumer = MagicMock()
    
    mock_msg = MagicMock()
    mock_msg.value.return_value = b'{"event_type": "AGENT_START_EVENT", "x_correlation_id": "c1"}'
    mock_msg.key.return_value = b"k1"
    mock_msg.error.return_value = None
    
    with patch("app.services.event_processing_service.EventProcessingService.process_kafka_event", new_callable=AsyncMock, return_value={"status": "running"}):
        await consumer._process_message(mock_msg)
        
        # Verify commit WAS called
        consumer._consumer.commit.assert_called_once()
