import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import ssl

from app.clients.kafka_consumer import KafkaEventConsumer
from app.models.kafka_events import RawKafkaEvent

@pytest.fixture
def mock_ws_manager():
    return MagicMock()

@pytest.fixture
def consumer(mock_ws_manager, mock_settings):
    # Patch the settings inside the client module specifically
    with patch("app.clients.kafka_consumer.settings", mock_settings):
        yield KafkaEventConsumer(mock_ws_manager)

@pytest.mark.asyncio
async def test_consumer_initialization(consumer, mock_settings):
    """Verify consumer initializes with correct settings."""
    # We must patch AIOKafkaConsumer and make it return an AsyncMock
    with patch("app.clients.kafka_consumer.AIOKafkaConsumer") as MockAIOClass:
        mock_consumer_instance = AsyncMock()
        MockAIOClass.return_value = mock_consumer_instance
        
        await consumer.start()
        
        MockAIOClass.assert_called_once_with(
            mock_settings.KAFKA_TOPIC,
            bootstrap_servers=mock_settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=mock_settings.KAFKA_CONSUMER_GROUP,
            security_protocol=mock_settings.KAFKA_SECURITY_PROTOCOL,
            ssl_context=None,
            auto_offset_reset="earliest",
            enable_auto_commit=False
        )
        # Verify it was actually started
        mock_consumer_instance.start.assert_called_once()
        assert consumer.is_initialized()

@pytest.mark.asyncio
async def test_ssl_context_creation(consumer, mock_settings):
    """Verify SSL context is created when certificates are present."""
    with patch("app.clients.kafka_consumer.settings") as mock_set:
        mock_set.KAFKA_SSL_CAFILE = "/path/to/ca.pem"
        mock_set.KAFKA_SSL_CERTFILE = "/path/to/cert.pem"
        mock_set.KAFKA_SSL_KEYFILE = "/path/to/key.pem"
        mock_set.KAFKA_SSL_PASSWORD = "secret-password"
        
        with patch("ssl.create_default_context") as mock_create_ssl:
            mock_context = MagicMock()
            mock_create_ssl.return_value = mock_context
            
            context = consumer._get_ssl_context()
            
            assert context == mock_context
            mock_create_ssl.assert_called_once_with(cafile="/path/to/ca.pem")
            mock_context.load_cert_chain.assert_called_once_with(
                certfile="/path/to/cert.pem",
                keyfile="/path/to/key.pem",
                password="secret-password"
            )

@pytest.mark.asyncio
async def test_process_message_success_flow(consumer, mock_ws_manager):
    """Thoroughly test the flow: Parse -> Process -> Broadcast -> Commit."""
    
    # 1. Setup mock message
    payload = {
        "x_correlation_id": "test-id",
        "event_type": "AGENT_START_EVENT",
        "agent_name": "test-agent"
    }
    mock_msg = MagicMock()
    mock_msg.value = json.dumps(payload).encode("utf-8")
    mock_msg.key = b"test-key"
    mock_msg.topic = "test-topic"
    mock_msg.partition = 0
    mock_msg.offset = 123

    # 2. Mock external services
    processed_data = {"status": "running", "event_type": "AGENT_START_EVENT"}
    
    # Mock consumer instance (AIOKafkaConsumer.commit is async)
    mock_consumer_inst = AsyncMock()
    consumer._consumer = mock_consumer_inst
    consumer._running = True

    with (
        patch("app.services.event_processing_service.EventProcessingService.process_kafka_event", 
              new_callable=AsyncMock, return_value=processed_data) as mock_process,
        patch.object(mock_ws_manager, "broadcast", new_callable=AsyncMock) as mock_broadcast
    ):
        # 3. Execute
        await consumer._process_message(mock_msg)

        # 4. Verify the chain
        mock_process.assert_called_once()
        mock_broadcast.assert_called_once_with("test-id", processed_data)
        mock_consumer_inst.commit.assert_called_once()

@pytest.mark.asyncio
async def test_process_message_persistence_failure_no_commit(consumer):
    """CRITICAL: If database persistence fails, we must NOT commit the offset."""
    payload = {"x_correlation_id": "id", "event_type": "AGENT_START_EVENT"}
    mock_msg = MagicMock()
    mock_msg.value = json.dumps(payload).encode("utf-8")
    
    mock_consumer_inst = AsyncMock()
    consumer._consumer = mock_consumer_inst

    with patch("app.services.event_processing_service.EventProcessingService.process_kafka_event", 
               side_effect=Exception("DB Down")):
        await consumer._process_message(mock_msg)
        
        # Verify commit was NEVER called
        mock_consumer_inst.commit.assert_not_called()
