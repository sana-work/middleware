import asyncio
import json
from typing import Optional

from confluent_kafka import Consumer, KafkaError, KafkaException

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
from app.utils.audit_logger import audit_logger
from app.utils.datetime_utils import utc_now, format_iso

logger = get_logger(__name__)


class KafkaEventConsumer:
    """
    Background Kafka consumer using confluent-kafka.

    Blocking poll calls are isolated inside a background thread via run_in_executor
    to avoid blocking the asyncio event loop.

    Poison-message handling:
      - Malformed JSON -> log and skip
      - Unsupported event type -> ignore
      - Persistence failure -> do not commit offset, log for manual recovery
    """

    def __init__(self, ws_manager: WebSocketManager) -> None:
        self._ws_manager = ws_manager
        self._running = False
        self._consumer: Optional[Consumer] = None

    def _build_config(self) -> dict:
        """Build confluent-kafka consumer config from env settings."""
        config = {
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": settings.KAFKA_CONSUMER_GROUP,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,  # manual commit after persistence
        }

        if settings.KAFKA_SECURITY_PROTOCOL:
            config["security.protocol"] = settings.KAFKA_SECURITY_PROTOCOL
        if settings.KAFKA_SSL_CAFILE:
            config["ssl.ca.location"] = settings.KAFKA_SSL_CAFILE
        if settings.KAFKA_SSL_CERTFILE:
            config["ssl.certificate.location"] = settings.KAFKA_SSL_CERTFILE
        if settings.KAFKA_SSL_KEYFILE:
            config["ssl.key.location"] = settings.KAFKA_SSL_KEYFILE
        if settings.KAFKA_SSL_PASSWORD:
            config["ssl.key.password"] = settings.KAFKA_SSL_PASSWORD
        if settings.KAFKA_DEBUG:
            config["debug"] = settings.KAFKA_DEBUG

        return config

    async def start(self) -> None:
        """Start the Kafka consumer loop in a background thread."""
        self._running = True
        config = self._build_config()

        def on_assign(consumer, partitions):
            # Use manual formatting to avoid confluent-kafka __str__ bug on Windows
            partition_list = [f"{p.topic}[{p.partition}]" for p in partitions]
            logger.info("Kafka partitions assigned", partitions=partition_list)

        def on_revoke(consumer, partitions):
            partition_list = [f"{p.topic}[{p.partition}]" for p in partitions]
            logger.info("Kafka partitions revoked", partitions=partition_list)

        def error_cb(err):
            logger.error("Kafka global error", error=str(err))

        # Add global error callback to help diagnose connection/SSL issues
        config["error_cb"] = error_cb

        try:
            self._consumer = Consumer(config)
            self._consumer.subscribe(
                [settings.KAFKA_TOPIC],
                on_assign=on_assign,
                on_revoke=on_revoke
            )
            logger.info("Kafka consumer subscribed", topic=settings.KAFKA_TOPIC)
        except KafkaException as e:
            logger.error("Failed to initialize Kafka consumer", error=str(e))
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._poll_loop, loop)

    def _poll_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Blocking poll loop."""
        logger.info("Kafka poll loop started")

        while self._running:
            msg = self._consumer.poll(timeout=1.0)
            if msg is None:
                continue
            
            KAFKA_MESSAGES_CONSUMED_TOTAL.inc()

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Kafka consumer error", error=msg.error().str())
                continue

            # Schedule the async processing on the event loop
            asyncio.run_coroutine_threadsafe(
                self._process_message(msg), loop
            )

        logger.info("Kafka poll loop exited")

    async def _process_message(self, msg) -> None:
        """Process a single Kafka message: parse, filter, normalize, persist, broadcast, commit."""
        raw_value = msg.value()
        raw_key = msg.key()
        topic = msg.topic()
        partition = msg.partition()
        offset = msg.offset()

        # 1. Parse JSON safely
        try:
            payload = json.loads(raw_value.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(
                "Malformed Kafka message, skipping",
                error=str(e),
                topic=topic,
                partition=partition,
                offset=offset,
            )
            self._safe_commit(msg)
            return

        # 2. Filter by allowed event types
        event_type = payload.get("event_type")
        if event_type not in ALLOWED_EVENT_TYPES:
            logger.debug("Ignored event type", event_type=event_type, offset=offset)
            self._safe_commit(msg)
            return

        # 3. Extract correlation_id
        x_correlation_id = payload.get("x_correlation_id")
        if not x_correlation_id:
            logger.warning(
                "Kafka event missing x_correlation_id, skipping",
                event_type=event_type,
                offset=offset,
            )
            self._safe_commit(msg)
            return

        # 4. Build kafka metadata
        kafka_metadata = {
            "topic": topic,
            "partition": partition,
            "offset": offset,
            "consumed_at": format_iso(utc_now()),
            "raw_key": raw_key.decode("utf-8") if raw_key else None,
        }

        # 5. Normalize, persist, update execution status
        try:
            raw_event = RawKafkaEvent(**payload)
            processed = await EventProcessingService.process_kafka_event(
                raw_event=raw_event,
                correlation_id=x_correlation_id,
                kafka_metadata=kafka_metadata,
            )

            if processed is None:
                # Duplicate event — already persisted, commit offset
                self._safe_commit(msg)
                return

        except Exception as e:
            # Persistence failure — do NOT commit, log for manual recovery
            logger.error(
                "Failed to persist Kafka event, offset NOT committed",
                error=str(e),
                correlation_id=x_correlation_id,
                event_type=event_type,
                topic=topic,
                partition=partition,
                offset=offset,
                raw_payload=payload,
            )
            return

        # 6. Broadcast to WebSocket subscribers (failure here must NOT block Kafka)
        try:
            await self._ws_manager.broadcast(x_correlation_id, processed)
        except Exception as e:
            logger.warning(
                "WebSocket broadcast failed, continuing Kafka processing",
                error=str(e),
                correlation_id=x_correlation_id,
            )

        # 7. Commit offset only after successful persistence
        self._safe_commit(msg)

    def _safe_commit(self, msg) -> None:
        """Commit offset after persistence."""
        try:
            self._consumer.commit(message=msg, asynchronous=False)
            KAFKA_COMMIT_SUCCESS_TOTAL.inc()
        except Exception as e:
            KAFKA_COMMIT_FAILURE_TOTAL.inc()
            logger.error("Kafka commit failed", error=str(e))

    async def stop(self) -> None:
        """Signal the poll loop to stop and close the consumer."""
        self._running = False
        if self._consumer:
            self._consumer.close()
            logger.info("Kafka consumer closed")

    def is_initialized(self) -> bool:
        """Check if the consumer has been created and is subscribed."""
        return self._consumer is not None and self._running
