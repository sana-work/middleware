import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.websocket_manager import WebSocketManager


@pytest.fixture
def ws_manager():
    """Create a fresh WebSocketManager for each test."""
    return WebSocketManager()


def _make_mock_ws():
    """Create a mock WebSocket."""
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_connect_registers_client(ws_manager):
    """Test that connecting a WebSocket registers it in the manager."""
    ws = _make_mock_ws()
    await ws_manager.connect("corr-1", ws)
    assert "corr-1" in ws_manager._connections
    assert ws in ws_manager._connections["corr-1"]


@pytest.mark.asyncio
async def test_disconnect_removes_client(ws_manager):
    """Test that disconnecting removes the WebSocket from the manager."""
    ws = _make_mock_ws()
    await ws_manager.connect("corr-1", ws)
    await ws_manager.disconnect("corr-1", ws)
    assert "corr-1" not in ws_manager._connections


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_clients(ws_manager):
    """Test that broadcast sends messages to all connected clients."""
    ws1 = _make_mock_ws()
    ws2 = _make_mock_ws()

    await ws_manager.connect("corr-1", ws1)
    await ws_manager.connect("corr-1", ws2)

    event_data = {
        "session_id": "sess-1",
        "event_type": "TOOL_INPUT_EVENT",
        "normalized_event_type": "tool_started",
        "status": "in_progress",
        "agent_name": "test_agent",
        "tool_name": "test_tool",
        "timestamp": "2026-04-15T10:00:00Z",
        "summary": "Test event",
        "payload": None,
    }

    await ws_manager.broadcast("corr-1", event_data)

    # Both clients should have received messages
    assert ws1.send_json.call_count >= 2  # event + status
    assert ws2.send_json.call_count >= 2


@pytest.mark.asyncio
async def test_broadcast_no_connections(ws_manager):
    """Test that broadcast is a no-op when no clients are connected."""
    # Should not raise
    await ws_manager.broadcast("nonexistent", {"event_type": "TEST"})


@pytest.mark.asyncio
async def test_terminal_completed_closes_connections(ws_manager):
    """Test that a completed terminal message closes all connections."""
    ws = _make_mock_ws()
    await ws_manager.connect("corr-1", ws)

    await ws_manager.send_terminal_and_close(
        correlation_id="corr-1",
        status="completed",
        event_type="AGENT_COMPLETION_EVENT",
        timestamp="2026-04-15T10:28:00Z",
    )

    ws.close.assert_called_once()
    assert "corr-1" not in ws_manager._connections


@pytest.mark.asyncio
async def test_terminal_failed_closes_connections(ws_manager):
    """Test that a failed terminal message closes all connections."""
    ws = _make_mock_ws()
    await ws_manager.connect("corr-1", ws)

    await ws_manager.send_terminal_and_close(
        correlation_id="corr-1",
        status="failed",
        event_type="AGENT_ERROR_EVENT",
        timestamp="2026-04-15T10:28:00Z",
        error_message="Agent execution failed",
    )

    ws.close.assert_called_once()
    assert "corr-1" not in ws_manager._connections


@pytest.mark.asyncio
async def test_broadcast_failed_client_does_not_crash(ws_manager):
    """Test that a single client failure doesn't crash the broadcast."""
    ws_good = _make_mock_ws()
    ws_bad = _make_mock_ws()
    ws_bad.send_json = AsyncMock(side_effect=Exception("connection lost"))

    await ws_manager.connect("corr-1", ws_good)
    await ws_manager.connect("corr-1", ws_bad)

    event_data = {
        "session_id": "sess-1",
        "event_type": "TOOL_INPUT_EVENT",
        "normalized_event_type": "tool_started",
        "status": "in_progress",
        "timestamp": "2026-04-15T10:00:00Z",
        "summary": "Test",
    }

    # Should not raise
    await ws_manager.broadcast("corr-1", event_data)

    # Good client should still receive
    assert ws_good.send_json.call_count >= 1
