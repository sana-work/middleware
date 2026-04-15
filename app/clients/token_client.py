import time
from typing import Optional

import httpx

from app.core.config import settings
from app.core.exceptions import TokenFetchError
from app.core.logging import get_logger
from app.utils.retry import async_retry

logger = get_logger(__name__)

# Token refresh buffer: refresh 60 seconds before actual expiry
_REFRESH_BUFFER_SECONDS = 60


class TokenClient:
    """
    In-memory cached token client.

    Fetches an auth token via POST with JSON body {"clientSecret": "..."}.
    Caches the token and automatically refreshes before expiry.
    """

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    async def get_token(self) -> str:
        """Return a valid token, fetching a new one if the cache has expired."""
        if self._token and time.time() < self._expires_at:
            return self._token

        logger.info("Token expired or missing, fetching new token")
        return await self._fetch_token()

    @async_retry(
        attempts=settings.HTTP_RETRY_ATTEMPTS,
        backoff_seconds=settings.HTTP_RETRY_BACKOFF_SECONDS,
        exception_types=(TokenFetchError, httpx.HTTPError),
    )
    async def _fetch_token(self) -> str:
        """POST to token endpoint to obtain a new access token."""
        async with httpx.AsyncClient(timeout=settings.TOKEN_TIMEOUT_SECONDS) as client:
            try:
                resp = await client.post(
                    settings.TOKEN_URL,
                    json={"clientSecret": settings.TOKEN_CLIENT_SECRET},
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error("Token fetch HTTP error", status_code=e.response.status_code)
                raise TokenFetchError(f"Token fetch failed: HTTP {e.response.status_code}") from e
            except httpx.RequestError as e:
                logger.error("Token fetch request error", error=str(e))
                raise TokenFetchError(f"Token fetch request failed: {e}") from e

        data = resp.json()
        access_token = data.get("access_token")
        if not access_token:
            raise TokenFetchError("Token response missing 'access_token' field")

        # Determine expiry: use expires_in from response, or fall back to TOKEN_TIMEOUT_SECONDS
        expires_in = data.get("expires_in", settings.TOKEN_TIMEOUT_SECONDS)
        self._token = access_token
        self._expires_at = time.time() + expires_in - _REFRESH_BUFFER_SECONDS

        logger.info("Token fetched successfully", expires_in=expires_in)
        return self._token


# Singleton instance
token_client = TokenClient()
