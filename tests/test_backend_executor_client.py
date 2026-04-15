from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.clients.backend_executor_client import BackendExecutorClient
from app.core.exceptions import BackendExecutorError


@pytest.mark.asyncio
async def test_backend_execute_success(mock_settings):
    """Test that the backend executor makes a POST call and returns the response."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": "Execution initialized successfully"}
    mock_response.raise_for_status = MagicMock()
    with patch("app.clients.backend_executor_client.httpx.AsyncClient") as mock_client_cls:
        with patch("app.clients.backend_executor_client.settings", mock_settings):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await BackendExecutorClient.execute(
                context="Investigate case TEST_123",
                correlation_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                session_id="11111111-2222-3333-4444-555555555555",
                soeid="TEST001",
                token="test-token",
            )

            assert result == {"message": "Execution initialized successfully"}

        # Verify POST was called
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")

        assert headers["X-Correlation-ID"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert headers["Session-ID"] == "11111111-2222-3333-4444-555555555555"
        assert headers["X-SOEID"] == "TEST001"
        assert headers["X-Authorization-Coin"] == "Bearer test-token"
        assert headers["Content-Type"] == "application/json"
        assert headers["Config-ID"] == mock_settings.BACKEND_CONFIG_ID
        assert headers["X-Application-ID"] == mock_settings.BACKEND_APPLICATION_ID


@pytest.mark.asyncio
async def test_backend_execute_timeout(mock_settings):
    with patch("app.clients.backend_executor_client.httpx.AsyncClient") as mock_client_cls:
        import httpx

    with patch("app.clients.backend_executor_client.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(BackendExecutorError):
            await BackendExecutorClient.execute(
                context="Test",
                correlation_id="test-corr-id",
                session_id="test-session-id",
                soeid="TEST001",
                token="test-token",
            )
