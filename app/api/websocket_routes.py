from typing import Optional, Dict
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, status

from app.services.metrics_service import ACTIVE_WS_CONNECTIONS, WS_CONNECTIONS_TOTAL, WS_THROTTLED_TOTAL
from app.core.throttler import check_throttle
from app.api.deps import get_current_user
from app.core.lifespan import get_ws_manager
from app.core.logging import get_logger
from app.db.repositories.executions_repository import ExecutionsRepository
from app.models.websocket_messages import WSConnectedMessage
from app.utils.datetime_utils import utc_now, format_iso

logger = get_logger(__name__)

router = APIRouter(tags=["WebSocket"])

@router.websocket("/ws/v1/chat/progress/{correlation_id}")
async def websocket_progress(
    websocket: WebSocket,
    correlation_id: str,
    current_user: str = Depends(get_current_user),
) -> None:
    """
    WebSocket endpoint for live progress updates.
    Gated by verified identity (soeid).
    """
    # 1. Manual Throttle Check
    client_ip = websocket.client.host if websocket.client else "unknown"
    if check_throttle(current_user, client_ip):
        # standard close code for policy violation / rate limit
        WS_THROTTLED_TOTAL.inc()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Rate limit exceeded")
        return

    ws_manager = get_ws_manager()

    # 2. Verify existence AND ownership (404 policy for leaks)
    execution = await ExecutionsRepository.get_execution_by_soeid(correlation_id, current_user)

    if not execution:
        # Standardize on 1008 for auth/authz mismatch on WS
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Execution not found or unauthorized")
        return

    # 3. Accept connection
    await websocket.accept()

    # Send connected message
    connected_msg = WSConnectedMessage(
        correlation_id=correlation_id,
        session_id=execution["session_id"],
        status=execution["status"],
        timestamp=format_iso(utc_now()),
    )
    await websocket.send_json(connected_msg.model_dump())

    # Register with manager
    await ws_manager.connect(correlation_id, websocket)

    try:
        # Keep alive
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected", correlation_id=correlation_id)
    except Exception as e:
        logger.warning("WebSocket error", correlation_id=correlation_id, error=str(e))
    finally:
        await ws_manager.disconnect(correlation_id, websocket)

def cleanup_throttle_cache():
    """Maintenance: cleanup stale throttle entries."""
    now = time.time()
    stale = [uid for uid, ts in _CONNECT_THROTTLE.items() if now - ts > 60]
    for uid in stale:
        _CONNECT_THROTTLE.pop(uid, None)
