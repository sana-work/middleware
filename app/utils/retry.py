import asyncio
from functools import wraps
import structlog

logger = structlog.get_logger(__name__)

def async_retry(attempts: int = 3, backoff_seconds: float = 2.0, exception_types: tuple = (Exception,)):
    """
    Retry an async function multiple times with exponential backoff.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exception_types as e:
                    last_err = e
                    if attempt == attempts:
                        logger.error("Retry limit reached", attempts=attempts, error=str(e), function=func.__name__)
                        break
                    
                    sleep_time = backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "Retry attempt failed, sleeping",
                        attempt=attempt,
                        sleep_time=sleep_time,
                        error=str(e),
                        function=func.__name__
                    )
                    await asyncio.sleep(sleep_time)
            
            raise last_err
        return wrapper
    return decorator
