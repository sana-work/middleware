from fastapi import APIRouter

from app.core.config import settings
from app.core.logging import get_logger
from app.models.api_responses import HealthLiveResponse, HealthReadyResponse, HealthReadyChecks

logger = get_logger(__name__)

router = APIRouter(tags=["Health"])


@router.get("/health/live", response_model=HealthLiveResponse)
async def health_live() -> HealthLiveResponse:
    """Lightweight liveness probe."""
    return HealthLiveResponse(status="ok")


@router.get("/health/ready", response_model=HealthReadyResponse)
async def health_ready() -> HealthReadyResponse:
    """
    Readiness probe with concrete dependency checks:
      - MongoDB: ping succeeds
      - Kafka: consumer is initialized and subscribed
      - Token config: TOKEN_URL is configured
      - Backend config: BACKEND_EXECUTOR_BASE_URL, CONFIG_ID, APPLICATION_ID present
    """
    # MongoDB check
    mongo_status = "down"
    try:
        from app.db.mongo import get_database
        db = get_database()
        await db.command("ping")
        mongo_status = "up"
    except Exception as e:
        logger.warning("MongoDB readiness check failed", error=str(e))

    # Kafka check
    kafka_status = "down"
    try:
        from app.core.lifespan import get_kafka_consumer
        consumer = get_kafka_consumer()
        if consumer and consumer.is_initialized():
            kafka_status = "up"
    except Exception as e:
        logger.warning("Kafka readiness check failed", error=str(e))

    # Token config check
    token_status = "up" if settings.TOKEN_URL and settings.TOKEN_CLIENT_SECRET else "down"

    # Backend config check
    backend_status = (
        "up"
        if settings.BACKEND_EXECUTOR_BASE_URL
        and settings.BACKEND_CONFIG_ID
        and settings.BACKEND_APPLICATION_ID
        else "down"
    )

    all_up = all(
        s == "up" for s in [mongo_status, kafka_status, token_status, backend_status]
    )

    return HealthReadyResponse(
        status="ready" if all_up else "degraded",
        checks=HealthReadyChecks(
            mongodb=mongo_status,
            kafka=kafka_status,
            token_client_config=token_status,
            backend_client_config=backend_status,
        ),
    )
