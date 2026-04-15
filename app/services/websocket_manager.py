import asyncio
from typing import Dict, Set, Any, Optional

from fastapi import WebSocket

from app.core.config import settings
from app.core.logging import get_logger
from app.models.websocket_messages import (
    WSEventMessage,
    WSStatusMessage,
    WSCompletedMessage,
    WSFailedMessage,
    WSHeartbeatMessage,
)
from app.utils.datetime_utils import utc_now, format_iso

from app.services.metrics_service import ACTIVE_WS_CONNECTIONS, WS_CONNECTIONS_TOTAL

logger = get_logger(__name__)

class WebSocketManager:
    """
    In-memory WebSocket connection manager keyed by correlation_id.
    """

    def __init__(self) -> None:
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._heartbeat_tasks: Dict[str, asyncio.Task] = {}

    async def connect(self, correlation_id: str, websocket: WebSocket) -> None:
        """Register a WebSocket connection."""
        if correlation_id not in self._connections:
            self._connections[correlation_id] = set()
        self._connections[correlation_id].add(websocket)

        # Metrics
        ACTIVE_WS_CONNECTIONS.inc()
        WS_CONNECTIONS_TOTAL.inc()

        # Start heartbeat if not already running
        if correlation_id not in self._heartbeat_tasks:
            task = asyncio.create_task(self._heartbeat_loop(correlation_id))
            self._heartbeat_tasks[correlation_id] = task

        logger.info(
            "WebSocket client connected",
            correlation_id=correlation_id,
            total_clients=len(self._connections[correlation_id]),
        )

    async def disconnect(self, correlation_id: str, websocket: WebSocket) -> None:
        """Unregister a WebSocket connection."""
        if correlation_id in self._connections:
            self._connections[correlation_id].discard(websocket)
            ACTIVE_WS_CONNECTIONS.dec()
            if not self._connections[correlation_id]:
                del self._connections[correlation_id]
                # Cancel heartbeat if no more clients
                if correlation_id in self._heartbeat_tasks:
                    self._heartbeat_tasks[correlation_id].cancel()
                    del self._heartbeat_tasks[correlation_id]

        logger.info("WebSocket client disconnected", correlation_id=correlation_id)

    async def broadcast(self, correlation_id: str, event_data: Dict[str, Any]) -> None:
        """
        Broadcast an event to all WebSocket clients subscribed to a correlation_id.
        Also sends a status update. If the event is terminal, sends terminal message and closes.
        """
        if correlation_id not in self._connections:
            return

        session_id = event_data.get("session_id", "")
        event_type = event_data.get("event_type", "")
        status = event_data.get("status", "")
        timestamp = event_data.get("timestamp", format_iso(utc_now()))

        # Build event message
        event_msg = WSEventMessage(
            correlation_id=correlation_id,
            session_id=session_id,
            event={
                "event_type": event_data.get("event_type"),
                "normalized_event_type": event_data.get("normalized_event_type"),
                "status": status,
                "agent_name": event_data.get("agent_name"),
                "tool_name": event_data.get("tool_name"),
                "timestamp": timestamp,
                "summary": event_data.get("summary"),
                "payload": event_data.get("payload"),
            },
        )
        await self._send_to_all(correlation_id, event_msg.model_dump())

        # Build status message
        status_msg = WSStatusMessage(
            correlation_id=correlation_id,
            status=status,
            latest_event_type=event_type,
            timestamp=timestamp,
        )
        await self._send_to_all(correlation_id, status_msg.model_dump())

        # If terminal, send terminal message and close
        if status in ("completed", "failed"):
            await self.send_terminal_and_close(
                correlation_id=correlation_id,
                status=status,
                event_type=event_type,
                timestamp=timestamp,
                error_message=event_data.get("summary") if status == "failed" else None,
            )

    async def send_personal(self, websocket: WebSocket, message: Dict[str, Any]) -> None:
        """Send a message to a single WebSocket client."""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.warning("Failed to send personal WS message", error=str(e))

    async def send_terminal_and_close(
        self,
        correlation_id: str,
        status: str,
        event_type: str,
        timestamp: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Send a terminal (completed/failed) message and close all connections for this correlation_id."""
        if correlation_id not in self._connections:
            return

        if status == "completed":
            msg = WSCompletedMessage(
                correlation_id=correlation_id,
                latest_event_type=event_type,
                timestamp=timestamp,
            )
        else:
            msg = WSFailedMessage(
                correlation_id=correlation_id,
                latest_event_type=event_type,
                timestamp=timestamp,
                error={"message": error_message or "Agent execution failed"},
            )

        await self._send_to_all(correlation_id, msg.model_dump())

        # Close all connections
        clients = list(self._connections.get(correlation_id, set()))
        for ws in clients:
            try:
                await ws.close()
            except Exception:
                pass

        # Cleanup
        self._connections.pop(correlation_id, None)
        if correlation_id in self._heartbeat_tasks:
            self._heartbeat_tasks[correlation_id].cancel()
            del self._heartbeat_tasks[correlation_id]

        logger.info("Terminal message sent and connections closed", correlation_id=correlation_id)

    async def _send_to_all(self, correlation_id: str, message: Dict[str, Any]) -> None:
        """Send a message to all clients subscribed to a correlation_id."""
        if correlation_id not in self._connections:
            return

        disconnected = []
        for ws in self._connections[correlation_id]:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning("WS send failed, marking for disconnect", error=str(e))
                disconnected.append(ws)

        # Clean up disconnected clients
        for ws in disconnected:
            self._connections[correlation_id].discard(ws)

    async def _heartbeat_loop(self, correlation_id: str) -> None:
        """Send periodic heartbeats to keep WebSocket connections alive."""
        try:
            while True:
                await asyncio.sleep(settings.WEBSOCKET_HEARTBEAT_SECONDS)
                if correlation_id not in self._connections:
                    break

                msg = WSHeartbeatMessage(
                    correlation_id=correlation_id,
                    timestamp=format_iso(utc_now()),
                )
                await self._send_to_all(correlation_id, msg.model_dump())
        except asyncio.CancelledError:
            pass

    async def close_all(self) -> None:
        """Close all active WebSocket connections (used during app shutdown)."""
        for correlation_id in list(self._connections.keys()):
            clients = list(self._connections.get(correlation_id, set()))
            for ws in clients:
                try:
                    await ws.close()
                except Exception:
                    pass

        self._connections.clear()

        for task in self._heartbeat_tasks.values():
            task.cancel()
        self._heartbeat_tasks.clear()

        logger.info("All WebSocket connections closed")
