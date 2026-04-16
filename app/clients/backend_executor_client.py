from typing import Dict, Any

import httpx

from app.core.config import settings
from app.core.exceptions import BackendExecutorError
from app.core.logging import get_logger
from app.utils.retry import async_retry

logger = get_logger(__name__)


class BackendExecutorClient:
    """
    Client for calling the backend conversational task executor API.
    Sends POST with required headers, masked logging, timeout and retry.
    """

    @staticmethod
    @async_retry(
        attempts=settings.HTTP_RETRY_ATTEMPTS,
        backoff_seconds=settings.HTTP_RETRY_BACKOFF_SECONDS,
        exception_types=(BackendExecutorError, httpx.HTTPError),
    )
    async def execute(
        context: str,
        correlation_id: str,
        session_id: str,
        soeid: str,
        token: str,
    ) -> Dict[str, Any]:
        """
        POST to the backend executor with required headers and body.
        Returns the immediate acknowledgment from the backend.
        """
        url = f"{settings.BACKEND_EXECUTOR_BASE_URL}{settings.BACKEND_EXECUTOR_PATH}"

        headers = {
            "Config-ID": settings.BACKEND_CONFIG_ID,
            "X-Correlation-ID": correlation_id,
            "X-Application-ID": settings.BACKEND_APPLICATION_ID,
            "Session-ID": session_id,
            "X-SOEID": soeid,
            "X-Authorization-Coin": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        body = {"context": context}

        # Log with sensitive values masked
        masked_headers = {
            k: ("***" if k in ("X-Authorization-Coin", "Config-ID") else v)
            for k, v in headers.items()
        }
        logger.info(
            "Calling backend executor",
            url=url,
            headers=masked_headers,
            correlation_id=correlation_id,
            session_id=session_id,
        )

        async with httpx.AsyncClient(
            timeout=settings.BACKEND_REQUEST_TIMEOUT_SECONDS,
            verify=settings.BACKEND_VERIFY_SSL
        ) as client:
            try:
                resp = await client.post(url, json=body, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error(
                    "Backend executor HTTP error",
                    status_code=e.response.status_code,
                    correlation_id=correlation_id,
                )
                raise BackendExecutorError(
                    f"Backend executor failed: HTTP {e.response.status_code}"
                ) from e
            except httpx.RequestError as e:
                logger.error(
                    "Backend executor request error",
                    error=str(e),
                    correlation_id=correlation_id,
                )
                raise BackendExecutorError(f"Backend executor request failed: {e}") from e

        data = resp.json()
        logger.info(
            "Backend executor responded",
            correlation_id=correlation_id,
            response_keys=list(data.keys()) if isinstance(data, dict) else "non-dict",
        )
        return data


backend_executor_client = BackendExecutorClient()
