import asyncio
import json
import ssl
from typing import Optional, Any

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
            
            # Log assigned partitions to detect if we're missing any
            assigned = self._consumer.assignment()
            logger.info(
                "Kafka consumer started",
                topic=settings.KAFKA_TOPIC,
                assigned_partitions=[f"p{tp.partition}" for tp in assigned],
                partition_count=len(assigned),
            )
            
            asyncio.create_task(self._consume_loop())
        except Exception as e:
            logger.error("Failed to start Kafka consumer", error=str(e))

    async def _consume_loop(self) -> None:
        """Main async consumption loop."""
        logger.info("Starting Kafka consumption loop", topic=settings.KAFKA_TOPIC)
        try:
            async for msg in self._consumer:
                if not self._running:
                    logger.info("Consumer loop stopping: loop flag False")
                    break
                
                logger.info("Kafka message received", topic=msg.topic, partition=msg.partition, offset=msg.offset)
                KAFKA_MESSAGES_CONSUMED_TOTAL.inc()
                await self._process_message(msg)
        except Exception as e:
            logger.error("Kafka consumption loop error", error=str(e))
        finally:
            await self.stop()

    def _recursive_json_loads(self, data: Any) -> Any:
        """Recursively decode JSON strings until a non-JSON string or non-string is found."""
        if not isinstance(data, str):
            return data
            
        try:
            decoded = json.loads(data)
            # If we successfully decoded it and it's still a string, try again
            if isinstance(decoded, str) and decoded != data:
                return self._recursive_json_loads(decoded)
            return decoded
        except (json.JSONDecodeError, TypeError):
            return data

    async def _process_message(self, msg) -> None:
        """Process a single Kafka message."""
        raw_value = msg.value
        raw_key = msg.key
        topic = msg.topic
        partition = msg.partition
        offset = msg.offset

        try:
            # First pass: decode bytes to string then recursively decode JSON
            initial_str = raw_value.decode("utf-8")
            payload = self._recursive_json_loads(initial_str)
            
            if not isinstance(payload, dict):
                logger.error("Kafka message payload is not a dictionary after decoding", offset=offset, raw_type=type(payload).__name__)
                await self._safe_commit()
                return
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error("Malformed Kafka message, skipping", error=str(e), offset=offset)
            await self._safe_commit()
            return

        # Log all top-level keys so we can detect unexpected field names from the backend
        logger.info(
            "Raw payload keys received",
            offset=offset,
            keys=list(payload.keys()),
            event_type_raw=payload.get("event_type"),
            latest_event_type_raw=payload.get("latest_event_type"),
            status_raw=payload.get("status"),
        )

        # Unwrap payload if nested in 'data' or 'body'
        for wrapper in ["data", "body"]:
            if wrapper in payload:
                inner_data = payload[wrapper]
                # Recursively decode the nested data if it's a string
                if isinstance(inner_data, str):
                    inner_data = self._recursive_json_loads(inner_data)
                
                if isinstance(inner_data, dict):
                    logger.info("Unwrapping nested payload", wrapper=wrapper, inner_keys=list(inner_data.keys()), offset=offset)
                    payload.update(inner_data)

        # Extract type and correlation ID with fallbacks
        event_type = payload.get("event_type") or payload.get("latest_event_type")
        x_correlation_id = (
            payload.get("x_correlation_id") or 
            payload.get("correlation_id") or 
            (payload.get("idempotency_key", "").split(":")[0] if ":" in payload.get("idempotency_key", "") else None)
        )
        
        # Infer event type if missing or if it's a generic status update
        if not event_type:
            status_val = payload.get("status")
            if status_val == "SUCCESS" or status_val == "completed":
                 event_type = "EXECUTION_FINAL_RESPONSE"
            elif status_val == "FAILED" or status_val == "failed":
                 event_type = "AGENT_ERROR_EVENT"
            elif status_val == "running":
                 event_type = "AGENT_START_EVENT"
            
            if event_type:
                logger.debug("Inferred event type from status", status=status_val, event_type=event_type)
                payload["event_type"] = event_type
        
        logger.info(
            "Extracted event metadata", 
            event_type=event_type, 
            correlation_id=x_correlation_id,
            offset=offset
        )
        
        if not x_correlation_id:
            logger.warning("Missing correlation_id in payload, skipping", offset=offset, full_payload=payload)
            await self._safe_commit()
            return

        if event_type not in ALLOWED_EVENT_TYPES:
            logger.warning(
                "Event type not in allowed list, skipping", 
                event_type=event_type, 
                allowed=sorted(ALLOWED_EVENT_TYPES),
                offset=offset,
                full_payload=payload
            )
            await self._safe_commit()
            return

        kafka_metadata = {
            "topic": topic,
            "partition": partition,
            "offset": offset,
            "timestamp": msg.timestamp,
            "headers": {k: v.decode("utf-8") if v else None for k, v in msg.headers} if msg.headers else None,
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
