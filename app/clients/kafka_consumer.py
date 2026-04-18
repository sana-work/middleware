import asyncio
import json
import ssl
from typing import Optional

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

from app.core.config import settings
from app.core.logging import get_logger
from app.models.kafka_events import ALLOWED_EVENT_TYPES, RawKafkaEvent
from app.services.event_processing_service import EventProcessingService
from app.services.websocket_manager import WebSocketManager
from app.services.metrics_service import (
    KAFKA_MESSAGES_CONSUMED_TOTAL,
    KAFKA_COMMIT_SUCCESS_TOTAL,
    KAFKA_COMMIT_FAILURE_TOTAL
)
from app.utils.datetime_utils import utc_now, format_iso

logger = get_logger(__name__)


class KafkaEventConsumer:
    """
    Background Kafka consumer using aiokafka (Pure Python).
    
    Natively asynchronous, avoiding the need for background threads.
    """

    def __init__(self, ws_manager: WebSocketManager) -> None:
        self._ws_manager = ws_manager
        self._running = False
        self._consumer: Optional[AIOKafkaConsumer] = None

    def _get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Build SSL context if certificates are provided."""
        if not settings.KAFKA_SSL_CAFILE:
            return None
            
        context = ssl.create_default_context(cafile=settings.KAFKA_SSL_CAFILE)
        if settings.KAFKA_SSL_CERTFILE and settings.KAFKA_SSL_KEYFILE:
            context.load_cert_chain(
                certfile=settings.KAFKA_SSL_CERTFILE,
                keyfile=settings.KAFKA_SSL_KEYFILE,
                password=settings.KAFKA_SSL_PASSWORD
            )
        return context

    async def start(self) -> None:
        """Initialize and start the Kafka consumer."""
        ssl_context = self._get_ssl_context()
        
        self._consumer = AIOKafkaConsumer(
            settings.KAFKA_TOPIC,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id=settings.KAFKA_CONSUMER_GROUP,
            security_protocol=settings.KAFKA_SECURITY_PROTOCOL or "PLAINTEXT",
            ssl_context=ssl_context,
            auto_offset_reset="earliest",
            enable_auto_commit=False  # manual commit after persistence
        )

        try:
            await self._consumer.start()
            self._running = True
            logger.info("Kafka consumer started", topic=settings.KAFKA_TOPIC)
            asyncio.create_task(self._consume_loop())
        except Exception as e:
            logger.error("Failed to start Kafka consumer", error=str(e))

    async def _consume_loop(self) -> None:
        """Main async consumption loop."""
        try:
            async for msg in self._consumer:
                if not self._running:
                    break
                
                KAFKA_MESSAGES_CONSUMED_TOTAL.inc()
                await self._process_message(msg)
        except Exception as e:
            logger.error("Kafka consumption loop error", error=str(e))
        finally:
            await self.stop()

    async def _process_message(self, msg) -> None:
        """Process a single Kafka message."""
        raw_value = msg.value
        raw_key = msg.key
        topic = msg.topic
        partition = msg.partition
        offset = msg.offset

        try:
            payload = json.loads(raw_value.decode("utf-8"))
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    pass
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Malformed Kafka message, skipping", error=str(e), offset=offset)
            await self._safe_commit()
            return

        # Unwrap payload if nested
        if "data" in payload:
            inner_data = payload["data"]
            if isinstance(inner_data, str):
                try:
                    payload.update(json.loads(inner_data))
                except json.JSONDecodeError:
                    pass
            elif isinstance(inner_data, dict):
                payload.update(inner_data)

        event_type = payload.get("event_type")
        x_correlation_id = payload.get("x_correlation_id") or payload.get("correlation_id")
        
        # Infer event type if missing
        if not event_type:
            status_val = payload.get("status")
            if status_val == "SUCCESS":
                 event_type = "EXECUTION_FINAL_RESPONSE"
            elif status_val == "FAILED":
                 event_type = "AGENT_ERROR_EVENT"
            payload["event_type"] = event_type
        
        if event_type not in ALLOWED_EVENT_TYPES or not x_correlation_id:
            await self._safe_commit()
            return

        kafka_metadata = {
            "topic": topic,
            "partition": partition,
            "offset": offset,
            "consumed_at": format_iso(utc_now()),
            "raw_key": raw_key.decode("utf-8") if raw_key else None,
        }

        try:
            raw_event = RawKafkaEvent(**payload)
            processed = await EventProcessingService.process_kafka_event(
                raw_event=raw_event,
                correlation_id=x_correlation_id,
                kafka_metadata=kafka_metadata,
            )

            if processed:
                await self._ws_manager.broadcast(x_correlation_id, processed)

            await self._safe_commit()

        except Exception as e:
            logger.error("Failed to persist Kafka event", error=str(e), offset=offset)

    async def _safe_commit(self) -> None:
        """Manually commit the current offsets."""
        try:
            await self._consumer.commit()
            KAFKA_COMMIT_SUCCESS_TOTAL.inc()
        except Exception as e:
            KAFKA_COMMIT_FAILURE_TOTAL.inc()
            logger.error("Kafka commit failed", error=str(e))

    async def stop(self) -> None:
        """Stop the consumer."""
        self._running = False
        if self._consumer:
            await self._consumer.stop()
            logger.info("Kafka consumer stopped")

    def is_initialized(self) -> bool:
        return self._consumer is not None and self._running
