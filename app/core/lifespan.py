from __future__ import annotations
from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI

from app.core.logging import setup_logging, get_logger
from app.core.throttler import throttler_cleanup_loop
from app.db.mongo import init_mongo, close_mongo
from app.clients.kafka_consumer import KafkaEventConsumer
from app.services.websocket_manager import WebSocketManager

logger = get_logger(__name__)

# Global references for app-wide access
kafka_consumer: KafkaEventConsumer | None = None
ws_manager: WebSocketManager | None = None


def get_ws_manager() -> WebSocketManager:
    """Return the global WebSocket manager instance."""
    assert ws_manager is not None, "WebSocketManager not initialized"
    return ws_manager


def get_kafka_consumer() -> KafkaEventConsumer | None:
    """Return the global Kafka consumer instance (may be None during tests)."""
    return kafka_consumer


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.
    Handles startup (MongoDB, Kafka, WebSocket manager) and shutdown.
    """
    global kafka_consumer, ws_manager

    setup_logging()
    logger.info("Application starting up")

    # --- Startup ---
    await init_mongo()
    logger.info("MongoDB initialized")

    ws_manager = WebSocketManager()
    logger.info("WebSocket manager initialized")

    kafka_consumer = KafkaEventConsumer(ws_manager=ws_manager)
    consumer_task = asyncio.create_task(kafka_consumer.start())
    logger.info("Kafka consumer background task started")

    # Start throttler cleanup
    cleanup_task = asyncio.create_task(throttler_cleanup_loop())

    yield

    # --- Shutdown ---
    logger.info("Application shutting down")

    # Stop cleanup
    cleanup_task.cancel()

    if kafka_consumer:
        await kafka_consumer.stop()
        logger.info("Kafka consumer stopped")

    if ws_manager:
        await ws_manager.close_all()
        logger.info("WebSocket manager closed all connections")

    await close_mongo()
    logger.info("MongoDB connection closed")
