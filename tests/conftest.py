import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from typing import Dict, Any


@pytest.fixture
def mock_settings():
    """Provide mock settings for testing."""
    with patch("app.core.config.settings") as mock:
        mock.MONGODB_URI = "mongodb://localhost:27017"
        mock.MONGODB_DATABASE = "test_db"
        mock.KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
        mock.KAFKA_TOPIC = "test-topic"
        mock.KAFKA_CONSUMER_GROUP = "test-group"
        mock.KAFKA_SECURITY_PROTOCOL = "PLAINTEXT"
        mock.KAFKA_SSL_CAFILE = None
        mock.KAFKA_SSL_CERTFILE = None
        mock.KAFKA_SSL_KEYFILE = None
        mock.KAFKA_SSL_PASSWORD = None
        mock.TOKEN_URL = "https://test-token-service/token"
        mock.TOKEN_CLIENT_SECRET = "test-secret"
        mock.TOKEN_TIMEOUT_SECONDS = 30
        mock.BACKEND_EXECUTOR_BASE_URL = "https://test-backend"
        mock.BACKEND_EXECUTOR_PATH = "/api/v1/native-conversational-task-executor"
        mock.BACKEND_CONFIG_ID = "test-config-id"
        mock.BACKEND_APPLICATION_ID = "test-app-id"
        mock.BACKEND_REQUEST_TIMEOUT_SECONDS = 60
        mock.WEBSOCKET_HEARTBEAT_SECONDS = 15
        mock.WEBSOCKET_IDLE_TIMEOUT_SECONDS = 300
        mock.HTTP_RETRY_ATTEMPTS = 1
        mock.HTTP_RETRY_BACKOFF_SECONDS = 0
        mock.CORS_ALLOW_ORIGINS = "http://localhost:3000"
        mock.cors_origins_list = ["http://localhost:3000"]
        mock.APP_NAME = "test-middleware"
        mock.LOG_LEVEL = "DEBUG"
        yield mock


@pytest.fixture
def sample_execute_request() -> Dict[str, Any]:
    """Sample chat execute request body."""
    return {
        "context": "Investigate case TEST_123",
        "soeid": "TEST001",
        "session_id": None,
        "metadata": {"client": "web"},
    }


@pytest.fixture
def sample_execution_doc() -> Dict[str, Any]:
    """Sample execution document from MongoDB."""
    return {
        "correlation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "session_id": "11111111-2222-3333-4444-555555555555",
        "soeid": "TEST001",
        "request_context": "Investigate case TEST_123",
        "status": "accepted",
        "latest_event_type": None,
        "created_at": "2026-04-15T10:15:29.000000+00:00",
        "updated_at": "2026-04-15T10:15:29.000000+00:00",
        "completed_at": None,
        "backend_ack": {"message": "Execution initialized successfully"},
    }


@pytest.fixture
def sample_raw_kafka_event() -> Dict[str, Any]:
    """Sample raw Kafka AGENT_START_EVENT payload."""
    return {
        "x_correlation_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "status": "STARTED",
        "event_type": "AGENT_START_EVENT",
        "agent_name": "recon_ops_investigator_agent",
        "invocation_id": "e-08b3bfba-1464-4850-b9bd-926a143ae0ef",
        "timestamp": "2026-04-15T10:15:29.447056",
    }
