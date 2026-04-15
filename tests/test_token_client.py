import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from app.clients.token_client import TokenClient


@pytest.fixture
def token_client_instance():
    """Create a fresh TokenClient instance for each test."""
    return TokenClient()


@pytest.mark.asyncio
async def test_token_fetch_success(token_client_instance, mock_settings):
    """Test that a token is fetched successfully from the token endpoint."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "test-token-value",
        "expires_in": 3600,
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.clients.token_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        token = await token_client_instance._fetch_token()
        assert token == "test-token-value"


@pytest.mark.asyncio
async def test_token_caching(token_client_instance, mock_settings):
    """Test that the token is cached and not re-fetched within expiry."""
    token_client_instance._token = "cached-token"
    token_client_instance._expires_at = time.time() + 3600  # valid for 1 hour

    token = await token_client_instance.get_token()
    assert token == "cached-token"


@pytest.mark.asyncio
async def test_token_refresh_after_expiry(token_client_instance, mock_settings):
    """Test that an expired token triggers a fresh fetch."""
    token_client_instance._token = "old-token"
    token_client_instance._expires_at = time.time() - 10  # already expired

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "access_token": "new-token-value",
        "expires_in": 3600,
    }
    mock_response.raise_for_status = MagicMock()

    with patch("app.clients.token_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        token = await token_client_instance.get_token()
        assert token == "new-token-value"
