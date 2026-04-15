import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def test_client(mock_settings):
    """Create a TestClient with mocked dependencies."""
    # Patch lifespan to skip Mongo/Kafka/WS init
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


def test_execute_happy_path(test_client, mock_settings):
    """Test POST /api/v1/chat/execute returns accepted response."""
    with (
        patch(
            "app.services.chat_execution_service.token_client.get_token",
            new_callable=AsyncMock,
            return_value="test-token",
        ),
        patch(
            "app.services.chat_execution_service.backend_executor_client.execute",
            new_callable=AsyncMock,
            return_value={"message": "Execution initialized successfully"},
        ),
        patch(
            "app.services.chat_execution_service.SessionsRepository.create_or_update_session",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.chat_execution_service.ExecutionsRepository.create_execution",
            new_callable=AsyncMock,
        ),
    ):
        response = test_client.post(
            "/api/v1/chat/execute",
            json={
                "context": "Investigate case TEST_123",
                "soeid": "TEST001",
                "metadata": {"client": "web"},
            },
            headers={"X-SOEID": "TEST001"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["message"] == "Execution initialized successfully"
        assert body["data"]["status"] == "accepted"
        assert "correlation_id" in body["data"]
        assert "session_id" in body["data"]
        assert "websocket_url" in body["data"]
        assert body["data"]["websocket_url"].startswith("/ws/v1/chat/progress/")


def test_execute_auto_generates_session_id(test_client, mock_settings):
    """Test that session_id is auto-generated when not provided."""
    with (
        patch(
            "app.services.chat_execution_service.token_client.get_token",
            new_callable=AsyncMock,
            return_value="test-token",
        ),
        patch(
            "app.services.chat_execution_service.backend_executor_client.execute",
            new_callable=AsyncMock,
            return_value={"message": "OK"},
        ),
        patch(
            "app.services.chat_execution_service.SessionsRepository.create_or_update_session",
            new_callable=AsyncMock,
        ),
        patch(
            "app.services.chat_execution_service.ExecutionsRepository.create_execution",
            new_callable=AsyncMock,
        ),
    ):
        response = test_client.post(
            "/api/v1/chat/execute",
            json={
                "context": "Test query",
                "soeid": "TEST001",
                # no session_id provided
            },
            headers={"X-SOEID": "TEST001"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["session_id"] is not None
        assert len(body["data"]["session_id"]) == 36  # UUID v4 length


def test_execute_missing_context(test_client, mock_settings):
    """Test that missing 'context' field returns 422."""
    response = test_client.post(
        "/api/v1/chat/execute",
        json={"soeid": "TEST001"},
        headers={"X-SOEID": "TEST001"}
    )
    assert response.status_code == 422


def test_execute_missing_soeid(test_client, mock_settings):
    """Test that missing 'soeid' field returns 422."""
    response = test_client.post(
        "/api/v1/chat/execute",
        json={"context": "Test query"},
        headers={"X-SOEID": "TEST001"}
    )
    assert response.status_code == 422
