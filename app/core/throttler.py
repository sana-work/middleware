import time
import asyncio
from typing import Dict
from app.core.logging import get_logger

logger = get_logger(__name__)

# In-memory storage for connection attempts
# format: { "user:ip": last_timestamp }
_CONNECT_THROTTLE: Dict[str, float] = {}
THROTTLE_SECONDS = 2.0
CLEANUP_INTERVAL_SECONDS = 600  # 10 minutes
STALE_THRESHOLD_SECONDS = 3600  # 1 hour

def check_throttle(user_id: str, client_ip: str) -> bool:
    """
    Check if a connection attempt should be throttled.
    Returns True if throttled, False if allowed.
    """
    throttle_key = f"{user_id}:{client_ip}"
    now = time.time()
    last_attempt = _CONNECT_THROTTLE.get(throttle_key, 0)
    
    if now - last_attempt < THROTTLE_SECONDS:
        return True
        
    _CONNECT_THROTTLE[throttle_key] = now
    return False

async def throttler_cleanup_loop():
    """Background task to prune stale entries from the throttle map."""
    logger.info("WebSocket throttler cleanup loop started")
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            now = time.time()
            stale_keys = [
                k for k, ts in _CONNECT_THROTTLE.items() 
                if now - ts > STALE_THRESHOLD_SECONDS
            ]
            for k in stale_keys:
                _CONNECT_THROTTLE.pop(k, None)
            
            if stale_keys:
                logger.info("Cleaned up stale throttle entries", count=len(stale_keys))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in throttler cleanup loop", error=str(e))
